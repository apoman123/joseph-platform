/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Ray: a line from the first anchor through the second that
 * continues past it to the plot edge. `extend_left` also extends it back
 * past the first anchor; turning `extend_right` off leaves a segment.
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

/** Ray tool. */
export const rayTool: ToolPlugin = {
  manifest: {
    id: 'ray',
    version: 1,
    displayName: 'Ray',
    group: 'lines',
    icon: 'move-up-right',
    anchorCount: 2,
  },
  defaultStyle: palette => ({
    color: palette.user,
    line_width: 2,
    line_style: 'solid',
    extend_left: false,
    extend_right: true,
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
      doc.style.extend_right !== false
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
      doc.style.extend_right !== false
    );
    return distanceToSegment(point, start, end) <= tolerance
      ? {part: 'body'}
      : null;
  },
  describe: doc =>
    `Ray from ${doc.anchors[0]?.price ?? '?'} through ${doc.anchors[1]?.price ?? '?'}`,
};
