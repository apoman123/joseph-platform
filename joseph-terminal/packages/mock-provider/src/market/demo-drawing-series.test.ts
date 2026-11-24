/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Tests for the DEMO anchored VWAP and regression channel
 * series (the engine stand-in for the engine-backed drawing tools).
 */

import {readFileSync, readdirSync} from 'node:fs';
import {join} from 'node:path';
import Ajv2020 from 'ajv/dist/2020';
import addFormats from 'ajv-formats';
import {
  ApiError,
  TIMEFRAME_MS,
  floorToTimeframe,
  type Candle,
} from '@joseph/contracts';
import {describe, expect, it} from 'vitest';
import {
  demoAnchorRefusal,
  DEMO_SERIES_BAR_CAP,
  anchoredVwapPoints,
  regressionValues,
} from './demo-drawing-series';
import {DemoMarketDataClient} from './demo-market-client';

const SCHEMA_DIR = join(__dirname, '..', '..', '..', 'contracts', 'schemas');
const NOW = Date.UTC(2026, 10, 20, 12, 7, 30);
const DURATION = TIMEFRAME_MS['5m'];
const LAST = floorToTimeframe('5m', NOW) - DURATION;
const client = new DemoMarketDataClient({now: () => NOW});

/**
 * Validates an `IndicatorSeries` against the contract schema.
 * @param value Candidate series.
 * @return Whether it is valid.
 * @throws Error when the schemas do not define `IndicatorSeries`.
 */
function validSeries(value: unknown): boolean {
  const ajv = new Ajv2020({strict: false, allErrors: true});
  addFormats(ajv);
  for (const file of readdirSync(SCHEMA_DIR)) {
    if (file.endsWith('.schema.json')) {
      ajv.addSchema(JSON.parse(readFileSync(join(SCHEMA_DIR, file), 'utf8')));
    }
  }
  const validate = ajv.getSchema(
    'https://schemas.joseph.local/v1/indicators.schema.json#/$defs/IndicatorSeries'
  );
  if (!validate) throw new Error('missing IndicatorSeries');
  return validate(value) as boolean;
}

/**
 * Builds a candle with the given prices.
 * @param i Bar index (start = i minutes).
 * @param ohlc Open, high, low, close.
 * @param volume Base volume.
 * @return Candle.
 */
function candle(
  i: number,
  [open, high, low, close]: [number, number, number, number],
  volume: number
): Candle {
  return {
    instrument_id: 'mock:perp:BTCUSD',
    timeframe: '1m',
    start_ms: i * 60_000,
    end_ms: (i + 1) * 60_000,
    open: String(open),
    high: String(high),
    low: String(low),
    close: String(close),
    base_volume: String(volume),
    quote_turnover: null,
    trade_count: null,
    is_final: true,
    revision: 0,
    source: 'mock',
    feed_state: 'DEMO',
    received_at_ms: 0,
    event_ts_ms: null,
  };
}

describe('anchored VWAP (DEMO engine)', () => {
  it('is the volume-weighted hlc3 from the anchor with σ bands', () => {
    const points = anchoredVwapPoints(
      [candle(0, [10, 12, 8, 10], 1), candle(1, [20, 22, 18, 20], 3)],
      {band_1: '1', band_2: '0', band_3: '0'}
    );
    expect(points[0]?.values).toMatchObject({
      vwap: '10',
      stdev: '0',
      anchor_ms: 0,
      bars: 1,
    });
    // (10·1 + 20·3) / 4 = 17.5; σ = sqrt(1300/4 − 17.5²) = sqrt(18.75).
    const second = points[1]?.values ?? {};
    expect(second.vwap).toBe('17.5');
    expect(Number(second.stdev)).toBeCloseTo(Math.sqrt(18.75), 7);
    expect(Number(second.upper_1)).toBeCloseTo(17.5 + Math.sqrt(18.75), 7);
    expect(Number(second.lower_1)).toBeCloseTo(17.5 - Math.sqrt(18.75), 7);
    expect(second.upper_2).toBeNull();
    expect(second.bars).toBe(2);
  });

  it('serves a contract-valid series from the anchor bar', async () => {
    const anchor = LAST - 9 * DURATION;
    const series = await client.getIndicatorSeries({
      instrumentId: 'mock:perp:BTCUSD',
      timeframe: '5m',
      pluginId: 'anchored_vwap',
      fromMs: anchor,
      settings: {
        anchor: 'time',
        anchor_ms: anchor,
        source: 'hlc3',
        band_1: '1',
        band_2: '2',
        band_3: '0',
      },
    });
    expect(validSeries(series)).toBe(true);
    expect(series.points).toHaveLength(10);
    expect(series.points[0]?.bar_start_ms).toBe(anchor);
    expect(series.points[0]?.values.anchor_ms).toBe(anchor);
    expect(series.points.at(-1)?.values.bars).toBe(10);
    expect(series.points.at(-1)?.values.upper_2).toMatch(/^\d+(\.\d+)?$/);
    expect(series.points.at(-1)?.values.upper_3).toBeNull();
  });

  it('computes from anchors far back (more than the old 2000 bars)', async () => {
    const anchor = LAST - 2500 * DURATION;
    const series = await client.getIndicatorSeries({
      instrumentId: 'mock:perp:BTCUSD',
      timeframe: '5m',
      pluginId: 'anchored_vwap',
      fromMs: anchor,
      settings: {anchor: 'time', anchor_ms: anchor, band_1: '1'},
    });
    expect(validSeries(series)).toBe(true);
    expect(series.points).toHaveLength(2501);
    expect(series.points[0]?.values.anchor_ms).toBe(anchor);
    expect(series.points.at(-1)?.values.vwap).toMatch(/^\d+(\.\d+)?$/);
  });

  it('refuses anchors more than 50 000 bars back like the engine', async () => {
    const anchor = LAST - DEMO_SERIES_BAR_CAP * DURATION;
    const refused = client.getIndicatorSeries({
      instrumentId: 'mock:perp:BTCUSD',
      timeframe: '5m',
      pluginId: 'anchored_vwap',
      fromMs: anchor,
      settings: {anchor: 'time', anchor_ms: anchor, band_1: '1'},
    });
    await expect(refused).rejects.toBeInstanceOf(ApiError);
    await expect(refused).rejects.toMatchObject({
      status: 422,
      body: {message: demoAnchorRefusal(50_001, '5m')},
    });
  });

  it('rejects malformed settings like the engine', async () => {
    await expect(
      client.getIndicatorSeries({
        instrumentId: 'mock:perp:BTCUSD',
        timeframe: '5m',
        pluginId: 'anchored_vwap',
        fromMs: LAST,
        settings: {anchor_ms: LAST, band_1: 1},
      })
    ).rejects.toThrow(/band_1/);
  });
});

describe('regression channel (DEMO engine)', () => {
  it('fits a least-squares line with a sample σ and Pearson R', () => {
    const exact = regressionValues(
      [1, 2, 3].map(c => candle(c, [c, c, c, c], 1)),
      {deviations: '2'}
    );
    expect(exact).toMatchObject({
      start: '1',
      end: '3',
      slope: '1',
      stdev: '0',
      pearson_r: '1',
      bars: 3,
      start_ms: 60_000,
    });
    // closes 1, 3, 2: slope 0.5, line 1.5..2.5, residuals −0.5, 1, −0.5.
    const noisy = regressionValues(
      [1, 3, 2].map((c, i) => candle(i, [c, c, c, c], 1)),
      {deviations: '2'}
    );
    const sigma = Math.sqrt(1.5 / 2);
    expect(noisy).toMatchObject({
      start: '1.5',
      end: '2.5',
      slope: '0.5',
      pearson_r: '0.5',
      max_up: '1',
      max_down: '0.5',
    });
    expect(Number(noisy.stdev)).toBeCloseTo(sigma, 7);
    expect(Number(noisy.upper_end)).toBeCloseTo(2.5 + 2 * sigma, 7);
    expect(Number(noisy.lower_start)).toBeCloseTo(1.5 - 2 * sigma, 7);
  });

  it('is null for fewer than two bars', () => {
    expect(regressionValues([candle(0, [1, 1, 1, 1], 1)], {})).toMatchObject({
      start: null,
      pearson_r: null,
      bars: 1,
    });
  });

  it('publishes the channel at the t2 bar, also for a t1 far back', async () => {
    const t1 = LAST - 50 * DURATION;
    const t2 = LAST - 10 * DURATION;
    const query = {
      instrumentId: 'mock:perp:BTCUSD',
      timeframe: '5m' as const,
      pluginId: 'regression_channel',
      fromMs: t2,
    };
    const series = await client.getIndicatorSeries({
      ...query,
      settings: {anchor_ms: t1, source: 'close', deviations: '2'},
    });
    expect(validSeries(series)).toBe(true);
    const point = series.points.find(p => p.bar_start_ms === t2);
    expect(point?.values).toMatchObject({start_ms: t1, bars: 41});
    expect(Number(point?.values.upper_end)).toBeGreaterThan(
      Number(point?.values.end)
    );
    const farBack = LAST - 2400 * DURATION;
    const long = await client.getIndicatorSeries({
      ...query,
      settings: {anchor_ms: farBack, deviations: '2'},
    });
    const atEnd = long.points.find(p => p.bar_start_ms === t2);
    expect(atEnd?.values).toMatchObject({start_ms: farBack, bars: 2391});
  });
});
