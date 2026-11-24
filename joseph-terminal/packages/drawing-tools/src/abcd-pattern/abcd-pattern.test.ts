/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Tests for the ABCD pattern tool.
 */

import {describe, expect, it} from 'vitest';
import {
  STATE,
  TRANSFORM,
  makeDoc,
  manifestProblems,
  renderOps,
  texts,
} from '../testing/harness';
import {abcdPatternTool as tool} from '.';

describe('abcd-pattern', () => {
  const doc = makeDoc(tool, [
    {time_ms: 0, price: '100'},
    {time_ms: 600_000, price: '150'},
    {time_ms: 1_200_000, price: '130.9'},
    {time_ms: 1_800_000, price: '180.9'},
  ]);
  it('has a valid manifest needing four anchors and no fill', () => {
    expect(manifestProblems(tool)).toEqual([]);
    expect(tool.manifest.anchorCount).toBe(4);
    expect(tool.defaultStyle(STATE.palette).fill_color).toBeUndefined();
  });
  it('labels BC/AB and CD/BC', () => {
    const labels = texts(renderOps(tool, doc));
    expect(labels).toEqual(
      expect.arrayContaining(['A', 'B', 'C', 'D', '0.382', '2.618'])
    );
    expect(tool.describe?.(doc)).toBe('BC/AB 0.382, CD/BC 2.618');
  });
  it('hits handles, legs and the ratio connectors', () => {
    expect(tool.hitTest(doc, TRANSFORM, {x: 300, y: 96}, 6)).toEqual({
      part: 'handle',
      handleIndex: 3,
    });
    expect(tool.hitTest(doc, TRANSFORM, {x: 150, y: 298}, 6)).toEqual({
      part: 'body',
    });
    // Midpoint of the A-C connector.
    expect(tool.hitTest(doc, TRANSFORM, {x: 100, y: 422.75}, 6)).toEqual({
      part: 'body',
    });
    expect(tool.hitTest(doc, TRANSFORM, {x: 300, y: 450}, 6)).toBeNull();
  });
});
