/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Tests for the XABCD harmonic pattern tool.
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
import {xabcdPatternTool as tool} from '.';

/** A Gartley-like XABCD: AB/XA 0.618, BC/AB 0.5, AD/XA 0.786. */
const ANCHORS = [
  {time_ms: 0, price: '100'},
  {time_ms: 600_000, price: '150'},
  {time_ms: 1_200_000, price: '119.1'},
  {time_ms: 1_800_000, price: '134.55'},
  {time_ms: 2_400_000, price: '110.7'},
];

describe('xabcd-pattern', () => {
  const doc = makeDoc(tool, ANCHORS);
  it('has a valid manifest needing five anchors', () => {
    expect(manifestProblems(tool)).toEqual([]);
    expect(tool.manifest.anchorCount).toBe(5);
    expect(tool.manifest.group).toBe('patterns');
    expect(tool.defaultStyle(STATE.palette).fill_color).toBe('#e66fd21a');
  });
  it('labels the points and the harmonic ratios from anchor prices', () => {
    const labels = texts(renderOps(tool, doc));
    expect(labels).toEqual(expect.arrayContaining(['X', 'A', 'B', 'C', 'D']));
    expect(labels).toEqual(
      expect.arrayContaining(['0.618', '0.500', '1.544', '0.786'])
    );
    expect(tool.describe?.(doc)).toBe(
      'AB/XA 0.618, BC/AB 0.500, CD/BC 1.544, AD/XA 0.786'
    );
  });
  it('fills triangles XAB and BCD', () => {
    const unfilled = {...doc, style: {...doc.style, fill_color: null}};
    const fills = (d: typeof doc) =>
      renderOps(tool, d).filter(o => o.op === 'fill').length;
    expect(fills(doc) - fills(unfilled)).toBe(2);
  });
  it('hits handles by anchor index, legs and the filled triangles', () => {
    expect(tool.hitTest(doc, TRANSFORM, {x: 101, y: 251}, 6)).toEqual({
      part: 'handle',
      handleIndex: 1,
    });
    expect(tool.hitTest(doc, TRANSFORM, {x: 400, y: 446}, 6)).toEqual({
      part: 'handle',
      handleIndex: 4,
    });
    expect(tool.hitTest(doc, TRANSFORM, {x: 50, y: 375}, 6)).toEqual({
      part: 'body',
    });
    // The X-B connector.
    expect(tool.hitTest(doc, TRANSFORM, {x: 100, y: 452}, 6)).toEqual({
      part: 'body',
    });
    expect(tool.hitTest(doc, TRANSFORM, {x: 100, y: 380}, 6)).toEqual({
      part: 'body',
    });
    const unfilled = {...doc, style: {...doc.style, fill_color: null}};
    expect(tool.hitTest(unfilled, TRANSFORM, {x: 100, y: 380}, 6)).toBeNull();
    expect(tool.hitTest(doc, TRANSFORM, {x: 50, y: 100}, 6)).toBeNull();
  });
});
