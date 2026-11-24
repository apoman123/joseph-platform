/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Trendline between two time/price anchors, optionally
 * extended left and/or right.
 */

import {
  anchorPoints,
  applyStroke,
  distanceToSegment,
  drawHandles,
  extendSegment,
  hitHandles,
  strokePath,
  type ToolPlugin,
} from '@joseph/drawing-core';

/** Trendline tool. */
export const trendlineTool: ToolPlugin = {
  manifest: {
    id: 'trendline',
    version: 1,
    displayName: 'Trendline',
    group: 'lines',
    icon: 'trending-up',
    anchorCount: 2,
    hotkey: 'alt+t',
  },
  defaultStyle: palette => ({
    color: palette.user,
    line_width: 2,
    line_style: 'solid',
    extend_left: false,
    extend_right: false,
  }),
  render(ctx, doc, transform, state) {
    const [a, b] = anchorPoints(transform, doc) ?? [];
    if (!a || !b) return;
    applyStroke(ctx, doc.style, state);
    const [start, end] = extendSegment(
      a,
      b,
      transform.width,
      doc.style.extend_left === true,
      doc.style.extend_right === true
    );
    strokePath(ctx, [start, end]);
    drawHandles(ctx, [a, b], state);
  },
  hitTest(doc, transform, point, tolerance) {
    const [a, b] = anchorPoints(transform, doc) ?? [];
    if (!a || !b) return null;
    const handle = hitHandles([a, b], point, tolerance);
    if (handle) return handle;
    const [start, end] = extendSegment(
      a,
      b,
      transform.width,
      doc.style.extend_left === true,
      doc.style.extend_right === true
    );
    return distanceToSegment(point, start, end) <= tolerance
      ? {part: 'body'}
      : null;
  },
  describe: doc =>
    `Trendline from ${doc.anchors[0]?.price ?? '?'} to ${doc.anchors[1]?.price ?? '?'}`,
};
