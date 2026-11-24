/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Tests for the DEMO strategy client and fixtures.
 */

import {readFileSync, readdirSync} from 'node:fs';
import {join} from 'node:path';
import type {StrategyDefinition} from '@joseph/contracts';
import Ajv2020 from 'ajv/dist/2020';
import addFormats from 'ajv-formats';
import {describe, expect, it} from 'vitest';
import {createDemoSignals} from './demo-signals';
import {DemoStrategyClient} from './demo-strategy-client';
import {sha256Hex} from './sha256';

const SCHEMAS = join(__dirname, '..', '..', '..', 'contracts', 'schemas');
const FIXTURES = join(__dirname, '..', '..', '..', 'contracts', 'fixtures');

/**
 * Builds a validator for one contract definition.
 * @param file Schema file.
 * @param def Definition name.
 * @return Validate function returning error text.
 */
function validator(file: string, def: string): (value: unknown) => string {
  const ajv = new Ajv2020({strict: false, allErrors: true});
  addFormats(ajv);
  for (const name of readdirSync(SCHEMAS)) {
    if (name.endsWith('.schema.json')) {
      ajv.addSchema(JSON.parse(readFileSync(join(SCHEMAS, name), 'utf8')));
    }
  }
  const validate = ajv.getSchema(
    `https://schemas.joseph.local/v1/${file}#/$defs/${def}`
  );
  return value => (validate?.(value) ? '' : JSON.stringify(validate?.errors));
}

/**
 * Loads the valid strategies of the shared fixtures (the example templates).
 * @return Strategy definitions.
 */
function fixtureStrategies(): StrategyDefinition[] {
  const fixture = JSON.parse(
    readFileSync(join(FIXTURES, 'strategy.fixture.json'), 'utf8')
  ) as {valid: StrategyDefinition[]};
  return fixture.valid;
}

/**
 * Loads the EMA trend example, the first valid strategy in the fixtures.
 * @return Strategy definition.
 * @throws Error when the fixture has no valid strategy.
 */
function emaTrend(): StrategyDefinition {
  const def = fixtureStrategies()[0];
  if (!def) throw new Error('missing fixture');
  return def;
}

describe('sha256Hex', () => {
  it('matches known vectors', () => {
    expect(sha256Hex('')).toBe(
      'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'
    );
    expect(sha256Hex('abc')).toBe(
      'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad'
    );
  });
});

describe('DemoStrategyClient', () => {
  it('creates immutable versions with schema-valid records', async () => {
    const client = new DemoStrategyClient({now: () => 1_800_000_000_000});
    const v1 = await client.createStrategy(emaTrend());
    expect(validator('strategy.schema.json', 'StrategyVersion')(v1)).toBe('');
    const edited = {...emaTrend(), name: 'EMA trend edited'};
    const v2 = await client.createVersion(v1.strategy_id, edited);
    expect(v2.version).toBe(2);
    const {versions} = await client.getStrategy(v1.strategy_id);
    expect(versions.map(v => v.version)).toEqual([2, 1]);
    expect(versions[1]?.definition.name).toBe(emaTrend().name);
    await expect(client.createStrategy(emaTrend())).rejects.toMatchObject({
      body: {code: 'CONFLICT'},
    });
  });

  it('rejects invalid definitions using the injected validator', async () => {
    const client = new DemoStrategyClient({
      validate: () => ({
        valid: false,
        errors: [{path: '/when', code: 'x', message: 'bad'}],
        warnings: [],
        required_plugins: [],
        required_timeframes: [],
        node_count: 0,
        max_depth: 0,
      }),
    });
    await expect(client.createStrategy(emaTrend())).rejects.toMatchObject({
      body: {
        code: 'VALIDATION_FAILED',
        field_errors: [{field: '/when', message: 'bad'}],
      },
    });
  });

  it('activates one version and never AUTO_LIVE', async () => {
    const client = new DemoStrategyClient();
    await client.createStrategy(emaTrend());
    await client.createVersion('ema-trend-long', emaTrend());
    await client.activate('ema-trend-long', {version: 1, mode: 'PAPER'});
    const active = await client.activate('ema-trend-long', {
      version: 2,
      mode: 'ALERT_ONLY',
    });
    expect(active.state).toBe('ACTIVE');
    const {versions} = await client.getStrategy('ema-trend-long');
    expect(versions.filter(v => v.state === 'ACTIVE')).toHaveLength(1);
    await expect(
      client.activate('ema-trend-long', {
        version: 1,
        // Deliberately outside the contract type, as an untyped caller sends.
        mode: 'AUTO_LIVE' as 'PAPER',
      })
    ).rejects.toMatchObject({body: {code: 'TRADE_MODE_DISABLED'}});
    expect((await client.pause('ema-trend-long')).state).toBe('PAUSED');
  });

  it('returns a canned backtest explicitly marked DEMO', async () => {
    const client = new DemoStrategyClient();
    await client.createStrategy(emaTrend());
    const report = await client.backtest('ema-trend-long', {
      version: 1,
      instrument_id: 'mock:perp:BTCUSD',
      from_ms: 1_800_000_000_000,
      to_ms: 1_800_600_000_000,
    });
    expect(report.feed_state).toBe('DEMO');
    expect(report.assumptions[0]).toMatch(/^DEMO/);
    expect(validator('strategy.schema.json', 'BacktestReport')(report)).toBe(
      ''
    );
  });

  it('serves schema-valid fixture signals newest first', async () => {
    const check = validator('signal.schema.json', 'Signal');
    for (const signal of createDemoSignals(1_800_000_000_000)) {
      expect(check(signal)).toBe('');
    }
    const client = new DemoStrategyClient({now: () => 1_800_000_000_000});
    const {signals} = await client.listSignals({});
    expect(signals.map(s => s.signal_id)).toEqual([
      'demo-signal-1',
      'demo-signal-2',
      'demo-signal-3',
    ]);
    expect(
      (await client.listSignals({kinds: ['ENTRY_BLOCKED']})).signals
    ).toHaveLength(1);
  });

  it('signals come from the example strategies', () => {
    const ids = fixtureStrategies().map(def => def.strategy_id);
    const signals = createDemoSignals(1_800_000_000_000);
    expect(signals.map(s => s.strategy_id)).toEqual([
      'pivot-bounce-long',
      'rsi-reversion-short',
      'ema-trend-long',
    ]);
    for (const signal of signals) {
      expect(ids).toContain(signal.strategy_id);
      expect(signal.strategy_version_id).toBe(`${signal.strategy_id}:v1`);
      expect(signal.group_ids).toEqual([]);
    }
    expect(signals[0]?.aoi_ids).toEqual(['demo-pivot-s1-5m']);
  });

  it('fills the signal bar volume from the DEMO venue candle', () => {
    for (const signal of createDemoSignals(1_800_000_000_000)) {
      expect(Number(signal.bar_base_volume)).toBeGreaterThan(0);
      expect(Number(signal.bar_quote_volume)).toBeGreaterThan(
        Number(signal.bar_base_volume)
      );
    }
  });
});
