/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Tests for the ray tool.
 */

import {describe, expect, it} from 'vitest';
import {
  TRANSFORM,
  makeDoc,
  manifestProblems,
  renderOps,
} from '../testing/harness';
import {rayTool} from '.';

const ANCHORS = [
  {time_ms: 600_000, price: '150'},
  {time_ms: 1_200_000, price: '160'},
];

describe('ray', () => {
  const doc = makeDoc(rayTool, ANCHORS);
  it('has a valid manifest', () =>
    void expect(manifestProblems(rayTool)).toEqual([]));
  it('starts at the first anchor and runs to the right edge', () => {
    const ops = renderOps(rayTool, doc);
    expect(ops.find(o => o.op === 'moveTo')?.args).toEqual([100, 250]);
    expect(ops.find(o => o.op === 'lineTo')?.args[0]).toBe(1000);
    expect(rayTool.hitTest(doc, TRANSFORM, {x: 500, y: 50}, 6)).toEqual({
      part: 'body',
    });
    expect(rayTool.hitTest(doc, TRANSFORM, {x: 50, y: 275}, 6)).toBeNull();
  });
});
