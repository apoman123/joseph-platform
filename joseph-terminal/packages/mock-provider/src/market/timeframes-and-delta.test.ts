/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview DEMO seconds and monthly candles (calendar-correct, with
 * taker-buy volume), the capabilities that declare them, and the true
 * volume delta series, validated against the contract schemas.
 */

import {readFileSync, readdirSync} from 'node:fs';
import {join} from 'node:path';
import Ajv2020 from 'ajv/dist/2020';
import addFormats from 'ajv-formats';
import {Decimal, TIMEFRAMES, timeframeEnd} from '@joseph/contracts';
import {describe, expect, it} from 'vitest';
import {DemoMarketDataClient} from './demo-market-client';
import {DemoStudyClient} from './demo-study-client';
import {DEMO_INSTRUMENT_SPECS, demoCandle} from './prices';

const SCHEMA_DIR = join(__dirname, '..', '..', '..', 'contracts', 'schemas');
const BASE = 'https://schemas.joseph.local/v1/';
const NOW = Date.UTC(2026, 10, 20, 12, 7, 31, 400);
const client = new DemoMarketDataClient({now: () => NOW});
const studies = new DemoStudyClient(client);
const DAY = 86_400_000;

/**
 * Builds a validator for one contract definition.
 * @param file Schema file name, e.g. `market.schema.json`.
 * @param def Definition name under `$defs`.
 * @return Validation function.
 * @throws Error when the schema does not define `def`.
 */
function validator(file: string, def: string) {
  const ajv = new Ajv2020({strict: false, allErrors: true});
  addFormats(ajv);
  for (const name of readdirSync(SCHEMA_DIR)) {
    if (name.endsWith('.schema.json')) {
      ajv.addSchema(JSON.parse(readFileSync(join(SCHEMA_DIR, name), 'utf8')));
    }
  }
  const validate = ajv.getSchema(`${BASE}${file}#/$defs/${def}`);
  if (!validate) throw new Error(`missing ${def}`);
  return validate;
}

describe('DEMO seconds and monthly candles', () => {
  it('serves contract-valid, deterministic 1s/5s/1M batches', async () => {
    const validate = validator('market.schema.json', 'CandleBatch');
    for (const timeframe of ['1s', '5s', '30s', '1M'] as const) {
      const batch = await client.getCandles({
        instrumentId: 'mock:perp:ETHUSD',
        timeframe,
        limit: 40,
      });
      expect(validate(batch)).toBe(true);
      const again = await new DemoMarketDataClient({
        now: () => NOW,
      }).getCandles({instrumentId: 'mock:perp:ETHUSD', timeframe, limit: 40});
      expect(again.candles).toEqual(batch.candles);
      for (const candle of batch.candles) {
        expect(candle.end_ms).toBe(timeframeEnd(timeframe, candle.start_ms));
        const buy = Decimal.parse(candle.taker_buy_base_volume ?? '-1');
        expect(buy.cmp(Decimal.parse('0'))).toBeGreaterThanOrEqual(0);
        expect(buy.cmp(Decimal.parse(candle.base_volume))).toBeLessThanOrEqual(
          0
        );
        expect(Number(candle.quote_turnover)).toBeGreaterThan(0);
      }
    }
  });

  it('aligns seconds bars and keeps only the newest one open', async () => {
    const batch = await client.getCandles({
      instrumentId: 'mock:perp:BTCUSD',
      timeframe: '5s',
      limit: 12,
    });
    const last = batch.candles.at(-1);
    expect(last?.start_ms).toBe(Date.UTC(2026, 10, 20, 12, 7, 30));
    expect(last?.is_final).toBe(false);
    expect(batch.candles.at(-2)?.is_final).toBe(true);
    const starts = batch.candles.map(c => c.start_ms);
    expect(
      starts.every((t, i) => i === 0 || t - (starts[i - 1] ?? 0) === 5000)
    ).toBe(true);
  });

  it('ends monthly bars on the 1st, including February and December', async () => {
    const batch = await client.getCandles({
      instrumentId: 'mock:perp:BTCUSD',
      timeframe: '1M',
      limit: 14,
    });
    const starts = batch.candles.map(c => new Date(c.start_ms).toISOString());
    expect(starts.at(-1)).toBe('2026-11-01T00:00:00.000Z');
    expect(starts[0]).toBe('2025-10-01T00:00:00.000Z');
    const feb = batch.candles.find(c => c.start_ms === Date.UTC(2026, 1, 1));
    expect(feb ? feb.end_ms - feb.start_ms : 0).toBe(28 * DAY);
    const dec = batch.candles.find(c => c.start_ms === Date.UTC(2025, 11, 1));
    expect(dec?.end_ms).toBe(Date.UTC(2026, 0, 1));
    expect(batch.candles.at(-1)?.is_final).toBe(false);
  });

  it('scales sub-minute volume and wicks below one-minute bars', () => {
    const spec = DEMO_INSTRUMENT_SPECS[0];
    if (!spec) throw new Error('missing spec');
    const start = Date.UTC(2026, 10, 20, 11);
    const second = demoCandle(spec, '1s', start, NOW);
    const minute = demoCandle(spec, '1m', start, NOW);
    expect(Number(second.base_volume)).toBeLessThan(
      Number(minute.base_volume) / 10
    );
    expect(Number(second.high) - Number(second.low)).toBeLessThan(
      Number(minute.high) - Number(minute.low)
    );
  });

  it('declares seconds (trade-built), 1M and taker volume', async () => {
    const venues = await client.listVenues();
    expect(validator('market.schema.json', 'VenueStatusList')(venues)).toBe(
      true
    );
    const caps = venues.venues[0]?.capabilities;
    expect(caps?.timeframes).toEqual([...TIMEFRAMES]);
    expect(caps?.timeframes).toContain('1M');
    expect(caps?.trade_built_timeframes).toEqual(['1s', '5s', '15s', '30s']);
    expect(caps?.market_data.taker_volume).toBe(true);
  });

  it('serves 1M studies on month starts and no 1M pivot levels', async () => {
    const {results} = await studies.runBatch({
      instrument_id: 'mock:perp:BTCUSD',
      tf: '1M',
      limit: 60,
      runs: [
        {script_id: 'builtin:sma', inputs: {length: 3}},
        {script_id: 'builtin:pivot_points'},
      ],
    });
    const [sma, pivots] = results;
    const points = sma?.result?.plots[0]?.points ?? [];
    expect(points.at(-1)?.time_ms).toBe(Date.UTC(2026, 9, 1));
    for (const point of points.slice(-24)) {
      expect(new Date(point.time_ms).getUTCDate()).toBe(1);
      expect(point.value).toEqual(expect.any(Number));
    }
    // Pivot levels need a period above the chart bars.
    expect(
      pivots?.result?.visuals?.filter(v => v.kind === 'line') ?? []
    ).toEqual([]);
  });
});

/**
 * Runs the volume delta built-in over DEMO candles.
 * @param instrument Instrument id.
 * @param tf Timeframe.
 * @param inputs Input overrides.
 * @return Plot values by name and the method table cell.
 */
async function delta(
  instrument: string,
  tf: '5m' | '1h' | '1M',
  inputs: Record<string, unknown> = {}
) {
  const {results} = await studies.runBatch({
    instrument_id: instrument,
    tf,
    limit: 200,
    include_open: true,
    runs: [{script_id: 'builtin:volume_delta', inputs: inputs as never}],
  });
  const result = results[0]?.result;
  if (!result) throw new Error(results[0]?.error ?? 'no result');
  const plots = Object.fromEntries(
    result.plots.map(plot => [plot.name, plot.points.map(p => p.value)])
  );
  const table = result.visuals?.find(v => v.kind === 'table');
  const rows = (table?.data.rows ?? []) as string[][];
  return {plots, method: rows[0]?.[1]};
}

describe('DEMO volume delta', () => {
  it('publishes true delta with buy and sell volume from taker volume', async () => {
    const {plots, method} = await delta('mock:perp:BTCUSD', '5m');
    expect(method).toBe('Taker buy volume');
    const pct = plots['Delta %'] ?? [];
    expect(pct).toHaveLength(200);
    pct.forEach((value, i) => {
      const buy = plots['Buy volume']?.[i] ?? NaN;
      const sell = plots['Sell volume']?.[i] ?? NaN;
      expect(buy).toBeGreaterThanOrEqual(0);
      expect(sell).toBeGreaterThanOrEqual(0);
      expect(value).toBeCloseTo(((buy - sell) / (buy + sell)) * 100, 6);
    });
  });

  it('estimates from intrabars when venue buy volume is off', async () => {
    const {plots, method} = await delta('mock:perp:BTCUSD', '1h', {
      use_buy_volume: false,
    });
    expect(['Intrabar estimate', 'Insufficient intrabar coverage']).toContain(
      method
    );
    expect((plots.Intrabars ?? []).some(count => (count ?? 0) > 0)).toBe(true);
  });

  it('labels classified trades on session stocks', async () => {
    const {method} = await delta('mock:stock:ACME', '1h');
    expect(method).toBe('Classified trades');
  });
});
