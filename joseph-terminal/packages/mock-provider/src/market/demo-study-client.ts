/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview DEMO `StudyClient`: the engine's built-in script catalog
 * (`contracts/openapi/builtin-scripts.json`, exported from the engine) and
 * batched study runs over DEMO candles through the DEMO study evaluator.
 * Plugin and owned scripts report that a backend is needed; public Python
 * source is only available from a backend.
 */

import builtinCatalog from '@joseph/contracts/builtin-scripts.json';
import type {
  Candle,
  CatalogKind,
  CatalogList,
  CatalogScript,
  MarketDataClient,
  ScriptBatchEntry,
  ScriptBatchResult,
  ScriptBatchRun,
  ScriptResult,
  StudyClient,
  Timeframe,
} from '@joseph/contracts';
import {demoSpec, demoSpecCalendar} from './demo-market-client';
import {evaluateBuiltin, studyPeriods, type StudyBar} from './demo-scripts';

/** The built-in catalog as the engine lists it. */
const CATALOG = builtinCatalog as unknown as CatalogList;

/** Lower timeframe of `auto` intrabar requests by chart timeframe class. */
const AUTO_INTRABARS: ReadonlyArray<readonly [number, Timeframe]> = [
  [60_000, '1s'],
  [86_400_000, '1m'],
  [86_400_001, '5m'],
];

/** Intrabar timeframes of the volume delta input. */
const INTRABAR_TFS = new Set(['1s', '5s', '15s', '1m', '3m', '5m', '15m']);

/** Approximate timeframe lengths for ordering (a month counts thirty days). */
const UNIT_MS: Readonly<Record<string, number>> = {
  s: 1000,
  m: 60_000,
  h: 3_600_000,
  d: 86_400_000,
  w: 604_800_000,
  M: 2_592_000_000,
};

/**
 * Approximates a timeframe's length.
 * @param tf Timeframe.
 * @return Milliseconds.
 */
function duration(tf: string): number {
  return Number(tf.slice(0, -1) || 1) * (UNIT_MS[tf.slice(-1)] ?? 0);
}

/**
 * Converts a candle to the engine's script bar.
 * @param candle DEMO candle.
 * @return Script bar.
 */
function studyBar(candle: Candle): StudyBar {
  return {
    time_ms: candle.start_ms,
    end_ms: candle.end_ms,
    is_final: candle.is_final,
    open: Number(candle.open),
    high: Number(candle.high),
    low: Number(candle.low),
    close: Number(candle.close),
    volume: Number(candle.base_volume),
    taker_buy_volume:
      candle.taker_buy_base_volume === null ||
      candle.taker_buy_base_volume === undefined
        ? null
        : Number(candle.taker_buy_base_volume),
    taker_buy_method: candle.taker_buy_method ?? null,
  };
}

/**
 * Picks the lower timeframe of an `auto` intrabar request.
 * @param chart Chart timeframe.
 * @return Lower timeframe, or null on 1s and 1m charts.
 */
function autoIntrabars(chart: Timeframe): Timeframe | null {
  if (chart === '1s' || chart === '1m') return null;
  const length = duration(chart);
  for (const [below, tf] of AUTO_INTRABARS) {
    if (length < below) return tf;
  }
  return length <= 86_400_000 ? '5m' : '1h';
}

/** DEMO built-in catalog and study runs. */
export class DemoStudyClient implements StudyClient {
  /**
   * Creates the client.
   * @param market DEMO market client whose candles the studies read.
   */
  constructor(private readonly market: MarketDataClient) {}

  /**
   * Lists the built-in scripts (DEMO has no plugins).
   * @param kind Optional role filter.
   * @return The engine's built-in catalog.
   */
  catalog(kind?: CatalogKind): Promise<CatalogList> {
    return Promise.resolve({
      scripts: CATALOG.scripts.filter(s => !kind || s.kind === kind),
      failures: [],
    });
  }

  /**
   * Reads one built-in script; DEMO has no Python source.
   * @param scriptId Catalog id.
   * @return The script without source.
   * @throws `Error` for ids outside the built-in catalog.
   */
  catalogScript(scriptId: string): Promise<CatalogScript> {
    const script = CATALOG.scripts.find(s => s.script_id === scriptId);
    if (!script) {
      return Promise.reject(new Error(`script ${scriptId} is not installed`));
    }
    return Promise.resolve({...script, source: null});
  }

  /**
   * Runs several built-in studies over one fetch of DEMO candles.
   * @param body Shared candle range plus one entry per study.
   * @return One result or error per study.
   */
  async runBatch(body: ScriptBatchRun): Promise<ScriptBatchResult> {
    const bars = (await this.candles(body))
      .filter(candle => candle.is_final || body.include_open)
      .map(studyBar);
    const results: ScriptBatchEntry[] = [];
    for (const run of body.runs) {
      try {
        results.push({
          script_id: run.script_id,
          result: await this.runOne(
            body,
            run.script_id,
            run.inputs ?? {},
            bars
          ),
          error: null,
        });
      } catch (failure) {
        results.push({
          script_id: run.script_id,
          result: null,
          error: failure instanceof Error ? failure.message : 'Study failed',
        });
      }
    }
    return {results};
  }

  /**
   * Fetches the chart candles of a run like the engine: the newest `limit`
   * bars, from `from_ms` when given (in DEMO pages of 2 000 bars).
   * @param body Run settings.
   * @return Candles, oldest first.
   */
  private async candles(body: ScriptBatchRun): Promise<Candle[]> {
    const limit = Math.min(body.limit ?? 500, 5000);
    if (body.from_ms === null || body.from_ms === undefined) {
      const batch = await this.market.getCandles({
        instrumentId: body.instrument_id,
        timeframe: body.tf,
        limit: Math.min(limit, 2000),
        ...(body.to_ms ? {toMs: body.to_ms} : {}),
      });
      return batch.candles;
    }
    const calendar = demoSpecCalendar(demoSpec(body.instrument_id));
    const collected: Candle[] = [];
    let cursor = calendar.floor(body.tf, body.from_ms);
    for (let page = 0; page < 30; page += 1) {
      const toMs = calendar.shift(body.tf, cursor, 2000);
      const batch = await this.market.getCandles({
        instrumentId: body.instrument_id,
        timeframe: body.tf,
        fromMs: cursor,
        toMs: body.to_ms ? Math.min(toMs, body.to_ms) : toMs,
      });
      const fresh = batch.candles.filter(
        candle => candle.start_ms >= cursor && candle.start_ms < toMs
      );
      collected.push(...fresh);
      if (fresh.length < 2000) break;
      cursor = toMs;
    }
    return collected.slice(-limit);
  }

  /**
   * Runs one built-in study.
   * @param body Shared run settings.
   * @param scriptId Catalog id.
   * @param overrides Study inputs.
   * @param bars Chart bars.
   * @return Result shaped like the engine's.
   * @throws `Error` for scripts DEMO cannot run or invalid inputs.
   */
  private async runOne(
    body: ScriptBatchRun,
    scriptId: string,
    overrides: Readonly<Record<string, unknown>>,
    bars: StudyBar[]
  ): Promise<ScriptResult> {
    const script = CATALOG.scripts.find(s => s.script_id === scriptId);
    if (!script || script.kind !== 'indicator') {
      throw new Error('Connect a backend to run plugin and owned scripts');
    }
    if (bars.length === 0) throw new Error('No candles for this study');
    const inputs: Record<string, unknown> = {
      ...Object.fromEntries(
        Object.entries(script.inputs).map(([key, spec]) => [key, spec?.default])
      ),
      ...(body.inputs ?? {}),
      ...overrides,
    };
    const spec = demoSpec(body.instrument_id);
    const calendar = demoSpecCalendar(spec);
    const periods = Object.fromEntries(
      studyPeriods(scriptId).map(tf => [
        tf,
        bars.map(bar => {
          const start = calendar.floor(tf as Timeframe, bar.time_ms);
          return [start, calendar.shift(tf as Timeframe, start, 1)] as const;
        }),
      ])
    );
    const extra: Record<string, StudyBar[]> = {};
    for (const [alias, request] of Object.entries(script.requests)) {
      extra[alias] = await this.request(body, request ?? {}, inputs, bars);
    }
    const output = evaluateBuiltin(scriptId, {
      bars,
      inputs,
      timeframe: body.tf,
      extra,
      periods,
    });
    return {
      script_id: scriptId,
      version: 0,
      kind: 'indicator',
      source_hash: 'demo',
      instrument_id: body.instrument_id,
      tf: body.tf,
      bars_count: bars.length,
      feed_state: 'DEMO',
      data_source: 'mock',
      complete: true,
      overlay: script.overlay,
      fills: [],
      equity_curve: [],
      metrics: {},
      bar_visuals: [],
      bar_closes: Object.fromEntries(
        bars.filter(bar => bar.is_final).map(bar => [bar.time_ms, bar.end_ms])
      ),
      ...output,
    };
  }

  /**
   * Fetches one declared request's DEMO bars like the engine host.
   * @param body Run settings.
   * @param request Declared request.
   * @param inputs Effective inputs.
   * @param bars Chart bars.
   * @return Closed bars; empty when nothing is needed.
   */
  private async request(
    body: ScriptBatchRun,
    request: Readonly<Record<string, unknown>>,
    inputs: Readonly<Record<string, unknown>>,
    bars: StudyBar[]
  ): Promise<StudyBar[]> {
    const intrabars = request.intrabars === true;
    const selected =
      typeof request.tf_input === 'string'
        ? String(inputs[request.tf_input] ?? 'auto')
        : String(request.tf);
    let tf: Timeframe | null;
    if (selected === 'auto') {
      tf = intrabars
        ? autoIntrabars(body.tf)
        : ((['1d', '1w', '1M'].find(p => duration(body.tf) < duration(p)) ??
            null) as Timeframe | null);
    } else tf = selected as Timeframe;
    if (!tf || (intrabars && !INTRABAR_TFS.has(tf))) return [];
    if (intrabars && duration(tf) >= duration(body.tf)) return [];
    const skip =
      typeof request.unless === 'string' &&
      (typeof request.unless_input !== 'string' ||
        inputs[request.unless_input] === true);
    let fromMs: number | undefined;
    if (intrabars) {
      const needed = bars.filter(
        bar =>
          (body.window_from_ms === null ||
            body.window_from_ms === undefined ||
            bar.end_ms > body.window_from_ms) &&
          (!skip || bar.taker_buy_volume === null)
      );
      const [first] = needed;
      if (!first) return [];
      fromMs = first.time_ms;
    }
    const limit = Math.min(Number(request.limit ?? 500), 2000);
    const batch = await this.market.getCandles({
      instrumentId: body.instrument_id,
      timeframe: tf,
      limit,
      ...(fromMs !== undefined ? {fromMs} : {}),
      toMs: bars.at(-1)?.end_ms ?? Date.now(),
    });
    return batch.candles
      .filter(candle => candle.is_final)
      .map(studyBar)
      .slice(-limit);
  }
}
