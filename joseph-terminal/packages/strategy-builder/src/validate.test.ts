/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Client validation mirrors the schema and cross-field rules.
 */

import type {StrategyDefinition} from '@joseph/contracts';
import {describe, expect, it} from 'vitest';
import {STRATEGY_TEMPLATES} from './templates';
import {readFixture} from './testing/schema';
import {nodeIdAtPointer, validateDefinition} from './validate';

/**
 * Builds Long A with a replaced rule tree.
 * @param when Rule tree JSON.
 * @return Definition.
 */
function withWhen(when: unknown): StrategyDefinition {
  const def = STRATEGY_TEMPLATES[0]?.create();
  if (!def) throw new Error('missing template');
  return {...def, when: when as StrategyDefinition['when']};
}

/**
 * Returns error codes for a definition.
 * @param def Definition.
 * @return Codes.
 */
function codes(def: unknown): string[] {
  return validateDefinition(def).errors.map(e => e.code);
}

describe('validateDefinition', () => {
  it('reports dependencies of Long A', () => {
    const result = validateDefinition(STRATEGY_TEMPLATES[0]?.create());
    expect(result.valid).toBe(true);
    expect(result.requiredPlugins).toEqual([
      'daily_bias',
      'fvg_ifvg',
      'liquidity_sweeps',
      'order_breaker_blocks',
      'order_flow_pressure',
      'premium_discount_ote',
    ]);
    expect(result.requiredTimeframes).toEqual(['5m', '1h', '4h']);
    expect(result.nodeCount).toBe(7);
    expect(result.maxDepth).toBe(2);
  });

  it('rejects every invalid fixture', () => {
    for (const invalid of readFixture('strategy').invalid) {
      expect(validateDefinition(invalid).valid).toBe(false);
    }
  });

  it('enforces k <= n for K_OF_N bias', () => {
    expect(
      codes(
        withWhen({
          op: 'BIAS',
          timeframes: ['4h'],
          value: 'BULLISH',
          match: 'K_OF_N',
          k: 2,
        })
      )
    ).toContain('k_exceeds_n');
  });

  it('requires sequence windows to fit the steps', () => {
    const def = withWhen({
      op: 'SEQUENCE',
      within_bars: 1,
      steps: [{name: 'SSL_SWEEP'}, {name: 'SELLER_FAILURE'}],
    });
    expect(codes(def)).toContain('window_too_small');
  });

  it('requires range-group members to share a timeframe', () => {
    const def = withWhen({
      op: 'ALL',
      children: [
        {op: 'PRICE_IN_ZONE', zone: 'DISCOUNT', tf: '1h', range_group: 'A'},
        {op: 'PRICE_IN_ZONE', zone: 'OTE', tf: '4h', range_group: 'A'},
      ],
    });
    const result = validateDefinition(def);
    expect(
      result.errors.find(e => e.code === 'range_group_timeframe')?.path
    ).toBe('/when/children/1/tf');
  });

  it('rejects unsupported nodes instead of skipping them', () => {
    const def = withWhen({op: 'ALL', children: [{op: 'RSI_ABOVE', value: 70}]});
    expect(validateDefinition(def).errors[0]).toMatchObject({
      path: '/when/children/0/op',
      code: 'unsupported_node',
    });
  });

  it('rejects unknown fields and duplicate ids', () => {
    const def = withWhen({
      op: 'ALL',
      children: [
        {op: 'RISK_REWARD', id: 'x', min_rr: '2', extra: 1},
        {op: 'RISK_REWARD', id: 'x', min_rr: '2'},
      ],
    });
    expect(codes(def)).toEqual(
      expect.arrayContaining(['additionalProperties', 'duplicate_id'])
    );
  });

  it('warns about AUTO_LIVE without blocking', () => {
    const def = {
      ...STRATEGY_TEMPLATES[0]?.create(),
      execution_mode: 'AUTO_LIVE',
    };
    const result = validateDefinition(def);
    expect(result.valid).toBe(true);
    expect(result.warnings.map(w => w.code)).toContain('auto_live');
  });
});

describe('nodeIdAtPointer', () => {
  it('maps a server error pointer to the deepest node id', () => {
    const def = STRATEGY_TEMPLATES[0]?.create();
    expect(nodeIdAtPointer(def, '/when/children/4/within_bars')).toBe('seq');
    expect(nodeIdAtPointer(def, '/when')).toBe('root');
    expect(nodeIdAtPointer(def, '/risk/min_rr')).toBeNull();
  });
});
