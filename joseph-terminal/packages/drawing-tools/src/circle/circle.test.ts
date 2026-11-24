/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Tests for the circle tool.
 */

import {describe, expect, it} from 'vitest';
import {
  TRANSFORM,
  makeDoc,
  manifestProblems,
  renderOps,
} from '../testing/harness';
import {circleRadius, circleTool as tool, distanceToCircle} from '.';

describe('circle', () => {
  // Centre (100, 250), radius point (150, 250): radius 50.
  const doc = makeDoc(tool, [
    {time_ms: 600_000, price: '150'},
    {time_ms: 900_000, price: '150'},
  ]);
  const unfilled = {...doc, style: {...doc.style, fill_color: null}};

  it('has a valid manifest', () =>
    void expect(manifestProblems(tool)).toEqual([]));

  it('measures the radius in pixels', () => {
    expect(circleRadius({x: 0, y: 0}, {x: 30, y: 40})).toBe(50);
    expect(distanceToCircle({x: 0, y: 60}, {x: 0, y: 0}, 50)).toBe(10);
    expect(distanceToCircle({x: 0, y: 10}, {x: 0, y: 0}, 50)).toBe(40);
  });

  it('paints a full circle around the centre', () => {
    const ops = renderOps(tool, doc);
    expect(ops.find(o => o.op === 'arc')?.args).toEqual([
      100,
      250,
      50,
      0,
      2 * Math.PI,
    ]);
  });

  it('hits handles, the circumference and the filled inside', () => {
    expect(tool.hitTest(doc, TRANSFORM, {x: 100, y: 250}, 6)).toEqual({
      part: 'handle',
      handleIndex: 0,
    });
    expect(tool.hitTest(doc, TRANSFORM, {x: 149, y: 251}, 6)).toEqual({
      part: 'handle',
      handleIndex: 1,
    });
    expect(tool.hitTest(doc, TRANSFORM, {x: 100, y: 203}, 6)).toEqual({
      part: 'body',
    });
    expect(tool.hitTest(doc, TRANSFORM, {x: 120, y: 250}, 6)).toEqual({
      part: 'body',
    });
    expect(tool.hitTest(unfilled, TRANSFORM, {x: 120, y: 250}, 6)).toBeNull();
    expect(tool.hitTest(doc, TRANSFORM, {x: 300, y: 250}, 6)).toBeNull();
  });
});
