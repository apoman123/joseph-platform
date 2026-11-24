/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Tests for the short position planner.
 */

import {positionMetrics} from '@joseph/drawing-core';
import {describe, expect, it} from 'vitest';
import {makeDoc, manifestProblems, renderOps, texts} from '../testing/harness';
import {shortPositionTool as tool} from '.';

describe('short-position', () => {
  const doc = makeDoc(tool, [{time_ms: 0, price: '150'}]);
  it('has a valid manifest', () =>
    void expect(manifestProblems(tool)).toEqual([]));
  it('places target below and stop above entry', () => {
    expect(doc.anchors[1]?.price).toBe('147');
    expect(doc.anchors[2]?.price).toBe('151.5');
    expect(positionMetrics('short', '150', '147', '151.5').riskReward).toBe(
      '2.00'
    );
  });
  it('labels the short entry', () => {
    expect(texts(renderOps(tool, doc))).toContain('Short 150.0, R 2.00');
  });
});
