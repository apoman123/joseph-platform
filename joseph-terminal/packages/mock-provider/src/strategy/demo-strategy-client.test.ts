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
    if (name.endsWith('.schema.json'))
      ajv.addSchema(JSON.parse(readFileSync(join(SCHEMAS, name), 'utf8')));
  }
  const validate = ajv.getSchema(
    `https://schemas.joseph.local/v1/${file}#/$defs/${def}`
  );
  return value => (validate?.(value) ? '' : JSON.stringify(validate?.errors));
}

/**
 * Loads Long A from the shared fixtures.
 * @return Definition.
 */
function longA(): StrategyDefinition {
  const fixture = JSON.parse(
    readFileSync(join(FIXTURES, 'strategy.fixture.json'), 'utf8')
  ) as {valid: StrategyDefinition[]};
  const def = fixture.valid[0];
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
    const v1 = await client.createStrategy(longA());
    expect(validator('strategy.schema.json', 'StrategyVersion')(v1)).toBe('');
    const edited = {...longA(), name: 'Long A edited'};
    const v2 = await client.createVersion(v1.strategy_id, edited);
    expect(v2.version).toBe(2);
    const {versions} = await client.getStrategy(v1.strategy_id);
    expect(versions.map(v => v.version)).toEqual([2, 1]);
    expect(versions[1]?.definition.name).toBe(longA().name);
    await expect(client.createStrategy(longA())).rejects.toMatchObject({
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
    await expect(client.createStrategy(longA())).rejects.toMatchObject({
      body: {
        code: 'VALIDATION_FAILED',
        field_errors: [{field: '/when', message: 'bad'}],
      },
    });
  });

  it('activates one version and never AUTO_LIVE', async () => {
    const client = new DemoStrategyClient();
    await client.createStrategy(longA());
    await client.createVersion('discount-ote-reversal', longA());
    await client.activate('discount-ote-reversal', {version: 1, mode: 'PAPER'});
    const active = await client.activate('discount-ote-reversal', {
      version: 2,
      mode: 'ALERT_ONLY',
    });
    expect(active.state).toBe('ACTIVE');
    const {versions} = await client.getStrategy('discount-ote-reversal');
    expect(versions.filter(v => v.state === 'ACTIVE')).toHaveLength(1);
    await expect(
      client.activate('discount-ote-reversal', {
        version: 1,
        mode: 'AUTO_LIVE' as 'PAPER',
      })
    ).rejects.toMatchObject({body: {code: 'TRADE_MODE_DISABLED'}});
    expect((await client.pause('discount-ote-reversal')).state).toBe('PAUSED');
  });

  it('returns a canned backtest explicitly marked DEMO', async () => {
    const client = new DemoStrategyClient();
    await client.createStrategy(longA());
    const report = await client.backtest('discount-ote-reversal', {
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
    for (const signal of createDemoSignals(1_800_000_000_000))
      expect(check(signal)).toBe('');
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
});
