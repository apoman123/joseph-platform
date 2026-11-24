/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Tests for DEMO indicator descriptors, the pivot level
 * fixture and point-in-time (replay) AOI queries, validated against the
 * contract schemas.
 */

import {readFileSync, readdirSync} from 'node:fs';
import {join} from 'node:path';
import Ajv2020 from 'ajv/dist/2020';
import addFormats from 'ajv-formats';
import {TIMEFRAME_MS, floorToTimeframe} from '@joseph/contracts';
import {describe, expect, it} from 'vitest';
import {pivotLevels} from './demo-indicators';
import {DemoMarketDataClient} from './demo-market-client';

const SCHEMA_DIR = join(__dirname, '..', '..', '..', 'contracts', 'schemas');
const BASE = 'https://schemas.joseph.local/v1/';
const NOW = Date.UTC(2026, 10, 20, 12, 7, 30);
const client = new DemoMarketDataClient({now: () => NOW});

/**
 * Builds a validator for one contract definition.
 * @param def Definition name in `indicators.schema.json`.
 * @return Validation function.
 * @throws Error when the schema does not define `def`.
 */
function validator(def: string) {
  const ajv = new Ajv2020({strict: false, allErrors: true});
  addFormats(ajv);
  for (const file of readdirSync(SCHEMA_DIR)) {
    if (file.endsWith('.schema.json')) {
      ajv.addSchema(JSON.parse(readFileSync(join(SCHEMA_DIR, file), 'utf8')));
    }
  }
  const validate = ajv.getSchema(`${BASE}indicators.schema.json#/$defs/${def}`);
  if (!validate) throw new Error(`missing ${def}`);
  return validate;
}

describe('DEMO indicator plugins', () => {
  it('lists the nine built-ins with real input schemas', async () => {
    const list = await client.listIndicatorPlugins();
    expect(validator('IndicatorPluginList')(list)).toBe(true);
    expect(list.plugins).toHaveLength(9);
    const delta = list.plugins.find(p => p.plugin_id === 'volume_delta');
    const props = (
      delta?.input_schema as {
        properties: Record<string, {default: unknown; maximum?: unknown}>;
      }
    ).properties;
    expect(props.min_coverage).toMatchObject({default: '0.8', maximum: '1'});
    const pivots = list.plugins.find(p => p.plugin_id === 'pivot_points');
    expect(pivots?.output_kinds).toContain('PIVOT_S1');
    expect(pivots?.output_events).toEqual([]);
  });
});

describe('DEMO pivot levels and replay', () => {
  it('derives the classic floor levels', () => {
    // H 12, L 6, C 9: P 9, R1 12, S1 6, R2 15, S2 3, R3 18, S3 0.
    expect(pivotLevels({high: 12, low: 6, close: 9})).toEqual({
      P: 9,
      R1: 12,
      R2: 15,
      R3: 18,
      S1: 6,
      S2: 3,
      S3: 0,
    });
  });

  it('publishes contract-valid levels in the classic order', async () => {
    const list = await client.getAois({
      instrumentId: 'mock:perp:BTCUSD',
      timeframe: '5m',
      plugins: ['pivot_points'],
    });
    expect(validator('AoiList')(list)).toBe(true);
    expect(list.events).toEqual([]);
    const live = list.aois.filter(a => a.state === 'active');
    expect(live.map(a => a.kind)).toEqual([
      'PIVOT_P',
      'PIVOT_R1',
      'PIVOT_R2',
      'PIVOT_R3',
      'PIVOT_S1',
      'PIVOT_S2',
      'PIVOT_S3',
    ]);
    const price = (kind: string) =>
      Number(live.find(a => a.kind === kind)?.lower);
    expect(price('PIVOT_S3')).toBeLessThan(price('PIVOT_S2'));
    expect(price('PIVOT_S1')).toBeLessThan(price('PIVOT_P'));
    expect(price('PIVOT_P')).toBeLessThan(price('PIVOT_R1'));
    expect(price('PIVOT_R2')).toBeLessThan(price('PIVOT_R3'));
    for (const aoi of list.aois) {
      expect(aoi.lower).toBe(aoi.upper);
      expect(aoi.group_id).toBeNull();
      expect(aoi.end_reason).toBeNull();
    }
    expect(live.find(a => a.kind === 'PIVOT_S1')).toMatchObject({
      aoi_id: 'demo-pivot-s1-5m',
      side: 'bullish',
    });
    const expired = list.aois.filter(a => a.state === 'expired');
    expect(expired).toHaveLength(7);
    // The previous levels expire when the current period's appear.
    expect(expired[0]?.invalidated_at_ms).toBe(live[0]?.created_at_ms);
  });

  it('publishes the live levels as snapshot values', async () => {
    const list = await client.getIndicatorSnapshots({
      instrumentId: 'mock:perp:BTCUSD',
      timeframe: '15m',
    });
    expect(validator('IndicatorSnapshotList')(list)).toBe(true);
    const pivots = list.snapshots.find(s => s.plugin_id === 'pivot_points');
    const s1 = pivots?.aois.find(
      a => a.kind === 'PIVOT_S1' && a.state === 'active'
    );
    expect(pivots?.values.s1).toBe(s1?.lower);
    expect(pivots?.values.period_start_ms).toBe(s1?.created_at_ms);
  });

  it('serves no levels on timeframes the plugin does not support', async () => {
    const list = await client.getAois({
      instrumentId: 'mock:perp:BTCUSD',
      timeframe: '1s',
    });
    expect(list.aois).toEqual([]);
  });

  it('answers at_ms with only what was known then', async () => {
    const duration = TIMEFRAME_MS['5m'];
    const lastStart = floorToTimeframe('5m', NOW) - duration;
    const atMs = lastStart - 120 * duration;
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
    // Levels that expired after `atMs` were still live at that time.
    expect(past.aois).toHaveLength(7);
    for (const aoi of past.aois) {
      expect(aoi.aoi_id).toMatch(/^demo-pivot-prev-/);
      expect(aoi.state).toBe('active');
      expect(aoi.invalidated_at_ms).toBeNull();
    }
    const future = await client.getAois({
      instrumentId: 'mock:perp:BTCUSD',
      timeframe: '5m',
      atMs: NOW + 1,
    });
    expect(future.as_of_ms).toBe(latest.as_of_ms);
  });

  it('serves a contract-valid volume delta series', async () => {
    const series = await client.getIndicatorSeries({
      instrumentId: 'mock:perp:BTCUSD',
      timeframe: '5m',
      pluginId: 'volume_delta',
    });
    expect(validator('IndicatorSeries')(series)).toBe(true);
    expect(series.points.length).toBeGreaterThan(100);
  });
});
