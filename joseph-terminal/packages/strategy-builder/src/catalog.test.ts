/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview The add-condition catalog offers and defaults nodes from
 * the vocabulary.
 */

import {describe, expect, it} from 'vitest';
import {NODE_CATALOG, catalogEntry, defaultLeaf} from './catalog';
import {STRATEGY_TEMPLATES} from './templates';
import {schemaErrors} from './testing/schema';
import {testVocabulary} from './testing/vocabulary';
import {validateDefinition} from './validate';
import {EMPTY_VOCABULARY} from './vocabulary';

const VOCAB = testVocabulary();

describe('node catalog', () => {
  it('hides indicator-fed nodes until plugins publish their inputs', () => {
    const offered = (vocab: typeof VOCAB) =>
      NODE_CATALOG.filter(e => e.available(vocab)).map(e => e.op);
    expect(offered(EMPTY_VOCABULARY)).toEqual([
      'ALL',
      'ANY',
      'NOT',
      'WITHIN_BARS',
      'PERSIST_FOR',
      'COMPARE',
      'IN_RANGE',
      'CROSSES',
      'TIME_WINDOW',
      'RISK_REWARD',
    ]);
    expect(offered(VOCAB)).toHaveLength(NODE_CATALOG.length);
  });

  it('takes defaults from the vocabulary', () => {
    expect(catalogEntry('STATE')?.create('LONG', '5m', VOCAB)).toEqual({
      op: 'STATE',
      plugin_id: 'volume_delta',
      timeframes: ['5m'],
      where: [{field: 'method', any_of: ['taker_volume']}],
    });
    expect(catalogEntry('IN_ZONE')?.create('SHORT', '1h', VOCAB)).toEqual({
      op: 'IN_ZONE',
      kinds: ['PIVOT_P'],
      side: 'bearish',
      tf: '1h',
    });
    expect(catalogEntry('EVENT')?.create('LONG', '5m', VOCAB)).toMatchObject({
      name: 'EXAMPLE_ZONE_FORMED',
    });
    expect(defaultLeaf('SHORT')).toEqual({
      op: 'COMPARE',
      left: {kind: 'candle', field: 'close'},
      cmp: 'lt',
      right: {kind: 'indicator', plugin_id: 'ema', field: 'ema'},
    });
  });

  it('creates schema-valid nodes that pass client validation', () => {
    const base = STRATEGY_TEMPLATES[0]?.create();
    if (!base) throw new Error('missing template');
    for (const entry of NODE_CATALOG) {
      for (const direction of ['LONG', 'SHORT'] as const) {
        const node = entry.create(direction, '15m', VOCAB);
        const def = {...base, direction, when: node};
        expect(
          schemaErrors('strategy.schema.json', 'StrategyDefinition', def)
        ).toEqual([]);
        expect(validateDefinition(def, VOCAB).errors).toEqual([]);
      }
    }
  });
});
