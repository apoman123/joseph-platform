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
import Ajv2020 from 'ajv/dist/2020';
import addFormats from 'ajv-formats';
import {describe, expect, it} from 'vitest';
import {createDemoSignals} from './demo-signals';
import {DemoStrategyClient} from './demo-strategy-client';
import {sha256Hex} from './sha256';

const SCHEMAS = join(__dirname, '..', '..', '..', 'contracts', 'schemas');

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
  it('lists the built-in strategies with their declared inputs', async () => {
    const client = new DemoStrategyClient();
    const {scripts, runtime_enabled} = await client.listStrategyScripts();
    expect(runtime_enabled).toBe(false);
    expect(scripts.map(s => s.script_id)).toEqual([
      'builtin:ema-trend-long',
      'builtin:rsi-reversion-short',
      'builtin:pivot-bounce-long',
    ]);
    expect(scripts[0]?.inputs.ema_length?.default).toBe(20);
    expect(await client.getStrategySource('builtin:ema-trend-long')).toMatch(
      /^# DEMO/
    );
  });

  it('labels synthetic backtests as DEMO and pairs fills into trades', async () => {
    const client = new DemoStrategyClient({now: () => 1_800_000_000_000});
    const result = await client.backtestStrategy(
      'builtin:rsi-reversion-short',
      {
        instrument_id: 'mock:perp:BTCUSD',
        tf: '5m',
        limit: 200,
      }
    );
    expect(result.data_source).toBe('DEMO');
    expect(result.feed_state).toBe('DEMO');
    expect(result.bars_count).toBe(200);
    expect(result.trades?.length).toBeGreaterThan(0);
    expect(result.trades?.every(t => t.direction === 'SHORT')).toBe(true);
    expect(result.fills.length).toBeGreaterThanOrEqual(
      2 * (result.metrics.trade_count ?? 0)
    );
    expect(result.equity_curve).toHaveLength(200);
  });

  it('refuses AUTO_LIVE and keeps activation state in memory', async () => {
    const client = new DemoStrategyClient({now: () => 1_800_000_000_000});
    await expect(
      client.createActivation({
        script_id: 'builtin:ema-trend-long',
        instrument_ids: ['mock:perp:BTCUSD'],
        tf: '15m',
        mode: 'AUTO_LIVE',
      })
    ).rejects.toMatchObject({body: {code: 'TRADE_MODE_DISABLED'}});
    const created = await client.createActivation({
      script_id: 'builtin:ema-trend-long',
      instrument_ids: ['mock:perp:BTCUSD'],
      tf: '15m',
      mode: 'PAPER',
    });
    expect(created.strategy_version_id).toBe('builtin:ema-trend-long:vdemo');
    expect((await client.pauseActivation(created.activation_id)).state).toBe(
      'PAUSED'
    );
    expect((await client.resumeActivation(created.activation_id)).state).toBe(
      'ACTIVE'
    );
    await client.deleteActivation(created.activation_id);
    expect((await client.listActivations()).activations).toEqual([]);
  });

  it('copies a built-in into an owned DEMO strategy', async () => {
    const client = new DemoStrategyClient();
    const copy = await client.copyStrategy(
      'builtin:pivot-bounce-long',
      'my-pivot'
    );
    expect(copy.kind).toBe('strategy');
    const {scripts} = await client.listStrategyScripts();
    expect(scripts.at(-1)).toMatchObject({
      script_id: 'my-pivot',
      origin: 'tenant',
    });
    await expect(
      client.copyStrategy('builtin:pivot-bounce-long', 'my-pivot')
    ).rejects.toMatchObject({body: {code: 'CONFLICT'}});
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
      (await client.listSignals({kinds: ['POSITION_EXITED']})).signals
    ).toHaveLength(1);
  });

  it('signals come from the built-in strategies', () => {
    const signals = createDemoSignals(1_800_000_000_000);
    expect(signals.map(s => s.strategy_id)).toEqual([
      'builtin:pivot-bounce-long',
      'builtin:rsi-reversion-short',
      'builtin:ema-trend-long',
    ]);
    for (const signal of signals) {
      expect(signal.strategy_version_id).toBe(`${signal.strategy_id}:vdemo`);
      expect(signal.condition).toBeNull();
    }
    expect(signals.map(s => s.kind)).toEqual([
      'ENTRY_CONFIRMED',
      'POSITION_EXITED',
      'ENTRY_CONFIRMED',
    ]);
  });
});
