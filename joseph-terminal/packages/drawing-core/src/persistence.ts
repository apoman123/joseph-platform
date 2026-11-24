/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Drawing persistence per (workspace, exact instrument), plus
 * the drawing part of workspace export/import.
 *
 * Drawings are keyed by canonical instrument id, so `binance:perp:BTCUSDT`
 * and `hyperliquid:perp:BTC` never share drawings. Unreadable payloads are
 * saved back verbatim.
 *
 * Export lists every instrument of the workspace that has drawings
 * (contract `WorkspaceExportDrawings`). Import reads untrusted collections
 * defensively: each document goes through the schema migrations, must
 * belong to its collection's instrument and stay within the contract limits
 * (64 anchors, 2000 characters of text, a valid style); documents saved by
 * a newer drawing schema are kept as opaque payloads (ADR 0007), anything
 * else that cannot be read is dropped and counted. Documents of tools this
 * build does not know are ordinary documents and are kept.
 */

import {
  TIMEFRAMES,
  isInstrumentId,
  type DrawingDocument,
  type InstrumentId,
  type WorkspaceExportDrawings,
} from '@joseph/contracts';
import {DRAWING_SCHEMA_VERSION} from './document';
import {MigrationRegistry} from './migrations';
import type {DrawingState, DrawingStore} from './store';
import {MAX_ANCHORS, type ToolRegistry} from './tool';

/** Storage port (structurally compatible with feature-registry storage). */
export interface DrawingStorage {
  /**
   * Reads a value.
   * @param key Key.
   * @return Value or null.
   */
  getItem(key: string): string | null;
  /**
   * Writes a value.
   * @param key Key.
   * @param value Value.
   */
  setItem(key: string, value: string): void;
  /**
   * Lists keys with a prefix (needed to export every instrument).
   * @param prefix Key prefix.
   * @return Matching keys.
   */
  keys?(prefix: string): string[];
}

/** Most instruments with drawings in one export (contract limit). */
export const MAX_EXPORT_INSTRUMENTS = 500;

/** Most drawings per instrument in one export (contract limit). */
export const MAX_EXPORT_DRAWINGS = 2000;

/** Most opaque payloads per instrument in one export (contract limit). */
export const MAX_EXPORT_OPAQUE = 500;

/** Longest drawing text (contract `DrawingDocument.text`). */
const MAX_TEXT = 2000;

/** Longest accepted drawing id. */
const MAX_ID = 128;

/** Drawing colors (contract `DrawingStyle.color`). */
const COLOR_PATTERN = /^#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?$/;

/** Line styles (contract `DrawingStyle.line_style`). */
const LINE_STYLES: ReadonlySet<string> = new Set(['solid', 'dashed', 'dotted']);

/** Timeframes a drawing may be scoped to. */
const TIMEFRAME_SET: ReadonlySet<string> = new Set(TIMEFRAMES);

/** Drawings of a workspace prepared for export. */
export interface DrawingExport {
  /** Collections of instruments with at least one drawing or payload. */
  collections: WorkspaceExportDrawings[];
  /** Readable drawings exported. */
  drawings: number;
  /** Payloads left out because they exceed the export limits. */
  omitted: number;
}

/** Drawings of one instrument read from an import file. */
export interface ImportedDrawings {
  /** Exact instrument. */
  instrumentId: InstrumentId;
  /** Drawings and preserved opaque payloads. */
  state: DrawingState;
}

/** Outcome of reading the drawings of an import file. */
export interface DrawingImport {
  /** Collections to restore (one per instrument). */
  collections: ImportedDrawings[];
  /** Readable drawings kept. */
  drawings: number;
  /** Opaque payloads kept verbatim (newer drawing schema). */
  opaque: number;
  /** Entries dropped because they could not be read safely. */
  dropped: number;
}

/** Options for reading imported drawings. */
export interface DrawingImportOptions {
  /** Schema migrations (default: the built-in registry). */
  migrations?: MigrationRegistry;
  /** Installed tools (tool-version migrations). */
  tools?: ToolRegistry;
}

/**
 * Applies tool-level migrations when the installed tool is newer.
 * @param doc Document.
 * @param tools Tool registry, if any.
 * @return Possibly upgraded document.
 */
export function upgradeToolVersion(
  doc: DrawingDocument,
  tools: ToolRegistry | undefined
): DrawingDocument {
  const tool = tools?.get(doc.tool_id);
  if (!tool || doc.tool_version >= tool.manifest.version || !tool.migrate) {
    return doc;
  }
  try {
    return tool.migrate(doc);
  } catch {
    return doc;
  }
}

/**
 * Whether a value is a plain JSON object.
 * @param value Candidate.
 * @return True for non-null, non-array objects.
 */
function isPlainObject(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

/**
 * Contract limits a readable document must meet to be imported.
 * @param doc Migrated document.
 * @param instrumentId Instrument of its collection.
 * @return True when the document is safe to store and render.
 */
export function isImportableDrawing(
  doc: DrawingDocument,
  instrumentId: InstrumentId
): boolean {
  const style = doc.style as unknown as Record<string, unknown>;
  const timeframe = doc.timeframe;
  return (
    doc.instrument_id === instrumentId &&
    doc.drawing_id.length > 0 &&
    doc.drawing_id.length <= MAX_ID &&
    doc.tool_id.length > 0 &&
    doc.tool_id.length <= MAX_ID &&
    Number.isInteger(doc.tool_version) &&
    doc.tool_version >= 1 &&
    doc.anchors.length <= MAX_ANCHORS &&
    doc.anchors.every(
      anchor =>
        Number.isFinite(anchor.time_ms) &&
        anchor.time_ms >= 0 &&
        Number.isFinite(Number(anchor.price))
    ) &&
    typeof style.color === 'string' &&
    COLOR_PATTERN.test(style.color) &&
    Number.isInteger(style.line_width) &&
    Number(style.line_width) >= 1 &&
    Number(style.line_width) <= 8 &&
    typeof style.line_style === 'string' &&
    LINE_STYLES.has(style.line_style) &&
    (timeframe === undefined ||
      timeframe === null ||
      TIMEFRAME_SET.has(timeframe)) &&
    (doc.text === undefined ||
      doc.text === null ||
      (typeof doc.text === 'string' && doc.text.length <= MAX_TEXT)) &&
    (doc.props === undefined || isPlainObject(doc.props)) &&
    Number.isFinite(doc.z_index)
  );
}

/**
 * Whether a raw payload was saved by a newer drawing schema (kept opaque).
 * @param raw Raw payload.
 * @return True for an object with a schema version above the current one.
 */
function isNewerSchema(raw: unknown): raw is Record<string, unknown> {
  return (
    isPlainObject(raw) &&
    typeof raw.schema_version === 'number' &&
    raw.schema_version > DRAWING_SCHEMA_VERSION
  );
}

/**
 * Reads the drawings of an import file (untrusted). One collection per
 * instrument is kept (later duplicates are dropped); every entry that can't
 * be read safely is dropped and counted.
 * @param raw The file's `drawings` array.
 * @param options Migrations and installed tools.
 * @return Collections to restore and counts.
 */
export function readImportedDrawings(
  raw: readonly unknown[],
  options: DrawingImportOptions = {}
): DrawingImport {
  const migrations = options.migrations ?? new MigrationRegistry();
  const result: DrawingImport = {
    collections: [],
    drawings: 0,
    opaque: 0,
    dropped: 0,
  };
  const seen = new Set<string>();
  const countOf = (value: unknown) => (Array.isArray(value) ? value.length : 0);
  raw.forEach((entry, index) => {
    const instrumentId = isPlainObject(entry) ? entry.instrument_id : null;
    if (
      index >= MAX_EXPORT_INSTRUMENTS ||
      !isPlainObject(entry) ||
      typeof instrumentId !== 'string' ||
      !isInstrumentId(instrumentId) ||
      seen.has(instrumentId)
    ) {
      const size = isPlainObject(entry)
        ? countOf(entry.drawings) + countOf(entry.opaque)
        : 1;
      result.dropped += Math.max(1, size);
      return;
    }
    seen.add(instrumentId);
    const drawings: DrawingDocument[] = [];
    const opaque: unknown[] = [];
    const ids = new Set<string>();
    const items = Array.isArray(entry.drawings) ? entry.drawings : [];
    items.forEach((item: unknown, position: number) => {
      if (position >= MAX_EXPORT_DRAWINGS) {
        result.dropped += 1;
        return;
      }
      if (isNewerSchema(item)) {
        if (opaque.length < MAX_EXPORT_OPAQUE) opaque.push(item);
        else result.dropped += 1;
        return;
      }
      const outcome = migrations.read(item);
      if (
        outcome.kind !== 'document' ||
        ids.has(outcome.doc.drawing_id) ||
        !isImportableDrawing(outcome.doc, instrumentId)
      ) {
        result.dropped += 1;
        return;
      }
      ids.add(outcome.doc.drawing_id);
      drawings.push(upgradeToolVersion(outcome.doc, options.tools));
    });
    const payloads = Array.isArray(entry.opaque) ? entry.opaque : [];
    for (const payload of payloads as unknown[]) {
      if (isPlainObject(payload) && opaque.length < MAX_EXPORT_OPAQUE) {
        opaque.push(payload);
      } else {
        result.dropped += 1;
      }
    }
    result.drawings += drawings.length;
    result.opaque += opaque.length;
    result.collections.push({instrumentId, state: {drawings, opaque}});
  });
  return result;
}

/** Repository of drawing collections. */
export class DrawingRepository {
  /**
   * Creates the repository.
   * @param storage Storage port.
   * @param workspaceId Workspace scope.
   * @param tools Tool registry (for tool-version migrations).
   * @param migrations Schema migrations.
   */
  constructor(
    private readonly storage: DrawingStorage,
    private readonly workspaceId: string,
    private readonly tools?: ToolRegistry,
    private readonly migrations = new MigrationRegistry()
  ) {}

  /**
   * Key prefix of this workspace's collections.
   * @return Prefix ending with a dot.
   */
  prefix(): string {
    return `joseph.drawings.v1.${this.workspaceId}.`;
  }

  /**
   * Storage key for an instrument.
   * @param instrumentId Instrument.
   * @return Key.
   */
  key(instrumentId: InstrumentId): string {
    return `${this.prefix()}${instrumentId}`;
  }

  /**
   * Instruments of this workspace that have a stored collection.
   * @return Canonical instrument ids, sorted (empty without key listing).
   */
  instruments(): InstrumentId[] {
    const prefix = this.prefix();
    const keys = this.storage.keys?.(prefix) ?? [];
    return keys
      .filter(key => key.startsWith(prefix))
      .map(key => key.slice(prefix.length))
      .filter(id => isInstrumentId(id))
      .sort();
  }

  /**
   * Loads drawings for an instrument, migrating as needed.
   * @param instrumentId Instrument.
   * @return State with readable drawings and preserved opaque payloads.
   */
  load(instrumentId: InstrumentId): DrawingState {
    const raw = this.storage.getItem(this.key(instrumentId));
    if (!raw) return {drawings: [], opaque: []};
    let parsed: unknown;
    try {
      parsed = JSON.parse(raw);
    } catch {
      return {drawings: [], opaque: [raw]};
    }
    const items =
      parsed &&
      typeof parsed === 'object' &&
      Array.isArray((parsed as {drawings?: unknown}).drawings)
        ? (parsed as {drawings: unknown[]}).drawings
        : [];
    const drawings: DrawingDocument[] = [];
    const opaque: unknown[] = [];
    for (const item of items) {
      const outcome = this.migrations.read(item);
      if (outcome.kind === 'opaque') {
        opaque.push(outcome.raw);
        continue;
      }
      drawings.push(upgradeToolVersion(outcome.doc, this.tools));
    }
    return {drawings, opaque};
  }

  /**
   * Saves drawings (and opaque payloads) for an instrument.
   * @param instrumentId Instrument.
   * @param state Store state.
   */
  save(instrumentId: InstrumentId, state: DrawingState): void {
    const collection = {
      schema_version: 1,
      workspace_id: this.workspaceId,
      instrument_id: instrumentId,
      drawings: [...state.drawings, ...state.opaque],
    };
    this.storage.setItem(this.key(instrumentId), JSON.stringify(collection));
  }

  /**
   * Collects every instrument's drawings for a workspace export. Opaque
   * payloads that are JSON objects travel verbatim; unparseable text stays
   * in this browser only.
   * @return Collections within the contract limits and counts.
   */
  exportAll(): DrawingExport {
    const result: DrawingExport = {collections: [], drawings: 0, omitted: 0};
    for (const instrumentId of this.instruments()) {
      const state = this.load(instrumentId);
      const objects = state.opaque.filter(isPlainObject);
      if (state.drawings.length === 0 && objects.length === 0) continue;
      if (result.collections.length >= MAX_EXPORT_INSTRUMENTS) {
        result.omitted += state.drawings.length + objects.length;
        continue;
      }
      const drawings = state.drawings.slice(0, MAX_EXPORT_DRAWINGS);
      const opaque = objects.slice(0, MAX_EXPORT_OPAQUE);
      result.omitted +=
        state.drawings.length -
        drawings.length +
        objects.length -
        opaque.length;
      result.drawings += drawings.length;
      result.collections.push({
        instrument_id: instrumentId,
        drawings,
        ...(opaque.length > 0 ? {opaque} : {}),
      });
    }
    return result;
  }

  /**
   * Restores imported collections: each one replaces the stored drawings
   * of its instrument; other instruments keep theirs.
   * @param collections Collections read by `readImportedDrawings`.
   */
  restore(collections: readonly ImportedDrawings[]): void {
    for (const {instrumentId, state} of collections) {
      this.save(instrumentId, state);
    }
  }
}

/**
 * Saves the store after changes, debounced; flushes on dispose.
 * @param store Drawing store.
 * @param repository Repository.
 * @param instrumentId Instrument whose drawings the store holds.
 * @param delayMs Debounce delay.
 * @return Disposer that flushes pending saves.
 */
export function bindAutosave(
  store: DrawingStore,
  repository: DrawingRepository,
  instrumentId: InstrumentId,
  delayMs = 300
): () => void {
  let timer: ReturnType<typeof setTimeout> | null = null;
  const flush = () => {
    if (timer) clearTimeout(timer);
    timer = null;
    repository.save(instrumentId, store.get());
  };
  const unsubscribe = store.subscribe(() => {
    if (timer) clearTimeout(timer);
    timer = setTimeout(flush, delayMs);
  });
  return () => {
    unsubscribe();
    if (timer) flush();
  };
}
