/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Workspace persistence: versioned storage with migrations,
 * JSON import/export, share links and debounced autosave.
 *
 * The stored and shared `WorkspaceDocument` stays at schema v1. Export
 * files carry their own format version, migrated step by step on import:
 *
 * - format 0: the V4 starter's single chart (`exchange`/`symbol`/
 *   `timeframe`), migrated to a v1 document (`migrateWorkspaceV0`);
 * - format 1: a plain v1 `WorkspaceDocument` (exports before drawings were
 *   included); it imports without touching drawings;
 * - format 2: the `WorkspaceExport` envelope (`format:
 *   'joseph.workspace-export'`) holding the v1 document plus the drawings
 *   of its instruments (`WorkspaceExportDrawings`). The drawing documents
 *   themselves are validated and migrated by `@joseph/drawing-core`.
 *
 * Files from a newer format are refused with an actionable message rather
 * than half-imported. Share links keep carrying the layout only.
 */

import {
  isInstrumentId,
  type WorkspaceDocument,
  type WorkspaceExport,
  type WorkspaceExportDrawings,
} from '@joseph/contracts';
import {LAYOUT_PANES, MAX_CHARTS, createChartState} from './workspace';

/** Storage port (structurally compatible with feature-registry storage). */
export interface WorkspaceStorage {
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
}

/** Maximum size of a workspace document (storage, share links). */
export const MAX_WORKSPACE_BYTES = 64 * 1024;

/** Maximum size of an imported workspace file (layout plus drawings). */
export const MAX_WORKSPACE_FILE_BYTES = 2 * 1024 * 1024;

/** `format` marker of workspace export files. */
export const WORKSPACE_EXPORT_FORMAT = 'joseph.workspace-export';

/** Current export file format version (`WorkspaceExport.schema_version`). */
export const WORKSPACE_EXPORT_VERSION = 2;

/** Most instruments with drawings in one export file (contract limit). */
export const MAX_EXPORT_COLLECTIONS = 500;

/** Outcome of parsing a workspace from untrusted input. */
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
  /** Venue. */
  exchange?: string;
  /** Venue symbol. */
  symbol?: string;
  /** Timeframe. */
  timeframe?: string;
}

/**
 * Migrates the V4 starter's single-chart state to schema v1.
 * @param raw Legacy object.
 * @param nowMs Clock.
 * @return v1 workspace candidate.
 */
export function migrateWorkspaceV0(
  raw: LegacyV0Workspace,
  nowMs: number
): unknown {
  const venue = raw.exchange ?? 'mock';
  const symbol = (raw.symbol ?? 'BTCUSD').toUpperCase();
  const instrumentId = `${venue}:perp:${symbol}`;
  return {
    schema_version: 1,
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
        (raw.timeframe ?? '5m') as never
      ),
    ],
    favorites: {timeframes: ['5m', '15m', '1h', '4h', '1d'], instruments: []},
    panels: {},
    updated_at_ms: nowMs,
  };
}

/**
 * Structural validation of a v1 workspace.
 * @param value Candidate.
 * @return Error message, or null when valid.
 */
export function validateWorkspace(value: unknown): string | null {
  if (!value || typeof value !== 'object') return 'Workspace must be an object';
  const doc = value as Partial<WorkspaceDocument>;
  if (doc.schema_version !== 1) return 'Unsupported workspace version';
  if (typeof doc.workspace_id !== 'string' || !doc.workspace_id) {
    return 'Missing workspace id';
  }
  if (!doc.layout || !(doc.layout in LAYOUT_PANES)) return 'Unknown layout';
  if (
    !Array.isArray(doc.charts) ||
    doc.charts.length < 1 ||
    doc.charts.length > MAX_CHARTS
  ) {
    return 'A workspace needs 1 to 8 charts';
  }
  for (const chart of doc.charts) {
    if (!chart || typeof chart.chart_id !== 'string') {
      return 'Chart is missing an id';
    }
    if (!isInstrumentId(String(chart.instrument_id))) {
      return `Chart ${chart.chart_id} has an invalid instrument id`;
    }
    if (!Array.isArray(chart.indicator_layers)) {
      return `Chart ${chart.chart_id} has no layers`;
    }
  }
  if (!doc.sync || typeof doc.sync.crosshair !== 'boolean') {
    return 'Missing sync options';
  }
  return null;
}

/**
 * Validates a parsed workspace value, migrating the V4 starter shape.
 * @param value Parsed JSON.
 * @param nowMs Clock for migrations.
 * @return Parse result with an actionable error.
 */
function readWorkspaceValue(
  value: unknown,
  nowMs: number
): WorkspaceParseResult {
  let raw = value;
  let migrated = false;
  if (raw && typeof raw === 'object' && !('schema_version' in raw)) {
    raw = migrateWorkspaceV0(raw, nowMs);
    migrated = true;
  }
  const error = validateWorkspace(raw);
  if (error) return {ok: false, error};
  const workspace = raw as WorkspaceDocument;
  if (JSON.stringify(workspace).length > MAX_WORKSPACE_BYTES) {
    return {ok: false, error: 'The workspace layout is larger than 64 KB'};
  }
  if (!workspace.charts.some(c => c.chart_id === workspace.active_chart_id)) {
    workspace.active_chart_id = workspace.charts[0]?.chart_id ?? 'chart-1';
  }
  return {ok: true, workspace, migrated};
}

/**
 * Parses untrusted workspace JSON (stored document or share link),
 * migrating legacy versions. Export files with drawings go through
 * `parseWorkspaceFile`.
 * @param text JSON text.
 * @param nowMs Clock for migrations.
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
 * document, 2 export with drawings), migrating older formats.
 * @param text File text.
 * @param nowMs Clock for migrations.
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
    const plain = readWorkspaceValue(raw, nowMs);
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
  const workspace = readWorkspaceValue(file.workspace, nowMs);
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
 * @param workspace Workspace.
 * @return Pretty JSON.
 */
export function exportWorkspace(workspace: WorkspaceDocument): string {
  return JSON.stringify(workspace, null, 2);
}

/**
 * Builds a workspace export file (format 2): the document plus the drawings
 * of its instruments.
 * @param workspace Workspace.
 * @param drawings Drawing collections (from `DrawingRepository.exportAll`).
 * @param nowMs Export time.
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
 * Serializes a workspace export file (format 2).
 * @param workspace Workspace.
 * @param drawings Drawing collections.
 * @param nowMs Export time.
 * @return Pretty JSON.
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
 * Encodes UTF-8 text as base64url.
 * @param text Text.
 * @return base64url string.
 */
function toBase64Url(text: string): string {
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
 * Decodes base64url to UTF-8 text.
 * @param value base64url string.
 * @return Text.
 * @throws {Error} When the value is not base64url.
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
 * Encodes a workspace into a share token (panel state is not shared).
 * @param workspace Workspace.
 * @return base64url token.
 */
export function encodeShareLink(workspace: WorkspaceDocument): string {
  const {panels: _panels, ...shareable} = workspace;
  return toBase64Url(JSON.stringify({...shareable, panels: {}}));
}

/**
 * Decodes and validates a share token.
 * @param token base64url token.
 * @param nowMs Clock.
 * @return Parse result.
 */
export function decodeShareLink(
  token: string,
  nowMs: number
): WorkspaceParseResult {
  if (token.length > (MAX_WORKSPACE_BYTES * 4) / 3) {
    return {ok: false, error: 'The share link is too long'};
  }
  try {
    return parseWorkspace(fromBase64Url(token), nowMs);
  } catch {
    return {ok: false, error: 'The share link is damaged'};
  }
}

/** Repository of workspaces. */
export class WorkspaceRepository {
  /**
   * Creates the repository.
   * @param storage Storage port.
   */
  constructor(private readonly storage: WorkspaceStorage) {}

  /**
   * Loads a workspace, migrating legacy data; invalid data yields null.
   * @param workspaceId Workspace id.
   * @param nowMs Clock.
   * @return Workspace or null.
   */
  load(workspaceId: string, nowMs: number): WorkspaceDocument | null {
    const raw = this.storage.getItem(`joseph.workspace.v1.${workspaceId}`);
    if (!raw) return null;
    const result = parseWorkspace(raw, nowMs);
    return result.ok ? result.workspace : null;
  }

  /**
   * Saves a workspace.
   * @param workspace Workspace.
   */
  save(workspace: WorkspaceDocument): void {
    this.storage.setItem(
      `joseph.workspace.v1.${workspace.workspace_id}`,
      JSON.stringify(workspace)
    );
  }
}

/** Debounced saver. */
export interface Autosaver {
  /**
   * Schedules a save.
   * @param workspace Latest workspace.
   */
  schedule(workspace: WorkspaceDocument): void;
  /** Saves immediately if a save is pending. */
  flush(): void;
}

/**
 * Creates a debounced workspace saver.
 * @param repository Repository.
 * @param delayMs Debounce delay.
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
