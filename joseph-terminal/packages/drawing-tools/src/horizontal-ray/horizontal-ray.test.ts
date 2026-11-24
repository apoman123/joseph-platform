/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Tests for the horizontal ray tool.
 */

import {describe, expect, it} from 'vitest';
import {
  TRANSFORM,
  makeDoc,
  manifestProblems,
  renderOps,
} from '../testing/harness';
import {horizontalRayTool as tool} from '.';

describe('horizontal-ray', () => {
  const doc = makeDoc(tool, [{time_ms: 600_000, price: '150'}]);
  it('has a valid manifest', () =>
    void expect(manifestProblems(tool)).toEqual([]));
  it('starts at the anchor and runs to the right edge', () => {
    const ops = renderOps(tool, doc);
    expect(ops.find(o => o.op === 'moveTo')?.args).toEqual([100, 250]);
  });
  it('does not hit left of its origin', () => {
    expect(tool.hitTest(doc, TRANSFORM, {x: 50, y: 250}, 6)).toBeNull();
    expect(tool.hitTest(doc, TRANSFORM, {x: 500, y: 250}, 6)).toEqual({
      part: 'body',
    });
  });
});
