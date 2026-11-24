/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Tests for the Fibonacci time zones tool.
 */

import {describe, expect, it} from 'vitest';
import {
  TRANSFORM,
  makeDoc,
  manifestProblems,
  renderOps,
  texts,
} from '../testing/harness';
import {fibTimeZonesTool as tool, timeZoneTimes} from '.';

describe('fib-time-zones', () => {
  // A at x=0, B at x=50 (5 minutes): zones every 5 minutes * F.
  const doc = makeDoc(tool, [
    {time_ms: 0, price: '150'},
    {time_ms: 300_000, price: '150'},
  ]);

  it('has a valid manifest', () =>
    void expect(manifestProblems(tool)).toEqual([]));

  it('places zones at Fibonacci multiples of the unit (exact ms)', () => {
    expect(
      timeZoneTimes(1_000, 60_000, [0, 1, 2, 3, 5]).map(z => z.timeMs)
    ).toEqual([1_000, 61_000, 121_000, 181_000, 301_000]);
    expect(timeZoneTimes(5_000, 0)).toEqual([{number: 0, timeMs: 5_000}]);
    expect(
      timeZoneTimes(120_000, -60_000, [0, 1, 2, 3]).map(z => z.timeMs)
    ).toEqual([120_000, 60_000, 0]);
  });

  it('labels the lines that fit on the plot only', () => {
    const labels = texts(renderOps(tool, doc));
    // x = 50 * F: up to F = 13 (650px); 21 (1050px) is off the plot.
    expect(labels).toEqual(['0', '1', '2', '3', '5', '8', '13']);
  });

  it('hits handles by index, zone lines, and misses between them', () => {
    expect(tool.hitTest(doc, TRANSFORM, {x: 52, y: 248}, 6)).toEqual({
      part: 'handle',
      handleIndex: 1,
    });
    expect(tool.hitTest(doc, TRANSFORM, {x: 402, y: 40}, 6)).toEqual({
      part: 'body',
    });
    expect(tool.hitTest(doc, TRANSFORM, {x: 300, y: 40}, 6)).toBeNull();
    expect(tool.hitTest(doc, TRANSFORM, {x: 1050, y: 40}, 6)).toBeNull();
  });
});
