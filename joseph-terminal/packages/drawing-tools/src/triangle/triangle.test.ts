/**
 * @fileoverview Tests for the triangle tool.
 */
import {describe, expect, it} from 'vitest';
import {
  TRANSFORM,
  makeDoc,
  manifestProblems,
  renderOps,
} from '../testing/harness';
import {triangleTool as tool} from '.';

describe('triangle', () => {
  // Vertices (100, 100), (200, 100) and (150, 300).
  const doc = makeDoc(tool, [
    {time_ms: 600_000, price: '180'},
    {time_ms: 1_200_000, price: '180'},
    {time_ms: 900_000, price: '140'},
  ]);
  const unfilled = {...doc, style: {...doc.style, fill_color: null}};

  it('has a valid manifest needing three anchors', () => {
    expect(manifestProblems(tool)).toEqual([]);
    expect(tool.manifest.anchorCount).toBe(3);
  });

  it('closes and fills the polygon', () => {
    const ops = renderOps(tool, doc);
    expect(ops.filter(o => o.op === 'lineTo').map(o => o.args)).toEqual([
      [200, 100],
      [150, 300],
    ]);
    expect(ops.some(o => o.op === 'closePath')).toBe(true);
    expect(ops.some(o => o.op === 'fill')).toBe(true);
  });

  it('draws only a segment while the third vertex is pending', () => {
    const preview = {...doc, anchors: doc.anchors.slice(0, 2)};
    const ops = renderOps(tool, preview);
    expect(ops.some(o => o.op === 'closePath')).toBe(false);
    expect(ops.some(o => o.op === 'stroke')).toBe(true);
  });

  it('hits vertices by anchor index, edges and the filled inside', () => {
    expect(tool.hitTest(doc, TRANSFORM, {x: 151, y: 298}, 6)).toEqual({
      part: 'handle',
      handleIndex: 2,
    });
    expect(tool.hitTest(doc, TRANSFORM, {x: 150, y: 103}, 6)).toEqual({
      part: 'body',
    });
    // Closing edge from (150, 300) back to (100, 100).
    expect(tool.hitTest(doc, TRANSFORM, {x: 125, y: 200}, 6)).toEqual({
      part: 'body',
    });
    expect(tool.hitTest(doc, TRANSFORM, {x: 150, y: 150}, 6)).toEqual({
      part: 'body',
    });
    expect(tool.hitTest(unfilled, TRANSFORM, {x: 150, y: 150}, 6)).toBeNull();
    expect(tool.hitTest(doc, TRANSFORM, {x: 400, y: 400}, 6)).toBeNull();
  });
});
