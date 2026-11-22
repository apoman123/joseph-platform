/**
 * @fileoverview Tests for the arrow tool.
 */
import {describe, expect, it} from 'vitest';
import {
  TRANSFORM,
  makeDoc,
  manifestProblems,
  renderOps,
} from '../testing/harness';
import {arrowHead, arrowTool as tool} from '.';

describe('arrow', () => {
  const doc = makeDoc(tool, [
    {time_ms: 0, price: '150'},
    {time_ms: 1_000_000, price: '150'},
  ]);
  it('has a valid manifest', () => expect(manifestProblems(tool)).toEqual([]));
  it('fills an arrowhead at the tip', () => {
    const ops = renderOps(tool, doc);
    expect(ops.filter(o => o.op === 'closePath')).toHaveLength(1);
    const [left, right] = arrowHead({x: 0, y: 0}, {x: 100, y: 0}, 10);
    expect(left.x).toBeLessThan(100);
    expect(left.y).toBeCloseTo(-right.y);
  });
  it('hits the shaft', () => {
    expect(tool.hitTest(doc, TRANSFORM, {x: 80, y: 252}, 6)).toEqual({
      part: 'body',
    });
    expect(tool.hitTest(doc, TRANSFORM, {x: 80, y: 300}, 6)).toBeNull();
  });
});
