/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Tests for the horizontal line tool.
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
import {horizontalLineTool as tool} from '.';

describe('horizontal-line', () => {
  const doc = makeDoc(tool, [{time_ms: 120_000, price: '150'}]);
  it('has a valid manifest', () =>
    void expect(manifestProblems(tool)).toEqual([]));
  it('renders a full-width line with a price label', () => {
    const ops = renderOps(tool, doc);
    expect(ops.find(o => o.op === 'lineTo')?.args).toEqual([
      TRANSFORM.width,
      250,
    ]);
    expect(texts(ops)).toEqual(['150.0']);
  });
  it('hits the line anywhere and the handle at the anchor', () => {
    expect(tool.hitTest(doc, TRANSFORM, {x: 900, y: 252}, 6)).toEqual({
      part: 'body',
    });
    expect(tool.hitTest(doc, TRANSFORM, {x: 20, y: 250}, 6)).toEqual({
      part: 'handle',
      handleIndex: 0,
    });
    expect(tool.hitTest(doc, TRANSFORM, {x: 20, y: 300}, 6)).toBeNull();
    expect(STATE.selected).toBe(true);
  });
});
