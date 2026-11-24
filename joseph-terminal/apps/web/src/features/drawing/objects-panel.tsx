/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Object list: every drawing on the active chart's
 * instrument, top of the z order first, with select, show/hide, lock,
 * reorder and delete. All edits are undoable drawing commands.
 */

'use client';
import type {DrawingDocument} from '@joseph/contracts';
import {
  reorderDrawing,
  setDrawingFlags,
  type DrawingStore,
} from '@joseph/drawing-core';
import {
  ChevronDown,
  ChevronUp,
  Eye,
  EyeOff,
  Lock,
  LockOpen,
  Trash2,
} from 'lucide-react';
import type {ReactElement} from 'react';
import type {TerminalRuntime} from '../../composition/runtime';
import {Icon} from '../../shell/icons';
import {useStore} from '../../shell/react-bindings';

/** Props of the object list. */
export interface ObjectsPanelProps {
  /** Runtime (shell-owned feature). */
  runtime: TerminalRuntime;
}

/**
 * Describes a drawing for the list. Engine-backed drawings (anchored VWAP,
 * the volume-weighted average price, and the regression channel) summarize
 * their engine values or say why none are drawn.
 * @param runtime Runtime.
 * @param doc Drawing.
 * @param chartId Chart hosting the drawing (defaults to the active chart).
 * @return Tool name and detail; the detail is the drawing's own text when
 *     set. A drawing of an unregistered tool is named unsupported.
 */
export function describeDrawing(
  runtime: TerminalRuntime,
  doc: DrawingDocument,
  chartId: string = runtime.selection.get().chartId
): {name: string; detail: string} {
  const tool = runtime.tools.get(doc.tool_id);
  if (!tool) {
    return {
      name: `Unsupported (${doc.tool_id})`,
      detail: 'Kept until the tool returns',
    };
  }
  const series =
    runtime.drawing.series.get(chartId)?.get(doc.drawing_id) ?? null;
  const detail = doc.text?.trim()
    ? doc.text.trim()
    : (tool.describe?.(doc, {series, tickSize: null}) ??
      `${doc.anchors.length} anchors`);
  return {name: tool.manifest.displayName, detail};
}

/**
 * Renders the object list of the active chart's instrument.
 * @param props Runtime.
 * @return Panel.
 */
export function ObjectsPanel({runtime}: ObjectsPanelProps): ReactElement {
  const selection = useStore(runtime.selection);
  const selected = useStore(runtime.drawing.selection);
  // Subscribed only to re-render when drawings change; the drawings
  // themselves are read from the store below.
  useStore(runtime.drawing.revision);
  const store: DrawingStore | undefined = runtime.drawing.stores.get(
    selection.chartId
  );
  // The store keeps the z order bottom first; the list shows the top first.
  const drawings = [...(store?.get().drawings ?? [])]
    .filter(doc => doc.instrument_id === selection.instrumentId)
    .reverse();
  const bump = () => void runtime.drawing.revision.update(n => n + 1);
  const now = () => Date.now();
  const select = (doc: DrawingDocument) => {
    runtime.drawing.controllers.get(selection.chartId)?.select(doc.drawing_id);
    runtime.drawing.selection.set({
      chartId: selection.chartId,
      drawingId: doc.drawing_id,
    });
  };
  return (
    <section className="j-panel" aria-label="Drawing objects">
      <header className="j-panel__header">
        <h2>Objects</h2>
        <span className="j-muted objects__count">{drawings.length}</span>
      </header>
      <div className="j-panel__body">
        {!store || drawings.length === 0 ? (
          <div className="j-empty">
            <strong>No drawings on this chart</strong>
            <p>
              Drawings you add to {selection.instrumentId} appear here, top of
              the stack first.
            </p>
          </div>
        ) : (
          <ul className="objects" aria-label="Drawings, top first">
            {drawings.map((doc, index) => {
              const {name, detail} = describeDrawing(
                runtime,
                doc,
                selection.chartId
              );
              const isSelected = selected?.drawingId === doc.drawing_id;
              const tool = runtime.tools.get(doc.tool_id);
              return (
                <li
                  key={doc.drawing_id}
                  className="objects__row"
                  aria-current={isSelected ? 'true' : undefined}
                  data-testid={`object-${doc.tool_id}`}
                >
                  <button
                    type="button"
                    className="objects__select"
                    onClick={() => void select(doc)}
                  >
                    <span
                      className="objects__swatch"
                      style={{background: doc.style.color}}
                      aria-hidden="true"
                    />
                    <Icon name={tool?.manifest.icon} size={14} />
                    <span className="objects__name">{name}</span>
                    <span
                      className="objects__detail j-muted"
                      title={detail}
                      data-testid="object-detail"
                    >
                      {detail}
                    </span>
                  </button>
                  <button
                    type="button"
                    className="j-btn j-btn--ghost j-btn--icon j-btn--sm"
                    aria-label={`Move ${name} up`}
                    disabled={index === 0}
                    onClick={() => {
                      reorderDrawing(store, doc.drawing_id, 'forward', now());
                      bump();
                    }}
                  >
                    <ChevronUp size={14} aria-hidden="true" />
                  </button>
                  <button
                    type="button"
                    className="j-btn j-btn--ghost j-btn--icon j-btn--sm"
                    aria-label={`Move ${name} down`}
                    disabled={index === drawings.length - 1}
                    onClick={() => {
                      reorderDrawing(store, doc.drawing_id, 'backward', now());
                      bump();
                    }}
                  >
                    <ChevronDown size={14} aria-hidden="true" />
                  </button>
                  <button
                    type="button"
                    className="j-btn j-btn--ghost j-btn--icon j-btn--sm"
                    aria-label={doc.visible ? `Hide ${name}` : `Show ${name}`}
                    aria-pressed={!doc.visible}
                    onClick={() => {
                      setDrawingFlags(
                        store,
                        doc.drawing_id,
                        {visible: !doc.visible},
                        now()
                      );
                      bump();
                    }}
                  >
                    {doc.visible ? (
                      <Eye size={14} aria-hidden="true" />
                    ) : (
                      <EyeOff size={14} aria-hidden="true" />
                    )}
                  </button>
                  <button
                    type="button"
                    className="j-btn j-btn--ghost j-btn--icon j-btn--sm"
                    aria-label={doc.locked ? `Unlock ${name}` : `Lock ${name}`}
                    aria-pressed={doc.locked}
                    onClick={() => {
                      setDrawingFlags(
                        store,
                        doc.drawing_id,
                        {locked: !doc.locked},
                        now()
                      );
                      bump();
                    }}
                  >
                    {doc.locked ? (
                      <Lock size={14} aria-hidden="true" />
                    ) : (
                      <LockOpen size={14} aria-hidden="true" />
                    )}
                  </button>
                  <button
                    type="button"
                    className="j-btn j-btn--ghost j-btn--icon j-btn--sm"
                    aria-label={`Delete ${name}`}
                    disabled={doc.locked}
                    onClick={() => {
                      if (store.remove(doc.drawing_id) && isSelected) {
                        runtime.drawing.selection.set(null);
                      }
                      bump();
                    }}
                  >
                    <Trash2 size={14} aria-hidden="true" />
                  </button>
                </li>
              );
            })}
          </ul>
        )}
        <p className="objects__hint j-muted">
          Changes here can be undone with Ctrl + Z.
        </p>
      </div>
    </section>
  );
}
