/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Tests for the arc tool.
 */

import {distance} from '@joseph/drawing-core';
import {describe, expect, it} from 'vitest';
import {TRANSFORM, makeDoc, manifestProblems} from '../testing/harness';
import {arcPath, arcTool as tool} from '.';

describe('arc', () => {
  // Start (100, 250), through (150, 200), end (200, 250): the upper half of
  // the circle of radius 50 around (150, 250).
  const doc = makeDoc(tool, [
    {time_ms: 600_000, price: '150'},
    {time_ms: 900_000, price: '160'},
    {time_ms: 1_200_000, price: '150'},
  ]);

  it('has a valid manifest needing three anchors', () => {
    expect(manifestProblems(tool)).toEqual([]);
    expect(tool.manifest.anchorCount).toBe(3);
  });

  it('samples the arc through the middle anchor', () => {
    const path = arcPath([
      {x: 100, y: 250},
      {x: 150, y: 200},
      {x: 200, y: 250},
    ]);
    expect(path[0]?.x).toBeCloseTo(100);
    expect(path.at(-1)?.x).toBeCloseTo(200);
    expect(path.at(-1)?.y).toBeCloseTo(250);
    expect(
      path.every(p => Math.abs(distance(p, {x: 150, y: 250}) - 50) < 1e-6)
    ).toBe(true);
    expect(path.every(p => p.y <= 250 + 1e-6)).toBe(true);
    expect(
      Math.min(...path.map(p => distance(p, {x: 150, y: 200})))
    ).toBeLessThan(4);
  });

  it('degrades to a segment for collinear or pending anchors', () => {
    expect(
      arcPath([
        {x: 0, y: 0},
        {x: 50, y: 50},
        {x: 100, y: 100},
      ])
    ).toEqual([
      {x: 0, y: 0},
      {x: 100, y: 100},
    ]);
    expect(
      arcPath([
        {x: 0, y: 0},
        {x: 10, y: 0},
      ])
    ).toHaveLength(2);
  });

  it('hits handles by anchor index and the arc, not its centre', () => {
    expect(tool.hitTest(doc, TRANSFORM, {x: 151, y: 201}, 6)).toEqual({
      part: 'handle',
      handleIndex: 1,
    });
    expect(tool.hitTest(doc, TRANSFORM, {x: 200, y: 251}, 6)).toEqual({
      part: 'handle',
      handleIndex: 2,
    });
    expect(tool.hitTest(doc, TRANSFORM, {x: 114.6, y: 214.6}, 6)).toEqual({
      part: 'body',
    });
    expect(tool.hitTest(doc, TRANSFORM, {x: 150, y: 250}, 6)).toBeNull();
    expect(tool.hitTest(doc, TRANSFORM, {x: 150, y: 300}, 6)).toBeNull();
  });
});
