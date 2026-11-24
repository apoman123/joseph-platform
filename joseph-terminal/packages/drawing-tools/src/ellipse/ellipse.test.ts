/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Tests for the ellipse tool.
 */

import {describe, expect, it} from 'vitest';
import {
  TRANSFORM,
  makeDoc,
  manifestProblems,
  renderOps,
} from '../testing/harness';
import {
  distanceToEllipse,
  ellipseGeometry,
  ellipseTool as tool,
  pointInEllipse,
} from '.';

describe('ellipse', () => {
  // Corners at (100, 200) and (200, 250): centre (150, 225), radii 50 x 25.
  const doc = makeDoc(tool, [
    {time_ms: 600_000, price: '160'},
    {time_ms: 1_200_000, price: '150'},
  ]);
  const unfilled = {...doc, style: {...doc.style, fill_color: null}};

  it('has a valid manifest', () =>
    void expect(manifestProblems(tool)).toEqual([]));

  it('inscribes the ellipse in the anchor box', () => {
    const e = ellipseGeometry({x: 200, y: 250}, {x: 100, y: 200});
    expect(e).toEqual({center: {x: 150, y: 225}, rx: 50, ry: 25});
    expect(pointInEllipse({x: 190, y: 225}, e)).toBe(true);
    expect(pointInEllipse({x: 190, y: 245}, e)).toBe(false);
    expect(distanceToEllipse({x: 150, y: 190}, e)).toBeCloseTo(10, 0);
  });

  it('strokes and fills an axis-aligned ellipse', () => {
    const ops = renderOps(tool, doc);
    expect(ops.find(o => o.op === 'ellipse')?.args).toEqual([
      150,
      225,
      50,
      25,
      0,
      0,
      2 * Math.PI,
    ]);
    expect(ops.some(o => o.op === 'fill')).toBe(true);
    expect(ops.some(o => o.op === 'stroke')).toBe(true);
  });

  it('hits handles by anchor index, the outline and the filled inside', () => {
    expect(tool.hitTest(doc, TRANSFORM, {x: 101, y: 201}, 6)).toEqual({
      part: 'handle',
      handleIndex: 0,
    });
    expect(tool.hitTest(doc, TRANSFORM, {x: 200, y: 250}, 6)).toEqual({
      part: 'handle',
      handleIndex: 1,
    });
    expect(tool.hitTest(doc, TRANSFORM, {x: 150, y: 202}, 6)).toEqual({
      part: 'body',
    });
    expect(tool.hitTest(doc, TRANSFORM, {x: 150, y: 225}, 6)).toEqual({
      part: 'body',
    });
    expect(tool.hitTest(unfilled, TRANSFORM, {x: 150, y: 225}, 6)).toBeNull();
    expect(tool.hitTest(doc, TRANSFORM, {x: 400, y: 225}, 6)).toBeNull();
  });
});
