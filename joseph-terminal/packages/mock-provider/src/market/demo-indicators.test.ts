/**
 * @fileoverview Tests for DEMO indicator descriptors, market-structure
 * fixtures and point-in-time (replay) AOI queries, validated against the
 * contract schemas.
 */
import {readFileSync, readdirSync} from 'node:fs';
import {join} from 'node:path';
import Ajv2020 from 'ajv/dist/2020';
import addFormats from 'ajv-formats';
import {TIMEFRAME_MS, floorToTimeframe} from '@joseph/contracts';
import {describe, expect, it} from 'vitest';
import {DemoMarketDataClient} from './demo-market-client';

const SCHEMA_DIR = join(__dirname, '..', '..', '..', 'contracts', 'schemas');
const BASE = 'https://schemas.joseph.local/v1/';
const NOW = Date.UTC(2026, 10, 20, 12, 7, 30);
const client = new DemoMarketDataClient({now: () => NOW});

/**
 * Builds a validator for one contract definition.
 * @param def Definition name in `indicators.schema.json`.
 * @return Validation function.
 */
function validator(def: string) {
  const ajv = new Ajv2020({strict: false, allErrors: true});
  addFormats(ajv);
  for (const file of readdirSync(SCHEMA_DIR)) {
    if (file.endsWith('.schema.json'))
      ajv.addSchema(JSON.parse(readFileSync(join(SCHEMA_DIR, file), 'utf8')));
  }
  const validate = ajv.getSchema(`${BASE}indicators.schema.json#/$defs/${def}`);
  if (!validate) throw new Error(`missing ${def}`);
  return validate;
}

describe('DEMO indicator plugins', () => {
  it('describes eight plugins with real input schemas', async () => {
    const list = await client.listIndicatorPlugins();
    expect(validator('IndicatorPluginList')(list)).toBe(true);
    expect(list.plugins.map(p => p.plugin_id)).toContain('market_structure');
    expect(list.plugins).toHaveLength(8);
    const flow = list.plugins.find(p => p.plugin_id === 'order_flow_pressure');
    const props = (
      flow?.input_schema as {
        properties: Record<string, {default: unknown; maximum?: unknown}>;
      }
    ).properties;
    expect(props.min_delta_share).toMatchObject({
      default: '0.20',
      maximum: '1',
    });
    expect(props.intrabar_tf).toMatchObject({enum: ['1m', '3m', '5m', '15m']});
    const ms = list.plugins.find(p => p.plugin_id === 'market_structure');
    expect(ms?.output_kinds).toEqual(['SWING_HIGH', 'SWING_LOW']);
    expect(ms?.output_events).toContain('SWING_EQL');
  });
});

describe('DEMO market structure and replay', () => {
  it('publishes contract-valid swings, breaks with pivots and pending levels', async () => {
    const list = await client.getAois({
      instrumentId: 'mock:perp:BTCUSD',
      timeframe: '5m',
      plugins: ['market_structure'],
    });
    expect(validator('AoiList')(list)).toBe(true);
    expect(new Set(list.aois.map(a => a.kind))).toEqual(
      new Set(['SWING_HIGH', 'SWING_LOW'])
    );
    const names = (list.events ?? []).map(e => e.name);
    expect(names).toEqual(
      expect.arrayContaining([
        'SWING_HH',
        'SWING_HL',
        'SWING_LL',
        'SWING_LH',
        'SWING_EQH',
        'BOS_BULL',
        'CHOCH_BULL',
        'INITIAL_BREAK_BEAR',
      ])
    );
    for (const event of list.events ?? []) {
      const pivot = (event.meta as {pivot_time_ms?: number}).pivot_time_ms;
      expect(pivot).toBeLessThan(event.bar_start_ms);
    }
  });

  it('answers at_ms with only what was known then', async () => {
    const duration = TIMEFRAME_MS['5m'];
    const lastStart = floorToTimeframe('5m', NOW) - duration;
    const atMs = lastStart - 60 * duration;
    const latest = await client.getAois({
      instrumentId: 'mock:perp:BTCUSD',
      timeframe: '5m',
    });
    const past = await client.getAois({
      instrumentId: 'mock:perp:BTCUSD',
      timeframe: '5m',
      atMs,
    });
    expect(validator('AoiList')(past)).toBe(true);
    expect(past.as_of_ms).toBe(atMs);
    expect(past.aois.length).toBeLessThan(latest.aois.length);
    expect(past.aois.every(a => a.confirmed_at_ms <= atMs)).toBe(true);
    expect((past.events ?? []).every(e => e.confirmed_at_ms <= atMs)).toBe(
      true
    );
    // A pool swept after `atMs` was still active at that time.
    const eql = past.aois.find(a => a.kind === 'EQL');
    expect(eql?.state).toBe('active');
    expect(eql?.invalidated_at_ms).toBeNull();
    const future = await client.getAois({
      instrumentId: 'mock:perp:BTCUSD',
      timeframe: '5m',
      atMs: NOW + 1,
    });
    expect(future.as_of_ms).toBe(latest.as_of_ms);
  });

  it('serves a contract-valid Delta % series', async () => {
    const series = await client.getIndicatorSeries({
      instrumentId: 'mock:perp:BTCUSD',
      timeframe: '5m',
      pluginId: 'order_flow_pressure',
    });
    expect(validator('IndicatorSeries')(series)).toBe(true);
    expect(series.points.length).toBeGreaterThan(100);
  });
});
