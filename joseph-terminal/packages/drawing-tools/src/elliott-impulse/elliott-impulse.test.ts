/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Tests for the Elliott impulse wave tool.
 */

import {describe, expect, it} from 'vitest';
import {
  TRANSFORM,
  makeDoc,
  manifestProblems,
  renderOps,
  texts,
} from '../testing/harness';
import {elliottImpulseTool as tool} from '.';

describe('elliott-impulse', () => {
  const doc = makeDoc(tool, [
    {time_ms: 0, price: '100'},
    {time_ms: 600_000, price: '130'},
    {time_ms: 1_200_000, price: '115'},
    {time_ms: 1_800_000, price: '170'},
    {time_ms: 2_400_000, price: '150'},
    {time_ms: 3_000_000, price: '185'},
  ]);
  it('has a valid manifest needing six anchors', () => {
    expect(manifestProblems(tool)).toEqual([]);
    expect(tool.manifest.anchorCount).toBe(6);
  });
  it('labels waves (0) to (5) above highs and below lows', () => {
    const ops = renderOps(tool, doc);
    expect(texts(ops)).toEqual(['(0)', '(1)', '(2)', '(3)', '(4)', '(5)']);
    const wave3 = ops.find(o => o.op === 'fillText' && o.args[0] === '(3)');
    const wave4 = ops.find(o => o.op === 'fillText' && o.args[0] === '(4)');
    // Wave 3 is a high at y=150 (label above), wave 4 a low at y=250.
    expect(Number(wave3?.args[2])).toBeLessThan(150);
    expect(Number(wave4?.args[2])).toBeGreaterThan(250);
    expect(tool.describe?.(doc)).toBe('Waves (1) to (5), 100 to 185');
  });
  it('hits handles by anchor index and the wave segments', () => {
    expect(tool.hitTest(doc, TRANSFORM, {x: 500, y: 75}, 6)).toEqual({
      part: 'handle',
      handleIndex: 5,
    });
    expect(tool.hitTest(doc, TRANSFORM, {x: 50, y: 425}, 6)).toEqual({
      part: 'body',
    });
    expect(tool.hitTest(doc, TRANSFORM, {x: 250, y: 450}, 6)).toBeNull();
  });
});
