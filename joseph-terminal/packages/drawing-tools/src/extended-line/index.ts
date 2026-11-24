/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Extended line: a line through two anchors that spans the
 * whole chart in both directions by default. Turning the `extend_left` or
 * `extend_right` style flag off stops the line at the first or second
 * anchor; a missing flag counts as on. A vertical line (both anchors at one
 * x) is drawn between the anchors only.
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

/** Extended line tool. */
export const extendedLineTool: ToolPlugin = {
  manifest: {
    id: 'extended-line',
    version: 1,
    displayName: 'Extended line',
    group: 'lines',
    icon: 'move-diagonal',
    anchorCount: 2,
  },
  defaultStyle: palette => ({
    color: palette.user,
    line_width: 1,
    line_style: 'solid',
    extend_left: true,
    extend_right: true,
  }),
  render(ctx, doc, transform, state) {
    const [a, b] = anchorPoints(transform, doc) ?? [];
    if (!a || !b) return;
    applyStroke(ctx, doc.style, state);
    strokePath(
      ctx,
      extendSegment(
        a,
        b,
        transform.width,
        doc.style.extend_left !== false,
        doc.style.extend_right !== false
      )
    );
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
      doc.style.extend_left !== false,
      doc.style.extend_right !== false
    );
    return distanceToSegment(point, start, end) <= tolerance
      ? {part: 'body'}
      : null;
  },
  describe: doc =>
    `Extended line through ${doc.anchors[0]?.price ?? '?'} and ${doc.anchors[1]?.price ?? '?'}`,
};
