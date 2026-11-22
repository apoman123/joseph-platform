/**
 * @fileoverview Workspace persistence: versioned storage with migrations,
 * JSON import/export, share links and debounced autosave.
 */
import {isInstrumentId, type WorkspaceDocument} from '@joseph/contracts';
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

/** Maximum accepted size of imported or shared workspaces. */
export const MAX_WORKSPACE_BYTES = 64 * 1024;

/** Outcome of parsing a workspace from untrusted input. */
export type WorkspaceParseResult =
  | {ok: true; workspace: WorkspaceDocument; migrated: boolean}
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
  if (typeof doc.workspace_id !== 'string' || !doc.workspace_id)
    return 'Missing workspace id';
  if (!doc.layout || !(doc.layout in LAYOUT_PANES)) return 'Unknown layout';
  if (
    !Array.isArray(doc.charts) ||
    doc.charts.length < 1 ||
    doc.charts.length > MAX_CHARTS
  ) {
    return 'A workspace needs 1 to 8 charts';
  }
  for (const chart of doc.charts) {
    if (!chart || typeof chart.chart_id !== 'string')
      return 'Chart is missing an id';
    if (!isInstrumentId(String(chart.instrument_id))) {
      return `Chart ${chart.chart_id} has an invalid instrument id`;
    }
    if (!Array.isArray(chart.indicator_layers))
      return `Chart ${chart.chart_id} has no layers`;
  }
  if (!doc.sync || typeof doc.sync.crosshair !== 'boolean')
    return 'Missing sync options';
  return null;
}

/**
 * Parses untrusted workspace JSON (import or share link), migrating legacy
 * versions.
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
  let migrated = false;
  if (raw && typeof raw === 'object' && !('schema_version' in raw)) {
    raw = migrateWorkspaceV0(raw as LegacyV0Workspace, nowMs);
    migrated = true;
  }
  const error = validateWorkspace(raw);
  if (error) return {ok: false, error};
  const workspace = raw as WorkspaceDocument;
  if (!workspace.charts.some(c => c.chart_id === workspace.active_chart_id)) {
    workspace.active_chart_id = workspace.charts[0]?.chart_id ?? 'chart-1';
  }
  return {ok: true, workspace, migrated};
}

/**
 * Serializes a workspace for export.
 * @param workspace Workspace.
 * @return Pretty JSON.
 */
export function exportWorkspace(workspace: WorkspaceDocument): string {
  return JSON.stringify(workspace, null, 2);
}

/**
 * Encodes UTF-8 text as base64url.
 * @param text Text.
 * @return base64url string.
 */
function toBase64Url(text: string): string {
  const bytes = new TextEncoder().encode(text);
  let binary = '';
  for (const byte of bytes) binary += String.fromCharCode(byte);
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
