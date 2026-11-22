/**
 * @fileoverview DEMO seconds and monthly candles (calendar-correct, with
 * taker-buy volume), the capabilities that declare them, and the true
 * order-flow delta series, validated against the contract schemas.
 */
import {readFileSync, readdirSync} from 'node:fs';
import {join} from 'node:path';
import Ajv2020 from 'ajv/dist/2020';
import addFormats from 'ajv-formats';
import {Decimal, TIMEFRAMES, timeframeEnd} from '@joseph/contracts';
import {describe, expect, it} from 'vitest';
import {DemoMarketDataClient, demoDeltaPoint} from './demo-market-client';
import {DEMO_INSTRUMENT_SPECS, demoCandle} from './prices';

const SCHEMA_DIR = join(__dirname, '..', '..', '..', 'contracts', 'schemas');
const BASE = 'https://schemas.joseph.local/v1/';
const NOW = Date.UTC(2026, 10, 20, 12, 7, 31, 400);
const client = new DemoMarketDataClient({now: () => NOW});
const DAY = 86_400_000;

/**
 * Builds a validator for one contract definition.
 * @param file Schema file.
 * @param def Definition name.
 * @return Validation function.
 */
function validator(file: string, def: string) {
  const ajv = new Ajv2020({strict: false, allErrors: true});
  addFormats(ajv);
  for (const name of readdirSync(SCHEMA_DIR)) {
    if (name.endsWith('.schema.json'))
      ajv.addSchema(JSON.parse(readFileSync(join(SCHEMA_DIR, name), 'utf8')));
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

  it('serves 1M AOIs confirmed by the true month ends', async () => {
    const list = await client.getAois({
      instrumentId: 'mock:perp:BTCUSD',
      timeframe: '1M',
    });
    expect(validator('indicators.schema.json', 'AoiList')(list)).toBe(true);
    expect(list.as_of_ms).toBe(Date.UTC(2026, 10, 1));
    for (const event of list.events ?? []) {
      expect(event.confirmed_at_ms).toBe(
        timeframeEnd('1M', event.bar_start_ms)
      );
      expect(new Date(event.bar_start_ms).getUTCDate()).toBe(1);
    }
  });
});

describe('DEMO order-flow delta', () => {
  it('publishes true delta with buy and sell volume from taker volume', async () => {
    const series = await client.getIndicatorSeries({
      instrumentId: 'mock:perp:BTCUSD',
      timeframe: '5m',
      pluginId: 'order_flow_pressure',
    });
    expect(validator('indicators.schema.json', 'IndicatorSeries')(series)).toBe(
      true
    );
    expect(series.points).toHaveLength(200);
    for (const point of series.points) {
      expect(point.values.method).toBe('taker_volume');
      const buy = Number(point.values.buy_volume);
      const sell = Number(point.values.sell_volume);
      expect(buy).toBeGreaterThanOrEqual(0);
      expect(sell).toBeGreaterThanOrEqual(0);
      const pct = Number(point.values.delta_pct);
      expect(pct).toBeCloseTo(((buy - sell) / (buy + sell)) * 100, 1);
    }
  });

  it('falls back to the candle-direction estimate on request', async () => {
    const series = await client.getIndicatorSeries({
      instrumentId: 'mock:perp:BTCUSD',
      timeframe: '1M',
      pluginId: 'order_flow_pressure',
      settings: {delta_source: 'estimate'},
    });
    expect(
      series.points.every(p => p.values.method === 'candle_direction')
    ).toBe(true);
    expect(series.points.every(p => p.values.buy_volume === null)).toBe(true);
    expect(series.as_of_ms).toBe(Date.UTC(2026, 10, 1));
  });

  it('estimates when a candle has no taker volume', () => {
    const spec = DEMO_INSTRUMENT_SPECS[1];
    if (!spec) throw new Error('missing spec');
    const candle = {
      ...demoCandle(spec, '1h', Date.UTC(2026, 10, 20, 9), NOW),
      taker_buy_base_volume: null,
    };
    expect(demoDeltaPoint(candle, false).values.method).toBe(
      'candle_direction'
    );
  });

  it('offers the delta_source input in the plugin schema', async () => {
    const list = await client.listIndicatorPlugins();
    const flow = list.plugins.find(p => p.plugin_id === 'order_flow_pressure');
    const props = (flow?.input_schema as {properties: Record<string, unknown>})
      .properties;
    expect(props.delta_source).toMatchObject({
      type: 'string',
      enum: ['auto', 'estimate'],
      default: 'auto',
    });
  });
});
