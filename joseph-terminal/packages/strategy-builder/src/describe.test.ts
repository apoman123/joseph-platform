/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Natural-language rendering of rule nodes.
 */

import type {RuleNode} from '@joseph/contracts';
import {describe, expect, it} from 'vitest';
import {describeNode, describeTarget, describeTree} from './describe';
import {STRATEGY_TEMPLATES} from './templates';
import {testVocabulary} from './testing/vocabulary';

const VOCAB = testVocabulary();

/**
 * Lists the sentences of a template's top-level conditions.
 * @param index Template index.
 * @param withVocab Whether to name things from the test vocabulary.
 * @return Sentences in order.
 * @throws `Error` when the template is missing or has no group root.
 */
function sentences(index: number, withVocab: boolean): string[] {
  const def = STRATEGY_TEMPLATES[index]?.create();
  if (def?.when.op !== 'ALL') throw new Error('missing template');
  return def.when.children.map(child =>
    describeNode(child, withVocab ? VOCAB : undefined)
  );
}

describe('describeNode', () => {
  it('renders the template sentences with plugin labels', () => {
    expect(sentences(0, true)).toEqual([
      'Close crosses above EMA',
      'RSI on 1h is greater than 50',
      'Volume delta delta_pct is greater than 0',
      'Projected reward to risk after costs is at least 2.0R',
    ]);
    expect(sentences(2, true)).toEqual([
      'Touched a bullish S1 or S2 in the last 3 bars (wick)',
      'Bar starts between 09:30 and 11:30 (America/New_York), Mon–Fri',
      'Volume delta: method is taker_volume or trade_classification on 5m',
      'Projected reward to risk after costs is at least 1.5R',
    ]);
  });

  it('shows raw names when no plugin labels them', () => {
    expect(sentences(0, false)[0]).toBe('Close crosses above ema.ema');
    expect(sentences(2, false)[0]).toBe(
      'Touched a bullish PIVOT_S1 or PIVOT_S2 in the last 3 bars (wick)'
    );
  });

  it('renders zones, states, events and time windows', () => {
    const cases: Array<[RuleNode, string]> = [
      [
        {op: 'IN_ZONE', kinds: ['EXAMPLE_ZONE'], side: 'bullish', tf: '1h'},
        'Close is inside a bullish Zone on 1h',
      ],
      [
        {
          op: 'IN_ZONE',
          kinds: ['PIVOT_S1', 'PIVOT_S2'],
          tf: '4h',
          price_source: 'hl2',
          group: 'G',
        },
        'Bar midpoint is inside a S1 or S2 on 4h (group G)',
      ],
      [
        {
          op: 'STATE',
          plugin_id: 'example_zones',
          timeframes: ['1d', '4h', '1h'],
          where: [
            {field: 'last_break', any_of: ['up']},
            {field: 'armed', any_of: [true]},
          ],
          match: 'K_OF_N',
          k: 2,
        },
        'At least 2 of 1d, 4h and 1h: Example zones: last_break is up and armed is true',
      ],
      [
        {
          op: 'EVENT',
          name: 'EXAMPLE_BREAK_UP',
          source: 'example_zones',
          tf: '15m',
          within_bars: 0,
        },
        'Break up confirmed on 15m on this bar (from Example zones)',
      ],
      [
        {
          op: 'SEQUENCE',
          within_bars: 4,
          steps: [{name: 'EXAMPLE_ZONE_FORMED'}, {name: 'AOI_INVALIDATED'}],
        },
        'Zone then AOI invalidated within 4 bars',
      ],
      [
        {
          op: 'TIME_WINDOW',
          time_zone: 'Asia/Taipei',
          windows: [
            {start: '22:00', end: '02:00'},
            {start: '09:00', end: '10:00'},
          ],
          weekdays: [7, 6],
        },
        'Bar starts between 22:00 and 02:00 or between 09:00 and 10:00 (Asia/Taipei), Sat, Sun',
      ],
      [
        {
          op: 'TIME_WINDOW',
          time_zone: 'UTC',
          windows: [{start: '08:00', end: '16:00'}],
          weekdays: [1, 3, 5],
        },
        'Bar starts between 08:00 and 16:00 (UTC), Mon, Wed, Fri',
      ],
      [
        {
          op: 'COMPARE',
          left: {kind: 'market', field: 'oi_change_pct'},
          cmp: 'lt',
          right: {kind: 'const', value: '-5'},
        },
        'OI change % is less than -5',
      ],
    ];
    for (const [node, text] of cases) {
      expect(describeNode(node, VOCAB)).toBe(text);
    }
  });

  it('describes the risk target', () => {
    const pivot = STRATEGY_TEMPLATES[2]?.create();
    if (!pivot) throw new Error('missing template');
    expect(describeTarget(pivot.risk, VOCAB)).toBe(
      'Nearest P or R1 beyond the entry'
    );
    expect(describeTarget(pivot.risk)).toBe(
      'Nearest PIVOT_P or PIVOT_R1 beyond the entry'
    );
    expect(
      describeTarget({
        min_rr: '2',
        stop_model: 'ATR',
        target_model: 'FIXED_R',
        fixed_r: '2',
      })
    ).toBe('Fixed 2R target');
  });

  it('outlines a whole tree', () => {
    const def = STRATEGY_TEMPLATES[1]?.create();
    if (!def) throw new Error('missing template');
    const outline = describeTree(def.when, VOCAB).split('\n');
    expect(outline).toEqual([
      'All of these are true',
      '  RSI crosses below 70',
      '  True at least once in the last 3 bars',
      '    Close is greater than Bollinger Bands upper',
      '  This is not true',
      '    OI change % is less than -5',
    ]);
  });
});
