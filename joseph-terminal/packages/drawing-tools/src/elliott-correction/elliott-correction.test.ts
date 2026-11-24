/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Tests for the Elliott correction wave tool.
 */

import {describe, expect, it} from 'vitest';
import {
  TRANSFORM,
  makeDoc,
  manifestProblems,
  renderOps,
  texts,
} from '../testing/harness';
import {elliottCorrectionTool as tool} from '.';

describe('elliott-correction', () => {
  const doc = makeDoc(tool, [
    {time_ms: 0, price: '180'},
    {time_ms: 600_000, price: '140'},
    {time_ms: 1_200_000, price: '160'},
    {time_ms: 1_800_000, price: '120'},
  ]);
  it('has a valid manifest needing four anchors', () => {
    expect(manifestProblems(tool)).toEqual([]);
    expect(tool.manifest.anchorCount).toBe(4);
  });
  it('labels (0), (A), (B) and (C)', () => {
    expect(texts(renderOps(tool, doc))).toEqual(['(0)', '(A)', '(B)', '(C)']);
    expect(tool.describe?.(doc)).toBe('Waves (A) to (C), 180 to 120');
  });
  it('hits handles by anchor index and the wave segments', () => {
    expect(tool.hitTest(doc, TRANSFORM, {x: 100, y: 300}, 6)).toEqual({
      part: 'handle',
      handleIndex: 1,
    });
    expect(tool.hitTest(doc, TRANSFORM, {x: 250, y: 300}, 6)).toEqual({
      part: 'body',
    });
    expect(tool.hitTest(doc, TRANSFORM, {x: 100, y: 100}, 6)).toBeNull();
  });
});
