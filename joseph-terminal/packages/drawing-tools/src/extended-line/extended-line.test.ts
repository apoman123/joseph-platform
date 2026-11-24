/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Tests for the extended line tool.
 */

import {describe, expect, it} from 'vitest';
import {
  TRANSFORM,
  makeDoc,
  manifestProblems,
  renderOps,
} from '../testing/harness';
import {extendedLineTool} from '.';

const ANCHORS = [
  {time_ms: 600_000, price: '150'},
  {time_ms: 1_200_000, price: '160'},
];

describe('extended line', () => {
  const doc = makeDoc(extendedLineTool, ANCHORS);
  it('has a valid manifest', () =>
    void expect(manifestProblems(extendedLineTool)).toEqual([]));
  it('spans both directions', () => {
    const ops = renderOps(extendedLineTool, doc);
    expect(ops.find(o => o.op === 'moveTo')?.args[0]).toBe(0);
    expect(
      extendedLineTool.hitTest(doc, TRANSFORM, {x: 50, y: 275}, 6)
    ).toEqual({part: 'body'});
    expect(
      extendedLineTool.hitTest(doc, TRANSFORM, {x: 100, y: 250}, 6)
    ).toEqual({
      part: 'handle',
      handleIndex: 0,
    });
  });
  it('stops at an anchor whose extend flag is off', () => {
    const leftOff = {
      ...doc,
      style: {...doc.style, extend_left: false},
    };
    const ops = renderOps(extendedLineTool, leftOff);
    expect(ops.find(o => o.op === 'moveTo')?.args).toEqual([100, 250]);
    expect(ops.find(o => o.op === 'lineTo')?.args[0]).toBe(1000);
    expect(
      extendedLineTool.hitTest(leftOff, TRANSFORM, {x: 50, y: 275}, 6)
    ).toBeNull();
    const rightOff = {
      ...doc,
      style: {...doc.style, extend_right: false},
    };
    expect(
      renderOps(extendedLineTool, rightOff).find(o => o.op === 'lineTo')?.args
    ).toEqual([200, 200]);
    expect(
      extendedLineTool.hitTest(rightOff, TRANSFORM, {x: 300, y: 150}, 6)
    ).toBeNull();
  });
});
