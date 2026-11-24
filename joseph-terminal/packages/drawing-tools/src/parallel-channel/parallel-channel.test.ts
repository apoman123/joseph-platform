/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Tests for the parallel channel tool.
 */

import {describe, expect, it} from 'vitest';
import {TRANSFORM, makeDoc, manifestProblems} from '../testing/harness';
import {parallelChannelTool as tool, parallelThrough} from '.';

describe('parallel-channel', () => {
  it('has a valid manifest', () =>
    void expect(manifestProblems(tool)).toEqual([]));
  it('offsets the base line through the third anchor', () => {
    expect(
      parallelThrough({x: 0, y: 100}, {x: 100, y: 0}, {x: 50, y: 100})
    ).toEqual([
      {x: 0, y: 150},
      {x: 100, y: 50},
    ]);
  });
  it('hits either rail', () => {
    const doc = makeDoc(tool, [
      {time_ms: 0, price: '150'},
      {time_ms: 600_000, price: '150'},
      {time_ms: 300_000, price: '140'},
    ]);
    expect(tool.hitTest(doc, TRANSFORM, {x: 70, y: 300}, 6)).toEqual({
      part: 'body',
    });
    expect(tool.hitTest(doc, TRANSFORM, {x: 70, y: 275}, 6)).toBeNull();
  });
});
