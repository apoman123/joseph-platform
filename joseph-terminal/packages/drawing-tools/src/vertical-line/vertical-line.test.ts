/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Tests for the vertical line tool.
 */

import {describe, expect, it} from 'vitest';
import {
  TRANSFORM,
  makeDoc,
  manifestProblems,
  renderOps,
  texts,
} from '../testing/harness';
import {verticalLineTool as tool} from '.';

describe('vertical-line', () => {
  const doc = makeDoc(tool, [
    {time_ms: Date.UTC(2026, 10, 20, 8, 30), price: '150'},
  ]);
  it('has a valid manifest', () =>
    void expect(manifestProblems(tool)).toEqual([]));
  it('labels the UTC time', () => {
    expect(texts(renderOps(tool, doc))).toEqual(['2026-11-20 08:30 UTC']);
  });
  it('hits along the full height', () => {
    const x = TRANSFORM.timeToX(doc.anchors[0]?.time_ms ?? 0) ?? 0;
    expect(tool.hitTest(doc, TRANSFORM, {x: x + 2, y: 5}, 6)).toEqual({
      part: 'body',
    });
  });
});
