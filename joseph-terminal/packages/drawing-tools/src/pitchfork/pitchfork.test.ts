/**
 * @fileoverview Tests for the Andrews pitchfork tool.
 */
import {describe, expect, it} from 'vitest';
import {
  TRANSFORM,
  makeDoc,
  manifestProblems,
  renderOps,
} from '../testing/harness';
import {pitchforkTines, pitchforkTool as tool} from '.';

describe('pitchfork', () => {
  // P1 (0, 250), P2 (100, 200), P3 (100, 300): median runs horizontally.
  const doc = makeDoc(tool, [
    {time_ms: 0, price: '150'},
    {time_ms: 600_000, price: '160'},
    {time_ms: 600_000, price: '140'},
  ]);

  it('has a valid manifest needing three anchors', () => {
    expect(manifestProblems(tool)).toEqual([]);
    expect(tool.manifest.anchorCount).toBe(3);
  });

  it('runs the median through the midpoint and the tines parallel to it', () => {
    const g = pitchforkTines(
      {x: 0, y: 250},
      {x: 100, y: 200},
      {x: 100, y: 300},
      1000,
      500
    );
    expect(g.median).toEqual([
      {x: 0, y: 250},
      {x: 1000, y: 250},
    ]);
    expect(g.upper).toEqual([
      {x: 100, y: 200},
      {x: 1000, y: 200},
    ]);
    expect(g.lower[1]).toEqual({x: 1000, y: 300});
    expect(g.fill).toHaveLength(4);
  });

  it('clips sloped tines to the plot edge', () => {
    const g = pitchforkTines(
      {x: 0, y: 400},
      {x: 100, y: 200},
      {x: 100, y: 400},
      1000,
      500
    );
    // Direction (100, -100): the median leaves through the top edge.
    expect(g.median[1].y).toBeCloseTo(0);
    expect(g.median[1].x).toBeCloseTo(400);
  });

  it('hits handles by anchor index, tines, and the filled fork', () => {
    expect(tool.hitTest(doc, TRANSFORM, {x: 101, y: 299}, 6)).toEqual({
      part: 'handle',
      handleIndex: 2,
    });
    expect(tool.hitTest(doc, TRANSFORM, {x: 700, y: 202}, 6)).toEqual({
      part: 'body',
    });
    expect(tool.hitTest(doc, TRANSFORM, {x: 500, y: 225}, 6)).toEqual({
      part: 'body',
    });
    expect(tool.hitTest(doc, TRANSFORM, {x: 500, y: 450}, 6)).toBeNull();
    const unfilled = {...doc, style: {...doc.style, fill_color: null}};
    expect(tool.hitTest(unfilled, TRANSFORM, {x: 500, y: 225}, 6)).toBeNull();
  });

  it('fills the fork and strokes three tines plus the base', () => {
    const ops = renderOps(tool, doc);
    expect(ops.some(o => o.op === 'fill')).toBe(true);
    expect(ops.filter(o => o.op === 'stroke').length).toBeGreaterThanOrEqual(4);
  });
});
