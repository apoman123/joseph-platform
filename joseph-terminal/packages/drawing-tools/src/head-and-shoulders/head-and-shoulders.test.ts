/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Tests for the head and shoulders tool.
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
import {headAndShouldersTool as tool, necklineSegment} from '.';

describe('head-and-shoulders', () => {
  const doc = makeDoc(tool, [
    {time_ms: 0, price: '100'},
    {time_ms: 600_000, price: '150'},
    {time_ms: 1_200_000, price: '120'},
    {time_ms: 1_800_000, price: '180'},
    {time_ms: 2_400_000, price: '122'},
    {time_ms: 3_000_000, price: '152'},
    {time_ms: 3_600_000, price: '100'},
  ]);
  it('has a valid manifest needing seven anchors with fill off', () => {
    expect(manifestProblems(tool)).toEqual([]);
    expect(tool.manifest.anchorCount).toBe(7);
    expect(tool.defaultStyle(STATE.palette).fill_color).toBeNull();
  });
  it('spans the neckline from the start to the end anchor', () => {
    expect(
      necklineSegment([
        {x: 0, y: 500},
        {x: 100, y: 250},
        {x: 200, y: 400},
        {x: 300, y: 100},
        {x: 400, y: 390},
        {x: 500, y: 240},
        {x: 600, y: 500},
      ])
    ).toEqual([
      {x: 0, y: 410},
      {x: 600, y: 380},
    ]);
    expect(necklineSegment([{x: 0, y: 0}])).toBeNull();
  });
  it('labels the shoulders and the head', () => {
    const labels = texts(renderOps(tool, doc));
    expect(labels).toEqual(['Left shoulder', 'Head', 'Right shoulder']);
    expect(tool.describe?.(doc)).toBe('Head 180, neckline 120 to 122');
  });
  it('hits handles, the zig-zag and the neckline, and the fill when on', () => {
    expect(tool.hitTest(doc, TRANSFORM, {x: 300, y: 101}, 6)).toEqual({
      part: 'handle',
      handleIndex: 3,
    });
    expect(tool.hitTest(doc, TRANSFORM, {x: 250, y: 250}, 6)).toEqual({
      part: 'body',
    });
    expect(tool.hitTest(doc, TRANSFORM, {x: 50, y: 407.5}, 6)).toEqual({
      part: 'body',
    });
    expect(tool.hitTest(doc, TRANSFORM, {x: 300, y: 300}, 6)).toBeNull();
    const filled = {...doc, style: {...doc.style, fill_color: '#e66fd21a'}};
    expect(tool.hitTest(filled, TRANSFORM, {x: 300, y: 300}, 6)).toEqual({
      part: 'body',
    });
    expect(tool.hitTest(doc, TRANSFORM, {x: 300, y: 450}, 6)).toBeNull();
  });
});
