/**
 * @fileoverview Tests for the curve tool.
 */
import {describe, expect, it} from 'vitest';
import {
  STATE,
  TRANSFORM,
  makeDoc,
  manifestProblems,
  renderOps,
} from '../testing/harness';
import {curvePath, curveTool as tool} from '.';

describe('curve', () => {
  // Start (100, 250), control (150, 150), end (200, 250): apex (150, 200).
  const doc = makeDoc(tool, [
    {time_ms: 600_000, price: '150'},
    {time_ms: 900_000, price: '170'},
    {time_ms: 1_200_000, price: '150'},
  ]);

  it('has a valid manifest needing three anchors', () => {
    expect(manifestProblems(tool)).toEqual([]);
    expect(tool.manifest.anchorCount).toBe(3);
  });

  it('samples a quadratic Bézier through the control midpoint', () => {
    const path = curvePath([
      {x: 100, y: 250},
      {x: 150, y: 150},
      {x: 200, y: 250},
    ]);
    expect(path[0]).toEqual({x: 100, y: 250});
    expect(path.at(-1)).toEqual({x: 200, y: 250});
    expect(path[path.length >> 1]).toEqual({x: 150, y: 200});
    expect(
      curvePath([
        {x: 0, y: 0},
        {x: 5, y: 5},
      ])
    ).toHaveLength(2);
  });

  it('draws dashed control lines when selected', () => {
    const dashes = (ops: ReturnType<typeof renderOps>) =>
      ops.filter(o => o.op === 'setLineDash').map(o => o.args[0]);
    expect(STATE.selected).toBe(true);
    expect(dashes(renderOps(tool, doc))).toContainEqual([3, 3]);
  });

  it('hits handles by anchor index and the curve, not the control lines', () => {
    expect(tool.hitTest(doc, TRANSFORM, {x: 150, y: 151}, 6)).toEqual({
      part: 'handle',
      handleIndex: 1,
    });
    expect(tool.hitTest(doc, TRANSFORM, {x: 100, y: 250}, 6)).toEqual({
      part: 'handle',
      handleIndex: 0,
    });
    expect(tool.hitTest(doc, TRANSFORM, {x: 150, y: 203}, 6)).toEqual({
      part: 'body',
    });
    // Midpoint of the start-control line, about 9 px from the curve.
    expect(tool.hitTest(doc, TRANSFORM, {x: 125, y: 200}, 6)).toBeNull();
    expect(tool.hitTest(doc, TRANSFORM, {x: 150, y: 300}, 6)).toBeNull();
  });
});
