/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Tests for the rotated rectangle tool.
 */

import {describe, expect, it} from 'vitest';
import {
  TRANSFORM,
  makeDoc,
  manifestProblems,
  renderOps,
} from '../testing/harness';
import {rotatedRectCorners, rotatedRectangleTool as tool} from '.';

describe('rotated-rectangle', () => {
  // A (100, 100), B (200, 100), C (150, 150): corners down to y = 150.
  const doc = makeDoc(tool, [
    {time_ms: 600_000, price: '180'},
    {time_ms: 1_200_000, price: '180'},
    {time_ms: 900_000, price: '170'},
  ]);
  const unfilled = {...doc, style: {...doc.style, fill_color: null}};

  it('has a valid manifest needing three anchors', () => {
    expect(manifestProblems(tool)).toEqual([]);
    expect(tool.manifest.anchorCount).toBe(3);
  });

  it('offsets the base side by the signed distance of C', () => {
    expect(
      rotatedRectCorners({x: 0, y: 0}, {x: 100, y: 0}, {x: 50, y: 40})
    ).toEqual([
      {x: 0, y: 0},
      {x: 100, y: 0},
      {x: 100, y: 40},
      {x: 0, y: 40},
    ]);
    expect(
      rotatedRectCorners({x: 0, y: 0}, {x: 100, y: 0}, {x: 20, y: -30})[2]
    ).toEqual({x: 100, y: -30});
    const tilted = rotatedRectCorners(
      {x: 0, y: 0},
      {x: 100, y: 100},
      {x: 0, y: 100}
    );
    expect(tilted[2].x).toBeCloseTo(50);
    expect(tilted[2].y).toBeCloseTo(150);
    expect(tilted[3].x).toBeCloseTo(-50);
    expect(tilted[3].y).toBeCloseTo(50);
  });

  it('closes and fills the four corners', () => {
    const ops = renderOps(tool, doc);
    expect(ops.filter(o => o.op === 'lineTo').map(o => o.args)).toEqual([
      [200, 100],
      [200, 150],
      [100, 150],
    ]);
    expect(ops.some(o => o.op === 'fill')).toBe(true);
  });

  it('hits handles by anchor index, sides and the filled inside', () => {
    expect(tool.hitTest(doc, TRANSFORM, {x: 150, y: 151}, 6)).toEqual({
      part: 'handle',
      handleIndex: 2,
    });
    expect(tool.hitTest(doc, TRANSFORM, {x: 199, y: 101}, 6)).toEqual({
      part: 'handle',
      handleIndex: 1,
    });
    expect(tool.hitTest(doc, TRANSFORM, {x: 202, y: 125}, 6)).toEqual({
      part: 'body',
    });
    expect(tool.hitTest(doc, TRANSFORM, {x: 130, y: 125}, 6)).toEqual({
      part: 'body',
    });
    expect(tool.hitTest(unfilled, TRANSFORM, {x: 130, y: 125}, 6)).toBeNull();
    expect(tool.hitTest(doc, TRANSFORM, {x: 400, y: 400}, 6)).toBeNull();
  });
});
