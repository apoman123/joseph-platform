/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview v1 → v2 migration preserves every mapped condition.
 */

import type {RuleNode} from '@joseph/contracts';
import {describe, expect, it} from 'vitest';
import {walk} from './ast';
import {diffDefinitions, exportDefinition, importDefinition} from './io';
import {migrateV1, type StrategyConfigV1} from './migrate-v1';
import {STRATEGY_TEMPLATES} from './templates';
import {schemaErrors} from './testing/schema';

/** The starter's DEFAULT_STRATEGY. */
const STARTER: StrategyConfigV1 = {
  version: 1,
  id: 'user-long-v1',
  name: 'Discount + OTE + AOI Long',
  enabled: true,
  side: 'long',
  timeframe: '5m',
  htfBias: 'bullish',
  requiredZones: ['discount', 'ote'],
  aoiAny: ['fvg', 'ob', 'breaker'],
  aoiLookbackBars: 3,
  triggerSequence: ['ssl_sweep', 'seller_failure'],
  triggerWindowBars: 3,
  minRR: 2,
  cooldownBars: 12,
};

/**
 * Collects nodes by operator.
 * @param root Root node.
 * @return Map from operator to its nodes in pre-order.
 */
function byOp(root: RuleNode): Map<string, RuleNode[]> {
  const out = new Map<string, RuleNode[]>();
  walk(root, node => out.set(node.op, [...(out.get(node.op) ?? []), node]));
  return out;
}

describe('migrateV1', () => {
  it('maps every starter condition and stays alert-only', () => {
    const {definition, report} = migrateV1(STARTER);
    expect(
      schemaErrors('strategy.schema.json', 'StrategyDefinition', definition)
    ).toEqual([]);
    const nodes = byOp(definition.when);
    expect(nodes.get('BIAS')?.[0]).toMatchObject({value: 'BULLISH'});
    expect(
      nodes.get('PRICE_IN_ZONE')?.map(n => (n as {zone: string}).zone)
    ).toEqual(['DISCOUNT', 'OTE']);
    expect(
      new Set(
        nodes
          .get('PRICE_IN_ZONE')
          ?.map(n => (n as {range_group?: string}).range_group)
      )
    ).toEqual(new Set(['A']));
    expect(nodes.get('AOI_TOUCH')?.[0]).toMatchObject({
      kinds: ['FVG', 'OB', 'BREAKER'],
      side: 'bullish',
      within_bars: 3,
    });
    expect(nodes.get('SEQUENCE')?.[0]).toMatchObject({
      within_bars: 3,
      steps: [{name: 'SSL_SWEEP'}, {name: 'SELLER_FAILURE'}],
    });
    expect(nodes.get('RISK_REWARD')?.[0]).toMatchObject({min_rr: '2'});
    expect(definition.risk.min_rr).toBe('2');
    expect(definition.notification.cooldown_bars).toBe(12);
    expect(definition.execution_mode).toBe('ALERT_ONLY');
    expect(definition.strategy_id).toBe('user-long-v1');
    expect(report.unmapped.some(u => u.startsWith('enabled'))).toBe(true);
    expect(report.assumptions.length).toBeGreaterThan(0);
  });

  it('reports ambiguous liquidity AOIs instead of guessing', () => {
    const {definition, report} = migrateV1({...STARTER, aoiAny: ['liquidity']});
    expect(byOp(definition.when).get('AOI_TOUCH')).toBeUndefined();
    expect(report.unmapped.some(u => u.includes('liquidity'))).toBe(true);
  });

  it('maps short rules and single triggers', () => {
    const {definition} = migrateV1({
      ...STARTER,
      side: 'short',
      htfBias: 'bearish',
      triggerSequence: ['bsl_sweep'],
    });
    expect(definition.direction).toBe('SHORT');
    expect(byOp(definition.when).get('EVENT')?.[0]).toMatchObject({
      name: 'BSL_SWEEP',
      within_bars: 0,
    });
    expect(byOp(definition.when).get('AOI_TOUCH')?.[0]).toMatchObject({
      side: 'bearish',
    });
  });

  it('slugs ids, trimming dash runs in linear time', () => {
    const id = (value: string): string =>
      migrateV1({...STARTER, id: value}).definition.strategy_id;
    expect(id('  My Long_v1!! ')).toBe('my-long-v1');
    expect(id('--a--b--')).toBe('a--b');
    expect(id('***')).toBe('migrated-strategy');
    // An inner dash run made the old `-+$` trim quadratic (~4 s here).
    const started = performance.now();
    expect(id(`x${'-'.repeat(80_000)}y`)).toBe(`x${'-'.repeat(62)}`);
    expect(performance.now() - started).toBeLessThan(1_000);
  });

  it('preserves conditions across random v1 configs (property)', () => {
    const zones = ['discount', 'premium', 'ote'];
    const aois = ['fvg', 'ifvg', 'ob', 'breaker'];
    const triggers = [
      'ssl_sweep',
      'bsl_sweep',
      'seller_failure',
      'buyer_failure',
      'bullish_bos',
    ];
    let seed = 3;
    const rand = (n: number) => {
      seed = (seed * 1103515245 + 12345) % 2147483648;
      return seed % n;
    };
    for (let i = 0; i < 150; i += 1) {
      const config: StrategyConfigV1 = {
        ...STARTER,
        requiredZones: zones.filter(() => rand(2) === 1),
        aoiAny: aois.filter(() => rand(2) === 1),
        triggerSequence: Array.from(
          {length: rand(4)},
          () => triggers[rand(triggers.length)] ?? 'ssl_sweep'
        ),
        triggerWindowBars: 1 + rand(6),
        minRR: 1 + rand(5),
        htfBias: (['bullish', 'bearish', 'any'] as const)[rand(3)],
      };
      const {definition} = migrateV1(config);
      expect(
        schemaErrors('strategy.schema.json', 'StrategyDefinition', definition)
      ).toEqual([]);
      const nodes = byOp(definition.when);
      expect(nodes.get('PRICE_IN_ZONE')?.length ?? 0).toBe(
        config.requiredZones?.length ?? 0
      );
      expect(nodes.has('BIAS')).toBe(config.htfBias !== 'any');
      expect(nodes.has('AOI_TOUCH')).toBe((config.aoiAny?.length ?? 0) > 0);
      const steps = config.triggerSequence?.length ?? 0;
      expect(nodes.has('SEQUENCE')).toBe(steps >= 2);
      expect(nodes.has('EVENT')).toBe(steps === 1);
    }
  });
});

describe('import / export', () => {
  it('round-trips a v2 definition', () => {
    const def = STRATEGY_TEMPLATES[0]?.create();
    if (!def) throw new Error('missing template');
    const result = importDefinition(exportDefinition(def));
    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.definition).toEqual(def);
      expect(result.migration).toBeNull();
    }
  });

  it('migrates starter v1 JSON on import', () => {
    const result = importDefinition(JSON.stringify(STARTER));
    expect(
      result.ok && result.migration !== null && result.validation.valid
    ).toBe(true);
  });

  it('rejects unsupported versions and bad JSON', () => {
    expect(importDefinition('{"schema_version": 3}')).toMatchObject({
      ok: false,
    });
    expect(importDefinition('not json')).toMatchObject({
      ok: false,
      error: 'The file is not valid JSON.',
    });
  });

  it('diffs two versions', () => {
    const a = STRATEGY_TEMPLATES[0]?.create();
    const b = STRATEGY_TEMPLATES[0]?.create();
    if (!a || !b) throw new Error('missing template');
    b.name = 'Long A v2';
    b.notification.cooldown_bars = 6;
    delete b.setup;
    expect(diffDefinitions(a, b)).toEqual([
      {path: '/name', kind: 'changed', before: a.name, after: 'Long A v2'},
      {
        path: '/notification/cooldown_bars',
        kind: 'changed',
        before: 12,
        after: 6,
      },
      {path: '/setup', kind: 'removed', before: {expire_after_bars: 24}},
    ]);
  });
});
