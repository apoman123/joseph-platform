/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Tests for the DEMO built-in studies: hand-derived SMA,
 * EMA, RSI, ATR and Bollinger values, the period VWAP reset, settings
 * validation and the served series.
 */

import {readFileSync, readdirSync} from 'node:fs';
import {join} from 'node:path';
import Ajv2020 from 'ajv/dist/2020';
import addFormats from 'ajv-formats';
import {TIMEFRAME_MS, floorToTimeframe, type Candle} from '@joseph/contracts';
import {describe, expect, it} from 'vitest';
import {DemoMarketDataClient} from './demo-market-client';
import {
  atrValues,
  bollingerValues,
  emaValues,
  periodVwapPoints,
  rmaValues,
  rsiValues,
  smaValues,
} from './demo-studies';

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
 * Builds a one-minute candle with the given prices.
 * @param i Bar index (start = i minutes).
 * @param ohlc Open, high, low, close.
 * @param volume Base volume.
 * @return Candle.
 */
function candle(
  i: number,
  [open, high, low, close]: [number, number, number, number],
  volume = 1
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

describe('moving averages', () => {
  it('averages the last length values', () => {
    expect(smaValues([1, 2, 3, 4, 5], 3)).toEqual([null, null, 2, 3, 4]);
    expect(smaValues([7], 1)).toEqual([7]);
  });

  it('seeds the EMA with the first value', () => {
    // α = 2 / (3 + 1) = 0.5: 1, 1.5, 2.25, 3.125, 4.0625.
    expect(emaValues([1, 2, 3, 4, 5], 3)).toEqual([
      1, 1.5, 2.25, 3.125, 4.0625,
    ]);
  });

  it('seeds the Wilder average with the SMA', () => {
    // Seed (2 + 4) / 2 = 3, then (3 + 6) / 2 = 4.5, (4.5 + 8) / 2 = 6.25.
    expect(rmaValues([2, 4, 6, 8], 2)).toEqual([null, 3, 4.5, 6.25]);
  });
});

describe('oscillators', () => {
  it('computes RSI from Wilder averages of gains and losses', () => {
    // Changes +2, −1, +2, 0. Gains RMA(2): 1, 1.5, 0.75; losses: 0.5,
    // 0.25, 0.125. RS 2, 6, 6 → RSI 66.67, 85.71, 85.71.
    const rsi = rsiValues([10, 12, 11, 13, 13], 2);
    expect(rsi.slice(0, 2)).toEqual([null, null]);
    expect(rsi[2]).toBeCloseTo(200 / 3, 10);
    expect(rsi[3]).toBeCloseTo(600 / 7, 10);
    expect(rsi[4]).toBeCloseTo(600 / 7, 10);
  });

  it('is 100 without losses and 0 without gains', () => {
    expect(rsiValues([1, 2, 3], 2)).toEqual([null, null, 100]);
    expect(rsiValues([3, 2, 1], 2)).toEqual([null, null, 0]);
  });

  it('computes ATR from the true range', () => {
    // True ranges 2 (high − low), 2, 4 (gap from close 10), 2.
    const candles = [
      candle(0, [9, 10, 8, 9]),
      candle(1, [9, 11, 9, 10]),
      candle(2, [12, 14, 12, 13]),
      candle(3, [12, 13, 11, 12]),
    ];
    expect(atrValues(candles, 2)).toEqual([null, 2, 3, 2.5]);
  });

  it('builds Bollinger Bands from the population deviation', () => {
    // Bar 2: mean 1.5, σ 0.5, bands 0.5..2.5, width 2 / 1.5, %B 0.75.
    const bands = bollingerValues([1, 2, 3, 4], 2, 2);
    expect(bands[0]).toBeNull();
    expect(bands[1]).toEqual({
      basis: 1.5,
      upper: 2.5,
      lower: 0.5,
      bandwidth: (2 / 1.5) * 100,
      percentB: 0.75,
    });
    expect(bands[3]).toMatchObject({basis: 3.5, upper: 4.5, lower: 2.5});
    expect(bollingerValues([5, 5], 2, 2)[1]).toMatchObject({
      bandwidth: 0,
      percentB: null,
    });
  });
});

describe('period VWAP', () => {
  it('restarts at the first bar of each period', () => {
    // Bars 0 and 1 share a period; bar 2 starts the next one.
    const candles = [
      candle(0, [10, 10, 10, 10], 1),
      candle(1, [20, 20, 20, 20], 3),
      candle(2, [30, 30, 30, 30], 2),
    ];
    const points = periodVwapPoints(
      candles,
      start => (start < 120_000 ? 0 : 120_000),
      {}
    );
    expect(points.map(p => p.values.vwap)).toEqual(['10', '17.5', '30']);
    expect(points.map(p => p.values.anchor_ms)).toEqual([0, 0, 120_000]);
    expect(points.map(p => p.values.bars)).toEqual([1, 2, 1]);
    expect(points.every(p => p.values.anchor_in_window === true)).toBe(true);
  });

  it('rejects sources the VWAP does not average', () => {
    expect(() =>
      periodVwapPoints([candle(0, [1, 1, 1, 1])], () => 0, {source: 'high'})
    ).toThrow(/source/);
  });
});

describe('DEMO study series', () => {
  it('serves warm, contract-valid series for every study', async () => {
    const mainValues: ReadonlyArray<[string, string]> = [
      ['sma', 'sma'],
      ['ema', 'ema'],
      ['bollinger_bands', 'basis'],
      ['rsi', 'rsi'],
      ['atr', 'atr'],
      ['anchored_vwap', 'vwap'],
      ['volume_delta', 'delta_pct'],
    ];
    for (const [pluginId, key] of mainValues) {
      const series = await client.getIndicatorSeries({
        instrumentId: 'mock:perp:BTCUSD',
        timeframe: '5m',
        pluginId,
      });
      expect(validSeries(series)).toBe(true);
      expect(series.indicator_version).toBe('demo');
      expect(series.feed_state).toBe('DEMO');
      expect(series.points).toHaveLength(200);
      expect(series.points.at(-1)?.bar_start_ms).toBe(LAST);
      // Earlier bars warm the study up, so the first point has a value.
      expect(series.points[0]?.values[key]).toMatch(/^-?\d+(\.\d+)?$/);
    }
  });

  it('follows length, source and the replay window', async () => {
    const query = {
      instrumentId: 'mock:perp:BTCUSD',
      timeframe: '5m' as const,
      pluginId: 'sma',
      fromMs: LAST - 4 * DURATION,
    };
    const one = await client.getIndicatorSeries({
      ...query,
      settings: {length: 1, source: 'open'},
    });
    const candles = await client.getCandles({
      instrumentId: query.instrumentId,
      timeframe: '5m',
      fromMs: query.fromMs,
      toMs: LAST + DURATION,
    });
    expect(one.points).toHaveLength(5);
    expect(one.points.map(p => Number(p.values.sma))).toEqual(
      candles.candles.map(c => Number(c.open))
    );
  });

  it('serves the period VWAP from the session start', async () => {
    const series = await client.getIndicatorSeries({
      instrumentId: 'mock:perp:BTCUSD',
      timeframe: '1h',
      pluginId: 'anchored_vwap',
      settings: {anchor: 'session', band_1: '2'},
    });
    const midnight = series.points.find(
      p => p.bar_start_ms === Date.UTC(2026, 10, 20)
    );
    expect(midnight?.values).toMatchObject({
      anchor_ms: Date.UTC(2026, 10, 20),
      bars: 1,
    });
    const later = series.points.find(
      p => p.bar_start_ms === Date.UTC(2026, 10, 20, 5)
    );
    expect(later?.values).toMatchObject({
      anchor_ms: Date.UTC(2026, 10, 20),
      bars: 6,
    });
    expect(Number(later?.values.upper_1)).toBeGreaterThan(
      Number(later?.values.vwap)
    );
    // On an exchange the period is the trading session.
    const stock = await client.getIndicatorSeries({
      instrumentId: 'mock:stock:ACME',
      timeframe: '15m',
      pluginId: 'anchored_vwap',
    });
    const starts = new Set(stock.points.map(p => p.bar_start_ms));
    for (const point of stock.points) {
      const anchor = Number(point.values.anchor_ms);
      expect(point.bar_start_ms).toBeGreaterThanOrEqual(anchor);
      expect(point.bar_start_ms - anchor).toBeLessThan(7 * 3_600_000);
      if (point.values.bars === 1) expect(anchor).toBe(point.bar_start_ms);
    }
    expect(starts.size).toBe(200);
  });

  it('rejects invalid settings like the engine', async () => {
    const query = {
      instrumentId: 'mock:perp:BTCUSD',
      timeframe: '5m' as const,
    };
    await expect(
      client.getIndicatorSeries({
        ...query,
        pluginId: 'ema',
        settings: {length: 0},
      })
    ).rejects.toThrow(/length/);
    await expect(
      client.getIndicatorSeries({
        ...query,
        pluginId: 'bollinger_bands',
        settings: {mult: '60'},
      })
    ).rejects.toThrow(/mult/);
    await expect(
      client.getIndicatorSeries({
        ...query,
        pluginId: 'rsi',
        settings: {source: 'volume'},
      })
    ).rejects.toThrow(/source/);
  });

  it('serves no points for plugins it does not compute', async () => {
    const series = await client.getIndicatorSeries({
      instrumentId: 'mock:perp:BTCUSD',
      timeframe: '5m',
      pluginId: 'example_zones',
    });
    expect(series.points).toEqual([]);
  });
});
