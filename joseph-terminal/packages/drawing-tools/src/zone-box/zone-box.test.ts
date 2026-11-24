/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Tests for the zone box tool.
 */

import {describe, expect, it} from 'vitest';
import {
  TRANSFORM,
  makeDoc,
  manifestProblems,
  renderOps,
  texts,
} from '../testing/harness';
import {midPrice, zoneBoxTool as tool, zoneRect} from '.';

describe('zone box', () => {
  const doc = makeDoc(tool, [
    {time_ms: 600_000, price: '160'},
    {time_ms: 1_200_000, price: '150.5'},
  ]);
  it('has a valid manifest', () =>
    void expect(manifestProblems(tool)).toEqual([]));
  it('extends right by default and labels the exact midpoint', () => {
    expect(midPrice('160', '150.5')).toBe('155.25');
    expect(texts(renderOps(tool, doc))).toEqual(['Zone · 50% 155.2']);
    expect(zoneRect({x: 100, y: 200}, {x: 200, y: 300}, 1000, true).width).toBe(
      900
    );
    expect(tool.hitTest(doc, TRANSFORM, {x: 800, y: 230}, 6)).toEqual({
      part: 'body',
    });
  });
  it('uses the drawing text as its name and can stop at the second anchor', () => {
    const named = {
      ...doc,
      text: 'Weekly FVG',
      style: {...doc.style, extend_right: false},
    };
    expect(texts(renderOps(tool, named))[0]).toMatch(/^Weekly FVG/);
    expect(tool.hitTest(named, TRANSFORM, {x: 800, y: 230}, 6)).toBeNull();
  });
});
