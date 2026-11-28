/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Workspace persistence: versioned storage with migrations,
 * JSON import/export, share links and debounced autosave.
 *
 * Workspaces are stored under `joseph.workspace.v1.<workspace id>`. The
 * stored and shared `WorkspaceDocument` is at schema v2 (charts list their
 * studies); a schema v1 document, from storage, a link or a file, is
 * migrated on read (`migrateWorkspaceV1`: indicator layers and attached
 * scripts become studies). Export files carry their own format version,
 * migrated step by step on import:
 *
 * - format 0: the single chart (`exchange`/`symbol`/`timeframe`) of the V4
 *   starter, the terminal that predates schema v1, migrated to a current
 *   document (`migrateWorkspaceV0`);
 * - format 1: a plain `WorkspaceDocument` (exports before drawings were
 *   included); it imports without touching drawings;
 * - format 2: the `WorkspaceExport` envelope (`format:
 *   'joseph.workspace-export'`) holding the document plus the drawings
 *   of its instruments (`WorkspaceExportDrawings`). The drawing documents
 *   themselves are validated and migrated by `@joseph/drawing-core`.
 *
 * Files from a newer format are refused with an actionable message rather
 * than half-imported. Share links carry the workspace document only: no
 * drawings and no panel state.
 */

import {
  isInstrumentId,
  type WorkspaceDocument,
  type WorkspaceExport,
  type WorkspaceExportDrawings,
} from '@joseph/contracts';
import {migrateWorkspaceV1} from './study-migration';
import {LAYOUT_PANES, createChartState} from './workspace';

/**
 * Storage port: a subset of `KeyValueStorage` in `@joseph/feature-registry`,
 * which satisfies it structurally.
 */
export interface WorkspaceStorage {
  /**
   * Reads a stored string.
   * @param key Storage key.
   * @return Stored text, or null when the key is absent.
   */
  getItem(key: string): string | null;
  /**
   * Stores a string, replacing any previous value.
   * @param key Storage key.
   * @param value Text to store.
   */
  setItem(key: string, value: string): void;
}

/**
 * Maximum size of a workspace document in share links, measured
 * as JSON text length (UTF-16 code units, not bytes).
 */
export const MAX_WORKSPACE_BYTES = 64 * 1024;

/**
 * Maximum size of an imported workspace file (layout plus drawings), measured
 * as text length like `MAX_WORKSPACE_BYTES`.
 */
export const MAX_WORKSPACE_FILE_BYTES = 2 * 1024 * 1024;

/** `format` marker of workspace export files. */
export const WORKSPACE_EXPORT_FORMAT = 'joseph.workspace-export';

/** Current export file format version (`WorkspaceExport.schema_version`). */
export const WORKSPACE_EXPORT_VERSION = 2;

/** Most instruments with drawings in one export file (contract limit). */
export const MAX_EXPORT_COLLECTIONS = 500;

/**
 * Outcome of parsing a workspace from untrusted input: the document and
 * whether it was migrated from the V4 starter shape, or an error message
 * for the user.
 */
export type WorkspaceParseResult =
  | {ok: true; workspace: WorkspaceDocument; migrated: boolean}
  | {ok: false; error: string};

/** Outcome of parsing an imported workspace file. */
export type WorkspaceFileParseResult =
  | {
      ok: true;
      /** Workspace document (schema v1). */
      workspace: WorkspaceDocument;
      /** Whether an older format was upgraded. */
      migrated: boolean;
      /** Export format the file was written in (0, 1 or 2). */
      formatVersion: 0 | 1 | 2;
      /**
       * Raw drawing collections (validated by `@joseph/drawing-core`), or
       * null when the file format carries no drawings (0 and 1).
       */
      drawings: readonly unknown[] | null;
    }
  | {ok: false; error: string};

/** V4 starter shape: one chart described by exchange/symbol/timeframe. */
interface LegacyV0Workspace {
  /** Venue id; `mock` when missing. */
  exchange?: string;
  /** Venue symbol in any case; `BTCUSD` when missing. */
  symbol?: string;
  /** Timeframe; `5m` when missing. */
  timeframe?: string;
}

/**
 * Migrates the V4 starter's single-chart state to schema v2. The instrument
 * becomes a perpetual (`<exchange>:perp:<SYMBOL>`) and the timeframe is
 * copied unchecked; callers validate the result with `validateWorkspace`.
 * @param raw Legacy object.
 * @param nowMs Migration time (UTC epoch ms), stored as `updated_at_ms`.
 * @return v2 workspace candidate, not yet validated.
 */
export function migrateWorkspaceV0(
  raw: LegacyV0Workspace,
  nowMs: number
): unknown {
  const venue = raw.exchange ?? 'mock';
  const symbol = (raw.symbol ?? 'BTCUSD').toUpperCase();
  const instrumentId = `${venue}:perp:${symbol}`;
  return {
    schema_version: 2,
    workspace_id: 'default',
    name: 'Imported workspace',
    layout: '1',
    sync: {
      crosshair: true,
      instrument: false,
      timeframe: false,
      viewport: false,
    },
    active_chart_id: 'chart-1',
    charts: [
      createChartState(
        'chart-1',
        instrumentId,
        // Unchecked on purpose: the result is only a candidate (`unknown`).
        (raw.timeframe ?? '5m') as never
      ),
    ],
    favorites: {timeframes: ['5m', '15m', '1h', '4h', '1d'], instruments: []},
    panels: {},
    updated_at_ms: nowMs,
  };
}

/**
 * Checks the structure of a v2 workspace candidate: schema version, id,
 * layout, a nonempty chart list with ids, valid instrument ids and study
 * lists, and the sync options. Other fields are not checked.
 * @param value Candidate, usually parsed JSON.
 * @return Error message for the user, or null when the checks pass.
 */
export function validateWorkspace(value: unknown): string | null {
  if (!value || typeof value !== 'object') return 'Workspace must be an object';
  const doc = value as Partial<WorkspaceDocument>;
  if (doc.schema_version !== 2) return 'Unsupported workspace version';
  if (typeof doc.workspace_id !== 'string' || !doc.workspace_id) {
    return 'Missing workspace id';
  }
  if (!doc.layout || !(doc.layout in LAYOUT_PANES)) return 'Unknown layout';
  if (!Array.isArray(doc.charts) || doc.charts.length < 1) {
    return 'A workspace needs at least one chart';
  }
  for (const chart of doc.charts) {
    if (!chart || typeof chart.chart_id !== 'string') {
      return 'Chart is missing an id';
    }
    if (!isInstrumentId(String(chart.instrument_id))) {
      return `Chart ${chart.chart_id} has an invalid instrument id`;
    }
    if (!Array.isArray(chart.studies)) {
      return `Chart ${chart.chart_id} has no study list`;
    }
  }
  if (!doc.sync || typeof doc.sync.crosshair !== 'boolean') {
    return 'Missing sync options';
  }
  return null;
}

/**
 * Validates a parsed workspace value, migrating the V4 starter shape (an
 * object without `schema_version`) and schema v1 documents.
 * @param value Parsed JSON.
 * @param nowMs Current time (UTC epoch ms) for migrations.
 * @param maxBytes Optional text limit in UTF-16 units; defaults to the share
 *     link limit. Local storage uses its own browser quota.
 * @return Parse result with an actionable error.
 */
function readWorkspaceValue(
  value: unknown,
  nowMs: number,
  maxBytes = MAX_WORKSPACE_BYTES
): WorkspaceParseResult {
  let raw = value;
  let migrated = false;
  if (raw && typeof raw === 'object' && !('schema_version' in raw)) {
    raw = migrateWorkspaceV0(raw, nowMs);
    migrated = true;
  } else if (
    raw &&
    typeof raw === 'object' &&
    (raw as {schema_version?: unknown}).schema_version === 1
  ) {
    raw = migrateWorkspaceV1(raw);
    migrated = true;
  }
  const error = validateWorkspace(raw);
  if (error) return {ok: false, error};
  const workspace = raw as WorkspaceDocument;
  if (JSON.stringify(workspace).length > maxBytes) {
    return {ok: false, error: 'The workspace layout is larger than 64 KB'};
  }
  // A dangling active chart id is repaired rather than rejected: the rest of
  // the layout is still usable.
  if (!workspace.charts.some(c => c.chart_id === workspace.active_chart_id)) {
    workspace.active_chart_id = workspace.charts[0]?.chart_id ?? 'chart-1';
  }
  return {ok: true, workspace, migrated};
}

/**
 * Parses size-bounded workspace JSON for share links and legacy callers,
 * migrating legacy versions. Export files with drawings go through
 * `parseWorkspaceFile`.
 * @param text JSON text, at most `MAX_WORKSPACE_BYTES` long.
 * @param nowMs Current time (UTC epoch ms) for migrations.
 * @return Parse result with an actionable error.
 */
export function parseWorkspace(
  text: string,
  nowMs: number
): WorkspaceParseResult {
  if (text.length > MAX_WORKSPACE_BYTES) {
    return {ok: false, error: 'The workspace file is larger than 64 KB'};
  }
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch {
    return {ok: false, error: 'The file is not valid JSON'};
  }
  return readWorkspaceValue(raw, nowMs);
}

/**
 * Parses an imported workspace file of any format (0 starter, 1 plain
 * document, 2 export with drawings), migrating older formats. A file without
 * a `format` field is format 0 or 1.
 * @param text File text, at most `MAX_WORKSPACE_FILE_BYTES` long.
 * @param nowMs Current time (UTC epoch ms) for migrations.
 * @return Workspace and raw drawing collections, or an actionable error.
 */
export function parseWorkspaceFile(
  text: string,
  nowMs: number
): WorkspaceFileParseResult {
  if (text.length > MAX_WORKSPACE_FILE_BYTES) {
    return {ok: false, error: 'The workspace file is larger than 2 MB'};
  }
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch {
    return {ok: false, error: 'The file is not valid JSON'};
  }
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
    return {ok: false, error: 'Workspace must be an object'};
  }
  if (!('format' in raw)) {
    const plain = readWorkspaceValue(raw, nowMs, MAX_WORKSPACE_FILE_BYTES);
    if (!plain.ok) return plain;
    return {
      ...plain,
      formatVersion: plain.migrated ? 0 : 1,
      drawings: null,
    };
  }
  const file = raw as Partial<Record<keyof WorkspaceExport, unknown>>;
  if (file.format !== WORKSPACE_EXPORT_FORMAT) {
    return {ok: false, error: 'This is not a Joseph workspace file'};
  }
  const version = file.schema_version;
  if (typeof version === 'number' && version > WORKSPACE_EXPORT_VERSION) {
    return {
      ok: false,
      error: `This file was exported by a newer Joseph version (export format ${version}); update the terminal to import it`,
    };
  }
  if (version !== WORKSPACE_EXPORT_VERSION) {
    return {ok: false, error: 'Unsupported workspace export version'};
  }
  const workspace = readWorkspaceValue(
    file.workspace,
    nowMs,
    MAX_WORKSPACE_FILE_BYTES
  );
  if (!workspace.ok) return workspace;
  const drawings = file.drawings ?? [];
  if (!Array.isArray(drawings)) {
    return {ok: false, error: 'The drawings in the file are malformed'};
  }
  if (drawings.length > MAX_EXPORT_COLLECTIONS) {
    return {
      ok: false,
      error: `The file has drawings on more than ${MAX_EXPORT_COLLECTIONS} instruments`,
    };
  }
  return {
    ok: true,
    workspace: workspace.workspace,
    migrated: workspace.migrated,
    formatVersion: 2,
    drawings,
  };
}

/**
 * Serializes a workspace document alone (export format 1, no drawings).
 * @param workspace Workspace to export.
 * @return JSON indented by two spaces.
 */
export function exportWorkspace(workspace: WorkspaceDocument): string {
  return JSON.stringify(workspace, null, 2);
}

/**
 * Builds a workspace export file (format 2): the document plus the drawings
 * of its instruments.
 * @param workspace Workspace to export.
 * @param drawings Drawing collections (from `DrawingRepository.exportAll`);
 *     only the first `MAX_EXPORT_COLLECTIONS` are kept.
 * @param nowMs Export time (UTC epoch ms); rounded and clamped at 0.
 * @return The export envelope.
 */
export function createWorkspaceExport(
  workspace: WorkspaceDocument,
  drawings: readonly WorkspaceExportDrawings[],
  nowMs: number
): WorkspaceExport {
  return {
    format: WORKSPACE_EXPORT_FORMAT,
    schema_version: WORKSPACE_EXPORT_VERSION,
    exported_at_ms: Math.max(0, Math.round(nowMs)),
    workspace,
    drawings: drawings.slice(0, MAX_EXPORT_COLLECTIONS),
  };
}

/**
 * Serializes a workspace export file (format 2); see `createWorkspaceExport`.
 * @param workspace Workspace to export.
 * @param drawings Drawing collections of its instruments.
 * @param nowMs Export time (UTC epoch ms).
 * @return JSON indented by two spaces.
 */
export function exportWorkspaceFile(
  workspace: WorkspaceDocument,
  drawings: readonly WorkspaceExportDrawings[],
  nowMs: number
): string {
  return JSON.stringify(
    createWorkspaceExport(workspace, drawings, nowMs),
    null,
    2
  );
}

/**
 * Encodes text as UTF-8 and then as base64url (RFC 4648 section 5), which
 * needs no escaping in a URL.
 * @param text Text to encode.
 * @return base64url string without `=` padding.
 */
function toBase64Url(text: string): string {
  // `btoa` takes one character per byte, so the UTF-8 bytes are spelled
  // out as Latin-1 characters first.
  const bytes = new TextEncoder().encode(text);
  let binary = '';
  for (const byte of bytes) {
    binary += String.fromCharCode(byte);
  }
  return btoa(binary)
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=+$/, '');
}

/**
 * Decodes unpadded base64url and reads the bytes as UTF-8.
 * @param value base64url string.
 * @return Decoded text; invalid UTF-8 becomes U+FFFD.
 * @throws `Error` when the value has characters outside the base64url
 *     alphabet; `atob` throws a `DOMException` when its length is not a
 *     valid base64 length.
 */
function fromBase64Url(value: string): string {
  if (!/^[A-Za-z0-9_-]*$/.test(value)) throw new Error('invalid base64url');
  const padded =
    value.replace(/-/g, '+').replace(/_/g, '/') +
    '==='.slice((value.length + 3) % 4);
  const binary = atob(padded);
  const bytes = Uint8Array.from(binary, char => char.charCodeAt(0));
  return new TextDecoder().decode(bytes);
}

/**
 * Encodes a workspace into a share token. Panel state (per-feature UI state,
 * which may be private) is replaced by an empty object.
 * @param workspace Workspace to share.
 * @return base64url token of the document JSON.
 */
export function encodeShareLink(workspace: WorkspaceDocument): string {
  const {panels: _panels, ...shareable} = workspace;
  return toBase64Url(JSON.stringify({...shareable, panels: {}}));
}

/**
 * Decodes and validates a share token.
 * @param token base64url token from `encodeShareLink`.
 * @param nowMs Current time (UTC epoch ms) for migrations.
 * @return Parse result; a token that does not decode reports a damaged link.
 */
export function decodeShareLink(
  token: string,
  nowMs: number
): WorkspaceParseResult {
  // base64 spells 3 bytes as 4 characters: a longer token cannot hold a
  // document within the limit, so it is refused before decoding.
  if (token.length > (MAX_WORKSPACE_BYTES * 4) / 3) {
    return {ok: false, error: 'The share link is too long'};
  }
  try {
    return parseWorkspace(fromBase64Url(token), nowMs);
  } catch {
    return {ok: false, error: 'The share link is damaged'};
  }
}

/** Stores workspaces as JSON under `joseph.workspace.v1.<workspace id>`. */
export class WorkspaceRepository {
  /**
   * Creates the repository.
   * @param storage Storage the documents are kept in.
   */
  constructor(private readonly storage: WorkspaceStorage) {}

  /**
   * Loads a workspace, migrating legacy data.
   * @param workspaceId Workspace id.
   * @param nowMs Current time (UTC epoch ms) for migrations.
   * @return Workspace, or null when nothing is stored or the stored data is
   *     invalid.
   */
  load(workspaceId: string, nowMs: number): WorkspaceDocument | null {
    const raw = this.storage.getItem(`joseph.workspace.v1.${workspaceId}`);
    if (!raw) return null;
    try {
      const result = readWorkspaceValue(
        JSON.parse(raw),
        nowMs,
        Number.POSITIVE_INFINITY
      );
      return result.ok ? result.workspace : null;
    } catch {
      return null;
    }
  }

  /**
   * Saves a workspace under its `workspace_id`, replacing the stored copy.
   * @param workspace Workspace to store.
   */
  save(workspace: WorkspaceDocument): void {
    this.storage.setItem(
      `joseph.workspace.v1.${workspace.workspace_id}`,
      JSON.stringify(workspace)
    );
  }
}

/** Debounced saver: only the latest scheduled workspace is written. */
export interface Autosaver {
  /**
   * Schedules a save and restarts the delay.
   * @param workspace Latest workspace; replaces any pending one.
   */
  schedule(workspace: WorkspaceDocument): void;
  /** Saves the pending workspace now, if there is one, and stops the timer. */
  flush(): void;
}

/**
 * Creates a debounced workspace saver.
 * @param repository Repository the workspaces are saved to.
 * @param delayMs Quiet time in milliseconds before a scheduled save runs.
 * @return Autosaver.
 */
export function createAutosaver(
  repository: WorkspaceRepository,
  delayMs = 400
): Autosaver {
  let pending: WorkspaceDocument | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;
  const flush = () => {
    if (timer) clearTimeout(timer);
    timer = null;
    if (pending) repository.save(pending);
    pending = null;
  };
  return {
    schedule(workspace) {
      pending = workspace;
      if (timer) clearTimeout(timer);
      timer = setTimeout(flush, delayMs);
    },
    flush,
  };
}
