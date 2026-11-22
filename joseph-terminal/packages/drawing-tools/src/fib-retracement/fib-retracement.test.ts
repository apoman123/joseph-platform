/**
 * @fileoverview Tests for the Fibonacci retracement tool.
 */
import {describe, expect, it} from 'vitest';
import {
  TRANSFORM,
  makeDoc,
  manifestProblems,
  renderOps,
  texts,
} from '../testing/harness';
import {fibRetracementTool as tool, retracementPrices} from '.';

describe('fib-retracement', () => {
  const doc = makeDoc(tool, [
    {time_ms: 0, price: '100'},
    {time_ms: 600_000, price: '200'},
  ]);
  it('has a valid manifest', () => expect(manifestProblems(tool)).toEqual([]));
  it('computes exact, tick-rounded levels (0 at end, 1 at start)', () => {
    expect(retracementPrices('100', '200', ['0', '0.618', '1'], '0.1')).toEqual(
      [
        {level: '0', price: '200'},
        {level: '0.618', price: '138.2'},
        {level: '1', price: '100'},
      ]
    );
  });
  it('labels every level', () => {
    expect(texts(renderOps(tool, doc))).toContain('0.618 (138.2)');
    expect(
      tool.hitTest(
        doc,
        TRANSFORM,
        {x: 30, y: TRANSFORM.priceToY(138.2) ?? 0},
        4
      )
    ).toEqual({part: 'body'});
  });
});
