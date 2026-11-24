/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Tests for the long position planner.
 */

import {positionMetrics} from '@joseph/drawing-core';
import {describe, expect, it} from 'vitest';
import {
  TRANSFORM,
  makeDoc,
  manifestProblems,
  renderOps,
  texts,
} from '../testing/harness';
import {longPositionTool as tool} from '.';

describe('long-position', () => {
  const doc = makeDoc(tool, [{time_ms: 0, price: '150'}]);
  it('has a valid manifest', () =>
    void expect(manifestProblems(tool)).toEqual([]));
  it('expands one click into tick-rounded entry, target and stop', () => {
    expect(doc.anchors).toEqual([
      {time_ms: 0, price: '150'},
      {time_ms: 24 * 60_000, price: '153'},
      {time_ms: 24 * 60_000, price: '148.5'},
    ]);
  });
  it('computes R with exact decimals', () => {
    expect(positionMetrics('long', '150', '153', '148.5')).toEqual({
      riskReward: '2.00',
      targetPct: '2.00',
      stopPct: '1.00',
      valid: true,
    });
    expect(positionMetrics('long', '150', '140', '160').valid).toBe(false);
  });
  it('labels target, stop and R and never touches orders', () => {
    const labels = texts(renderOps(tool, doc));
    expect(labels).toEqual([
      'Target 153.0 (2.00%)',
      'Stop 148.5 (1.00%)',
      'Long 150.0, R 2.00',
    ]);
    expect(
      tool.hitTest(doc, TRANSFORM, {x: 240, y: TRANSFORM.priceToY(153) ?? 0}, 6)
    ).toEqual({part: 'handle', handleIndex: 1});
    expect(Object.keys(tool)).not.toContain('submit');
  });
});
