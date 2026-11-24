/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Object-list and style-editor operations as undoable store
 * commands: z-order changes, style patches, text edits, visibility and
 * locking. Each operation is one history entry.
 */

import type {DrawingDocument, DrawingStyle} from '@joseph/contracts';
import type {DrawingStore} from './store';

/** Z-order moves offered by the object list. */
export type ZOrderMove = 'forward' | 'backward' | 'front' | 'back';

/** Sequence used to give each multi-document edit its own merge key. */
let editSeq = 0;

/**
 * Computes new z indices for a move. Drawings are renumbered 1..n in their
 * current order first, so ties and gaps never block a move.
 * @param drawings Drawings in z order (ascending).
 * @param id Drawing to move.
 * @param move Move.
 * @return New z index per drawing id (only changed entries).
 */
export function zOrderChanges(
  drawings: readonly DrawingDocument[],
  id: string,
  move: ZOrderMove
): Map<string, number> {
  const order = [...drawings].map(d => d.drawing_id);
  const index = order.indexOf(id);
  const changes = new Map<string, number>();
  if (index < 0) return changes;
  order.splice(index, 1);
  const target =
    move === 'front'
      ? order.length
      : move === 'back'
        ? 0
        : move === 'forward'
          ? Math.min(order.length, index + 1)
          : Math.max(0, index - 1);
  order.splice(target, 0, id);
  order.forEach((drawingId, i) => {
    const doc = drawings.find(d => d.drawing_id === drawingId);
    if (doc && doc.z_index !== i + 1) changes.set(drawingId, i + 1);
  });
  return changes;
}

/**
 * Moves a drawing in z order as one undo entry.
 * @param store Drawing store.
 * @param id Drawing id.
 * @param move Move.
 * @param nowMs Clock.
 * @return True when anything changed.
 */
export function reorderDrawing(
  store: DrawingStore,
  id: string,
  move: ZOrderMove,
  nowMs: number
): boolean {
  const changes = zOrderChanges(store.get().drawings, id, move);
  if (changes.size === 0) return false;
  editSeq += 1;
  const mergeKey = `reorder:${editSeq}`;
  for (const [drawingId, zIndex] of changes) {
    store.update(
      drawingId,
      doc => ({...doc, z_index: zIndex, updated_at_ms: nowMs}),
      'Reorder drawing',
      mergeKey
    );
  }
  return true;
}

/**
 * Applies a style patch (color, width, dash, fill, extension, labels).
 * Locked drawings are not restyled.
 * @param store Drawing store.
 * @param id Drawing id.
 * @param patch Style fields to change.
 * @param nowMs Clock.
 * @param mergeKey Optional key merging a continuous edit (color drag).
 * @return True when applied.
 */
export function restyleDrawing(
  store: DrawingStore,
  id: string,
  patch: Partial<DrawingStyle>,
  nowMs: number,
  mergeKey: string | null = null
): boolean {
  return store.update(
    id,
    doc => ({...doc, style: {...doc.style, ...patch}, updated_at_ms: nowMs}),
    'Change drawing style',
    mergeKey
  );
}

/**
 * Replaces a drawing's text (text notes, labels).
 * @param store Drawing store.
 * @param id Drawing id.
 * @param text New text (trimmed to the schema's 2000 characters).
 * @param nowMs Clock.
 * @return True when applied.
 */
export function retextDrawing(
  store: DrawingStore,
  id: string,
  text: string,
  nowMs: number
): boolean {
  return store.update(
    id,
    doc => ({...doc, text: text.slice(0, 2000), updated_at_ms: nowMs}),
    'Edit drawing text'
  );
}

/**
 * Changes tool inputs kept in `props` (for example anchored VWAP bands).
 * Locked drawings are not changed.
 * @param store Drawing store.
 * @param id Drawing id.
 * @param patch Props to set.
 * @param nowMs Clock.
 * @return True when applied.
 */
export function setDrawingInputs(
  store: DrawingStore,
  id: string,
  patch: Readonly<Record<string, string | boolean>>,
  nowMs: number
): boolean {
  return store.update(
    id,
    doc => ({
      ...doc,
      props: {...(doc.props as Record<string, unknown> | undefined), ...patch},
      updated_at_ms: nowMs,
    }),
    'Change drawing inputs'
  );
}

/**
 * Shows/hides or locks/unlocks one drawing (allowed on locked drawings).
 * @param store Drawing store.
 * @param id Drawing id.
 * @param flags Flags to set.
 * @param nowMs Clock.
 * @return True when applied.
 */
export function setDrawingFlags(
  store: DrawingStore,
  id: string,
  flags: {visible?: boolean; locked?: boolean},
  nowMs: number
): boolean {
  return store.update(
    id,
    doc => ({...doc, ...flags, updated_at_ms: nowMs}),
    flags.locked !== undefined
      ? flags.locked
        ? 'Lock drawing'
        : 'Unlock drawing'
      : flags.visible
        ? 'Show drawing'
        : 'Hide drawing'
  );
}
