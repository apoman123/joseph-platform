/**
 * @fileoverview Drawing persistence per (workspace, exact instrument).
 *
 * Drawings are keyed by canonical instrument id, so `binance:perp:BTCUSDT`
 * and `hyperliquid:perp:BTC` never share drawings. Unreadable payloads are
 * saved back verbatim.
 */
import type {DrawingDocument, InstrumentId} from '@joseph/contracts';
import {MigrationRegistry} from './migrations';
import type {DrawingState, DrawingStore} from './store';
import type {ToolRegistry} from './tool';

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
   * Storage key for an instrument.
   * @param instrumentId Instrument.
   * @return Key.
   */
  key(instrumentId: InstrumentId): string {
    return `joseph.drawings.v1.${this.workspaceId}.${instrumentId}`;
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
      drawings.push(this.upgradeTool(outcome.doc));
    }
    return {drawings, opaque};
  }

  /**
   * Applies tool-level migrations when the installed tool is newer.
   * @param doc Document.
   * @return Possibly upgraded document.
   */
  private upgradeTool(doc: DrawingDocument): DrawingDocument {
    const tool = this.tools?.get(doc.tool_id);
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
