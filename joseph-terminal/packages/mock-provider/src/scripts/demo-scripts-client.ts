/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview DEMO `ScriptsClient`: screener scans and alert jobs over
 * the engine's built-in filters and alerts (mirrored in TypeScript) on the
 * synthetic DEMO instruments. Every result is DEMO data: snapshots carry
 * `feed_state: 'DEMO'` and alert signals `data_source: 'DEMO'`. Owned
 * Python scripts need a connected market engine; DEMO never runs Python.
 */

import type {
  AlertJob,
  AlertJobInput,
  CatalogList,
  CatalogScript,
  Instrument,
  JsonValue,
  MarketDataClient,
  MarketSnapshot,
  PythonCatalog,
  ScanConditionPin,
  ScanInput,
  ScanJob,
  ScriptsClient,
  Signal,
} from '@joseph/contracts';
import {demoInstrument} from '../market';
import {DEMO_INSTRUMENT_SPECS} from '../market/prices';
import {demoAlertEvents, hasDemoAlert} from './demo-alerts';
import {
  DemoUnavailable,
  demoFilter,
  hasDemoFilter,
  type DemoBar,
} from './demo-filters';
import {DEMO_SCRIPT_CATALOG} from './demo-script-catalog';

/** DEMO market reads a scan or alert job needs. */
export type DemoScriptsMarket = Pick<
  MarketDataClient,
  'getSnapshot' | 'getCandles' | 'getOrderBook'
>;

/** Options of the DEMO scripts client. */
export interface DemoScriptsOptions {
  /** DEMO market data source. */
  market: DemoScriptsMarket;
  /** Injectable clock (UTC ms). */
  now?: () => number;
}

/** Message for owned scripts, which only the engine can run. */
const ENGINE_ONLY =
  'DEMO runs only the built-in filters and alerts; connect the market engine to use your own Python scripts';

/**
 * Looks up a catalog script.
 * @param scriptId Catalog id.
 * @return Entry or undefined.
 */
function entryOf(scriptId: string): CatalogScript | undefined {
  return DEMO_SCRIPT_CATALOG.find(s => s.script_id === scriptId);
}

/**
 * Merges inputs over a catalog script's defaults, rejecting undeclared ones.
 * @param entry Catalog script.
 * @param inputs Overrides.
 * @return Effective inputs.
 * @throws `Error` for an undeclared input or a value outside its options.
 */
function effectiveInputs(
  entry: CatalogScript,
  inputs: Readonly<Record<string, unknown>> = {}
): Record<string, JsonValue> {
  const merged: Record<string, JsonValue> = {};
  for (const [name, spec] of Object.entries(entry.inputs)) {
    if (spec) merged[name] = spec.default;
  }
  for (const [name, value] of Object.entries(inputs)) {
    const spec = entry.inputs[name];
    if (!spec) throw new Error(`${entry.script_id} has no input ${name}`);
    if (spec.options && !spec.options.includes(value)) {
      throw new Error(`${name} must be one of ${spec.options.join(', ')}`);
    }
    merged[name] = value;
  }
  return merged;
}

/** In-memory DEMO scans and alert jobs; nothing leaves the browser. */
export class DemoScriptsClient implements ScriptsClient {
  private readonly market: DemoScriptsMarket;
  private readonly now: () => number;
  private readonly scans = new Map<string, ScanJob>();
  private readonly jobs = new Map<string, AlertJob>();
  private readonly log = new Map<string, Signal>();
  private sequence = 0;

  /**
   * Creates the client.
   * @param options DEMO market source and clock.
   */
  constructor(options: DemoScriptsOptions) {
    this.market = options.market;
    this.now = options.now ?? (() => Date.now());
  }

  /**
   * Lists the DEMO mirror of the built-in filters and alerts.
   * @param kind Optional role filter.
   * @return Catalog listing.
   */
  async catalog(kind?: CatalogScript['kind']): Promise<CatalogList> {
    return {
      scripts: DEMO_SCRIPT_CATALOG.filter(s => !kind || s.kind === kind),
      failures: [],
    };
  }

  /**
   * Lists owned scripts: none in DEMO.
   * @return Empty catalog with the runtime disabled.
   */
  async list(): Promise<PythonCatalog> {
    return {scripts: [], runtime_enabled: false, next_offset: null};
  }

  /**
   * Runs a DEMO scan of built-in filters combined with AND.
   * @param body Market selection, bounds and conditions.
   * @return Running job; poll `scanStatus` for results.
   * @throws `Error` for owned scripts, non-filters or invalid inputs.
   */
  async screen(body: ScanInput): Promise<ScanJob> {
    const conditions = body.conditions ?? [];
    if (!conditions.length) throw new Error('add at least one filter');
    const pins: ScanConditionPin[] = conditions.map(condition => {
      const entry = entryOf(condition.script_id);
      if (!entry || !hasDemoFilter(entry.script_id)) {
        throw new Error(entry ? `${entry.title} is not a filter` : ENGINE_ONLY);
      }
      return {
        script_id: entry.script_id,
        version: 0,
        source_hash: entry.source_hash,
        title: entry.title,
        inputs: effectiveInputs(entry, condition.inputs),
      };
    });
    const [first] = pins;
    if (!first) throw new Error('add at least one filter');
    const job: ScanJob = {
      job_id: `demo-scan-${++this.sequence}`,
      script_id: first.script_id,
      version: 0,
      source_hash: first.source_hash,
      status: 'running',
      created_at_ms: this.now(),
      total: 0,
      scanned: 0,
      universe_count: 0,
      matches: [],
      errors: [],
      unavailable: [],
      error: null,
      conditions: pins,
    };
    this.scans.set(job.job_id, job);
    void this.run(job, body, pins);
    return {...job};
  }

  /**
   * Evaluates a DEMO scan, updating the stored job.
   * @param job Stored job.
   * @param body Scan request.
   * @param pins Resolved conditions.
   */
  private async run(
    job: ScanJob,
    body: ScanInput,
    pins: readonly ScanConditionPin[]
  ): Promise<void> {
    const now = this.now();
    const needle = (body.query ?? '').trim().toLowerCase();
    let rows: Instrument[] = DEMO_INSTRUMENT_SPECS.map(spec =>
      demoInstrument(spec, now)
    ).filter(
      row =>
        (!body.venue || body.venue === 'mock') &&
        (!body.market_type || row.key.market_type === body.market_type) &&
        (!needle ||
          `${row.instrument_id} ${row.name ?? ''} ${row.local_name ?? ''}`
            .toLowerCase()
            .includes(needle))
    );
    const snapshots = new Map<string, MarketSnapshot>();
    for (const row of rows) {
      try {
        snapshots.set(
          row.instrument_id,
          await this.market.getSnapshot(row.instrument_id)
        );
      } catch {
        // Reported as unavailable below.
      }
    }
    if (body.order_by === 'turnover') {
      const turnover = (row: Instrument) =>
        Number(snapshots.get(row.instrument_id)?.turnover_24h_quote ?? -1);
      rows = [...rows].sort((a, b) => turnover(b) - turnover(a));
    }
    job.universe_count = rows.length;
    rows = rows.slice(0, body.max_symbols ?? 100);
    job.total = rows.length;
    const matches: NonNullable<ScanJob['matches']> = [];
    const unavailable: NonNullable<ScanJob['unavailable']> = [];
    const errors: NonNullable<ScanJob['errors']> = [];
    job.matches = matches;
    job.unavailable = unavailable;
    job.errors = errors;
    const needsBook = pins.some(pin =>
      (entryOf(pin.script_id)?.metrics ?? []).includes('order_book')
    );
    for (const row of rows) {
      if (job.status !== 'running') return;
      const id = row.instrument_id;
      const snapshot = snapshots.get(id);
      if (!snapshot) {
        unavailable.push({instrument_id: id, error: 'quote unavailable'});
        job.scanned = (job.scanned ?? 0) + 1;
        continue;
      }
      let bars: DemoBar[] = [];
      if (body.history_bars) {
        const batch = await this.market.getCandles({
          instrumentId: id,
          timeframe: body.tf ?? '1d',
          limit: body.history_bars,
        });
        bars = batch.candles
          .filter(c => c.is_final)
          .map(c => ({
            open: Number(c.open),
            high: Number(c.high),
            low: Number(c.low),
            close: Number(c.close),
            volume: Number(c.base_volume),
          }));
      }
      const book = needsBook ? await this.market.getOrderBook(id, 100) : null;
      const context = {snapshot, instrument: row, bars, book, nowMs: now};
      const reasons: string[] = [];
      let state: 'match' | 'excluded' | 'unavailable' | 'error' = 'match';
      for (const pin of pins) {
        try {
          if (!demoFilter(pin.script_id, context, pin.inputs ?? {})) {
            state = 'excluded';
            break;
          }
        } catch (err) {
          const message = err instanceof Error ? err.message : String(err);
          if (!(err instanceof DemoUnavailable)) {
            errors.push({
              instrument_id: id,
              error: `${pin.title}: ${message}`,
            });
            state = 'error';
            break;
          }
          reasons.push(`${pin.title}: ${message}`);
          state = 'unavailable';
        }
      }
      if (state === 'match') {
        matches.push({
          instrument_id: id,
          snapshot: snapshot,
        });
      } else if (state === 'unavailable') {
        unavailable.push({instrument_id: id, error: reasons.join('; ')});
      }
      job.scanned = (job.scanned ?? 0) + 1;
    }
    if (job.status === 'running') job.status = 'complete';
  }

  /**
   * Reads a DEMO scan.
   * @param id Job id.
   * @return Current job state.
   * @throws `Error` when the job is unknown.
   */
  async scanStatus(id: string): Promise<ScanJob> {
    const job = this.scans.get(id);
    if (!job) throw new Error('scan job not found');
    return structuredClone(job);
  }

  /**
   * Cancels a DEMO scan, keeping partial results.
   * @param id Job id.
   * @return Job state.
   * @throws `Error` when the job is unknown.
   */
  async cancelScan(id: string): Promise<ScanJob> {
    const job = this.scans.get(id);
    if (!job) throw new Error('scan job not found');
    if (job.status === 'running' || job.status === 'pending') {
      job.status = 'cancelled';
    }
    return structuredClone(job);
  }

  /**
   * Lists DEMO alert jobs after evaluating newly closed bars.
   * @return Jobs in creation order.
   */
  async alerts(): Promise<AlertJob[]> {
    await this.evaluate();
    return [...this.jobs.values()].map(job => ({...job}));
  }

  /**
   * Creates or replaces a DEMO alert job on a built-in condition.
   * @param body Condition, symbol, inputs and destinations.
   * @param id Job to replace.
   * @return Saved job; only bars closing after now are evaluated.
   * @throws `Error` for owned scripts, unknown jobs or invalid inputs.
   */
  async saveAlert(body: AlertJobInput, id?: string): Promise<AlertJob> {
    const entry = entryOf(body.script_id);
    if (!entry || !hasDemoAlert(entry.script_id) || body.version !== 0) {
      throw new Error(
        entry ? `${entry.title} cannot publish alerts` : ENGINE_ONLY
      );
    }
    if (id && !this.jobs.has(id)) throw new Error('alert job not found');
    effectiveInputs(entry, body.inputs);
    const now = this.now();
    const job: AlertJob = {
      enabled: false,
      history_bars: 500,
      subscription_ids: [],
      ...body,
      inputs: {...(body.inputs ?? {})},
      job_id: id ?? `demo-alert-${++this.sequence}`,
      created_at_ms: (id && this.jobs.get(id)?.created_at_ms) || now,
      last_processed_ms: now,
      last_error: null,
      last_checked_ms: null,
      emitted: 0,
      source_hash: entry.source_hash,
      title: entry.title,
    };
    this.jobs.set(job.job_id, job);
    return {...job};
  }

  /**
   * Deletes a DEMO alert job; its log stays.
   * @param id Job id.
   */
  async deleteAlert(id: string): Promise<void> {
    this.jobs.delete(id);
  }

  /**
   * Lists the DEMO alert log after evaluating newly closed bars.
   * @param limit Maximum rows.
   * @return CUSTOM_ALERT signals, newest first.
   */
  async alertLog(limit = 200): Promise<Signal[]> {
    await this.evaluate();
    return [...this.log.values()]
      .sort((a, b) => b.bar_close_ms - a.bar_close_ms)
      .slice(0, limit);
  }

  /** Evaluates enabled jobs on bars closed since their checkpoint. */
  private async evaluate(): Promise<void> {
    const now = this.now();
    for (const job of this.jobs.values()) {
      if (!job.enabled) continue;
      const entry = entryOf(job.script_id);
      if (!entry) continue;
      job.last_checked_ms = now;
      try {
        const batch = await this.market.getCandles({
          instrumentId: job.instrument_id,
          timeframe: job.tf,
          limit: job.history_bars ?? 500,
        });
        const candles = batch.candles.filter(
          c => c.is_final && c.end_ms <= now
        );
        const events = demoAlertEvents(
          job.script_id,
          candles.map(c => ({
            open: Number(c.open),
            high: Number(c.high),
            low: Number(c.low),
            close: Number(c.close),
            volume: Number(c.base_volume),
          })),
          effectiveInputs(entry, job.inputs),
          job.instrument_id
        );
        for (const event of events) {
          const candle = candles[event.index];
          if (!candle || candle.end_ms <= job.last_processed_ms) continue;
          const key = `demo|${job.job_id}|0:${entry.source_hash}|${event.name}|${candle.start_ms}`;
          if (this.log.has(key)) continue;
          this.log.set(key, {
            signal_id: key,
            dedupe_key: key,
            tenant_id: 'demo-tenant',
            strategy_id: job.script_id,
            strategy_version_id: `${job.script_id}:v0`,
            source_hash: entry.source_hash,
            instrument_id: job.instrument_id,
            direction: 'NONE',
            timeframe: job.tf,
            kind: 'CUSTOM_ALERT',
            severity: 'INFO',
            mode: 'ALERT_ONLY',
            bar_start_ms: candle.start_ms,
            bar_close_ms: candle.end_ms,
            evaluated_at_ms: now,
            data_source: 'DEMO',
            entry_price: null,
            stop_price: null,
            target_price: null,
            rr: null,
            condition: event.name,
            message: `DEMO: ${event.message}`,
          });
          job.emitted = (job.emitted ?? 0) + 1;
        }
        job.last_processed_ms = Math.max(
          job.last_processed_ms,
          ...candles.map(c => c.end_ms)
        );
        job.last_error = null;
      } catch (err) {
        job.last_error = err instanceof Error ? err.message : String(err);
      }
    }
  }
}
