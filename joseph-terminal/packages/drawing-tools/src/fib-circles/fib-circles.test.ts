/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Tests for the Fibonacci circles tool.
 */

import {describe, expect, it} from 'vitest';
import {
  TRANSFORM,
  makeDoc,
  manifestProblems,
  renderOps,
  texts,
} from '../testing/harness';
import {FIB_CIRCLE_LEVELS, circleRadii, fibCirclesTool as tool} from '.';

describe('fib-circles', () => {
  // Centre (500, 250), B (600, 250): unit radius 100px.
  const doc = makeDoc(tool, [
    {time_ms: 3_000_000, price: '150'},
    {time_ms: 3_600_000, price: '150'},
  ]);

  it('has a valid manifest and stores its levels', () => {
    expect(manifestProblems(tool)).toEqual([]);
    expect(doc.props).toEqual({levels: FIB_CIRCLE_LEVELS});
  });

  it('scales the unit radius by each level', () => {
    const radii = circleRadii({x: 0, y: 0}, {x: 30, y: 40}, [
      '0.5',
      '1',
      '1.618',
    ]);
    expect(radii.map(r => r.level)).toEqual(['0.5', '1', '1.618']);
    expect(radii[0]?.radius).toBeCloseTo(25);
    expect(radii[1]?.radius).toBeCloseTo(50);
    expect(radii[2]?.radius).toBeCloseTo(80.9);
  });

  it('draws one circle and label per level', () => {
    const ops = renderOps(tool, doc);
    expect(ops.filter(o => o.op === 'arc')).toHaveLength(8);
    expect(texts(ops)).toEqual(FIB_CIRCLE_LEVELS);
  });

  it('hits circles and handles, misses between circles', () => {
    expect(tool.hitTest(doc, TRANSFORM, {x: 598, y: 252}, 6)).toEqual({
      part: 'handle',
      handleIndex: 1,
    });
    expect(tool.hitTest(doc, TRANSFORM, {x: 500, y: 88}, 6)).toEqual({
      part: 'body',
    });
    expect(tool.hitTest(doc, TRANSFORM, {x: 500, y: 160}, 6)).toBeNull();
  });
});
