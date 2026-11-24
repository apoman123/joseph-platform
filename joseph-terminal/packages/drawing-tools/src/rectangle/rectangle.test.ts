/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Tests for the rectangle tool.
 */

import {describe, expect, it} from 'vitest';
import {
  TRANSFORM,
  makeDoc,
  manifestProblems,
  renderOps,
} from '../testing/harness';
import {rectangleTool as tool} from '.';

describe('rectangle', () => {
  const doc = makeDoc(tool, [
    {time_ms: 600_000, price: '160'},
    {time_ms: 1_200_000, price: '150'},
  ]);
  it('has a valid manifest', () =>
    void expect(manifestProblems(tool)).toEqual([]));
  it('fills and strokes the normalized rectangle', () => {
    const ops = renderOps(tool, doc);
    expect(ops.find(o => o.op === 'fillRect')?.args).toEqual([
      100, 200, 100, 50,
    ]);
    expect(ops.some(o => o.op === 'strokeRect')).toBe(true);
  });
  it('hits inside when filled and misses outside', () => {
    expect(tool.hitTest(doc, TRANSFORM, {x: 150, y: 225}, 6)).toEqual({
      part: 'body',
    });
    expect(tool.hitTest(doc, TRANSFORM, {x: 400, y: 225}, 6)).toBeNull();
  });
  it('drags all four corners with the opposite corner fixed', () => {
    // Anchors at (100, 200) and (200, 250); the other corners are
    // (100, 250) and (200, 200).
    const to = {time_ms: 1_800_000, price: '140'};
    const corners: Array<[number, number]> = [
      [100, 200],
      [200, 250],
      [100, 250],
      [200, 200],
    ];
    corners.forEach(([x, y], handleIndex) => {
      expect(tool.hitTest(doc, TRANSFORM, {x, y}, 6)).toEqual({
        part: 'handle',
        handleIndex,
      });
    });
    const move = (handleIndex: number) =>
      tool.moveHandle?.(doc, handleIndex, to);
    expect(move(0)).toEqual([to, {time_ms: 1_200_000, price: '150'}]);
    expect(move(1)).toEqual([{time_ms: 600_000, price: '160'}, to]);
    // Corner 2 moves to `to`; corner 3 (1_200_000, 160) stays.
    expect(move(2)).toEqual([
      {time_ms: 1_800_000, price: '160'},
      {time_ms: 1_200_000, price: '140'},
    ]);
    // Corner 3 moves to `to`; corner 2 (600_000, 150) stays.
    expect(move(3)).toEqual([
      {time_ms: 600_000, price: '140'},
      {time_ms: 1_800_000, price: '150'},
    ]);
  });
});
