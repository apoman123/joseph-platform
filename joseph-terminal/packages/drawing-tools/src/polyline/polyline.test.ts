/**
 * @fileoverview Tests for the polyline tool.
 */
import {describe, expect, it} from 'vitest';
import {
  TRANSFORM,
  makeDoc,
  manifestProblems,
  renderOps,
} from '../testing/harness';
import {distanceToPath, polylineTool as tool} from '.';

describe('polyline', () => {
  const doc = makeDoc(tool, [
    {time_ms: 0, price: '150'},
    {time_ms: 600_000, price: '160'},
    {time_ms: 1_200_000, price: '140'},
  ]);
  it('has a valid manifest and is created click by click', () => {
    expect(manifestProblems(tool)).toEqual([]);
    expect(tool.manifest.creation).toBe('multi-click');
  });
  it('strokes every vertex and hits any segment or vertex', () => {
    const ops = renderOps(tool, doc);
    expect(ops.filter(o => o.op === 'lineTo')).toHaveLength(2);
    expect(tool.hitTest(doc, TRANSFORM, {x: 150, y: 250}, 6)).toEqual({
      part: 'body',
    });
    expect(tool.hitTest(doc, TRANSFORM, {x: 200, y: 300}, 6)).toEqual({
      part: 'handle',
      handleIndex: 2,
    });
    expect(distanceToPath({x: 0, y: 0}, [{x: 0, y: 0}])).toBe(Infinity);
  });
});
