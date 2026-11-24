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
import {validateDefinition} from './validate';

describe('strategy templates', () => {
  it('Long A and Short B are identical to the contract fixtures', () => {
    const fixture = readFixture('strategy');
    const longA = STRATEGY_TEMPLATES.find(t => t.id === 'long-a')?.create();
    const shortB = STRATEGY_TEMPLATES.find(t => t.id === 'short-b')?.create();
    expect(longA).toEqual(fixture.valid[0]);
    expect(shortB).toEqual(fixture.valid[1]);
  });

  for (const template of STRATEGY_TEMPLATES) {
    it(`${template.id} validates against the JSON Schema and client rules`, () => {
      const definition = template.create();
      expect(
        schemaErrors('strategy.schema.json', 'StrategyDefinition', definition)
      ).toEqual([]);
      expect(validateDefinition(definition).errors).toEqual([]);
    });
  }

  it('returns independent copies', () => {
    const [first] = STRATEGY_TEMPLATES;
    if (!first) throw new Error('no templates');
    const a = first.create();
    a.name = 'changed';
    expect(first.create().name).not.toBe('changed');
  });
});
