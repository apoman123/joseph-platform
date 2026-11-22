/**
 * @fileoverview Tests for the rectangle tool.
 */
import {describe, expect, it} from 'vitest';
import {
  TRANSFORM,
  makeDoc,
  manifestProblems,
  renderOps,
} from '../testing/harness';
import {rectangleTool as tool} from '.';

describe('rectangle', () => {
  const doc = makeDoc(tool, [
    {time_ms: 600_000, price: '160'},
    {time_ms: 1_200_000, price: '150'},
  ]);
  it('has a valid manifest', () => expect(manifestProblems(tool)).toEqual([]));
  it('fills and strokes the normalized rectangle', () => {
    const ops = renderOps(tool, doc);
    expect(ops.find(o => o.op === 'fillRect')?.args).toEqual([
      100, 200, 100, 50,
    ]);
    expect(ops.some(o => o.op === 'strokeRect')).toBe(true);
  });
  it('hits inside when filled and misses outside', () => {
    expect(tool.hitTest(doc, TRANSFORM, {x: 150, y: 225}, 6)).toEqual({
      part: 'body',
    });
    expect(tool.hitTest(doc, TRANSFORM, {x: 400, y: 225}, 6)).toBeNull();
  });
});
