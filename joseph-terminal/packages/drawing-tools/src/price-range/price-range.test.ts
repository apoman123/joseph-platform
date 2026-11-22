/**
 * @fileoverview Tests for the measure tool.
 */
import {describe, expect, it} from 'vitest';
import {
  TRANSFORM,
  makeDoc,
  manifestProblems,
  renderOps,
  texts,
} from '../testing/harness';
import {measure, priceRangeTool as tool} from '.';

describe('price-range', () => {
  it('has a valid manifest', () => expect(manifestProblems(tool)).toEqual([]));
  it('measures price, percent, bars and duration', () => {
    expect(
      measure(
        {time_ms: 0, price: '100'},
        {time_ms: 90 * 60_000, price: '102.5'},
        60_000
      )
    ).toEqual({
      delta: '2.5',
      pct: '2.50',
      bars: 90,
      duration: '1h 30m',
    });
  });
  it('labels a falling range with a minus sign', () => {
    const doc = makeDoc(tool, [
      {time_ms: 0, price: '160'},
      {time_ms: 600_000, price: '150'},
    ]);
    expect(texts(renderOps(tool, doc))[0]).toBe('−10.0 (-6.25%)');
    expect(tool.hitTest(doc, TRANSFORM, {x: 50, y: 225}, 6)).toEqual({
      part: 'body',
    });
  });
});
