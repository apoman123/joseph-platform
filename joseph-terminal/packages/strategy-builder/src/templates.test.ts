/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Templates match the shared fixtures and the JSON Schema.
 */

import {describe, expect, it} from 'vitest';
import {STRATEGY_TEMPLATES} from './templates';
import {readFixture, schemaErrors} from './testing/schema';
import {testVocabulary} from './testing/vocabulary';
import {validateDefinition} from './validate';

describe('strategy templates', () => {
  it('the three examples are identical to the contract fixtures', () => {
    const fixture = readFixture('strategy');
    const ids = [
      'ema-trend-long',
      'rsi-reversion-short',
      'pivot-bounce-long',
    ] as const;
    ids.forEach((id, i) => {
      const template = STRATEGY_TEMPLATES.find(t => t.id === id);
      expect(template?.create()).toEqual(fixture.valid[i]);
    });
    expect(STRATEGY_TEMPLATES.map(t => t.title)).toEqual([
      'EMA trend (long)',
      'RSI reversion (short)',
      'Pivot bounce (long)',
      'Blank long',
      'Blank short',
    ]);
  });

  for (const template of STRATEGY_TEMPLATES) {
    it(`${template.id} validates against the JSON Schema and client rules`, () => {
      const definition = template.create();
      expect(
        schemaErrors('strategy.schema.json', 'StrategyDefinition', definition)
      ).toEqual([]);
      expect(validateDefinition(definition).errors).toEqual([]);
      const checked = validateDefinition(definition, testVocabulary());
      expect(checked.errors).toEqual([]);
      expect(checked.warnings).toEqual([]);
    });
  }

  it('blank templates use an ATR stop and a fixed 2R target', () => {
    const blank = STRATEGY_TEMPLATES.find(t => t.id === 'blank-short');
    expect(blank?.create().risk).toEqual({
      min_rr: '2',
      stop_model: 'ATR',
      stop_buffer_atr: '1.5',
      target_model: 'FIXED_R',
      fixed_r: '2',
    });
  });

  it('returns independent copies', () => {
    const [first] = STRATEGY_TEMPLATES;
    if (!first) throw new Error('no templates');
    const a = first.create();
    a.name = 'changed';
    expect(first.create().name).not.toBe('changed');
  });
});
