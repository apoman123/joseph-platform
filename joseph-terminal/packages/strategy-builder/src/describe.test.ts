/**
 * @fileoverview Natural-language rendering of rule nodes.
 */
import {describe, expect, it} from 'vitest';
import {describeNode, describeTree} from './describe';
import {STRATEGY_TEMPLATES} from './templates';

describe('describeNode', () => {
  it('renders the reference sentences', () => {
    expect(
      describeNode({op: 'BIAS', timeframes: ['4h'], value: 'BULLISH'})
    ).toBe('4h bias is bullish');
    expect(
      describeNode({
        op: 'PRICE_IN_ZONE',
        zone: 'DISCOUNT',
        tf: '1h',
        range_group: 'A',
      })
    ).toBe('Close is inside the 1h discount zone (range group A)');
    expect(
      describeNode({
        op: 'AOI_TOUCH',
        kinds: ['FVG', 'OB', 'BREAKER'],
        side: 'bullish',
        within_bars: 3,
        touch: 'WICK',
      })
    ).toBe('Touched a bullish FVG, OB or breaker in the last 3 bars (wick)');
    expect(
      describeNode({
        op: 'SEQUENCE',
        within_bars: 4,
        steps: [{name: 'SSL_SWEEP'}, {name: 'SELLER_FAILURE'}],
      })
    ).toBe('SSL sweep then seller failure within 4 bars');
  });

  it('renders K-of-N bias, comparisons and sessions', () => {
    expect(
      describeNode({
        op: 'BIAS',
        timeframes: ['1d', '4h', '1h'],
        value: 'BEARISH',
        match: 'K_OF_N',
        k: 2,
      })
    ).toBe('At least 2 of 1d, 4h and 1h bias are bearish');
    expect(
      describeNode({
        op: 'COMPARE',
        left: {kind: 'market', field: 'oi_change_pct'},
        cmp: 'lt',
        right: {kind: 'const', value: '-5'},
      })
    ).toBe('OI change % is less than -5');
    expect(
      describeNode({op: 'SESSION', sessions: ['LONDON', 'NEW_YORK']})
    ).toBe('Bar starts in the London or New York session');
    expect(
      describeNode({op: 'EVENT', name: 'BOS_BULL', tf: '15m', within_bars: 0})
    ).toBe('Bullish BOS confirmed on 15m on this bar');
  });

  it('outlines a whole tree', () => {
    const def = STRATEGY_TEMPLATES[1]?.create();
    if (!def) throw new Error('missing template');
    const outline = describeTree(def.when).split('\n');
    expect(outline[0]).toBe('All of these are true');
    expect(outline).toContain('  This is not true');
    expect(outline).toContain('    OI change % is less than -5');
  });
});
