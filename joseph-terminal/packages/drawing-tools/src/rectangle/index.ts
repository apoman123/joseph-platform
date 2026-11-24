/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Rectangle: an axis-aligned box between two corner anchors,
 * filled by default. Each of its four corners has a handle; dragging one
 * moves that corner and keeps the opposite corner in place.
 */

import {
  anchorPoints,
  applyStroke,
  boxCorners,
  distanceToRectBorder,
  drawHandles,
  hitHandles,
  moveBoxCorner,
  pointInRect,
  rectFromPoints,
  type ToolPlugin,
} from '@joseph/drawing-core';

/** Rectangle tool. */
export const rectangleTool: ToolPlugin = {
  manifest: {
    id: 'rectangle',
    version: 1,
    displayName: 'Rectangle',
    group: 'shapes',
    icon: 'square',
    anchorCount: 2,
    hotkey: 'alt+r',
  },
  defaultStyle: palette => ({
    color: palette.user,
    line_width: 1,
    line_style: 'solid',
    // A hex alpha byte on the `#rrggbb` user color: 0x26, about 15 % opacity.
    fill_color: `${palette.user}26`,
  }),
  render(ctx, doc, transform, state) {
    const [a, b] = anchorPoints(transform, doc) ?? [];
    if (!a || !b) return;
    const rect = rectFromPoints(a, b);
    if (doc.style.fill_color) {
      ctx.fillStyle = doc.style.fill_color;
      ctx.fillRect(rect.x, rect.y, rect.width, rect.height);
    }
    applyStroke(ctx, doc.style, state);
    ctx.strokeRect(rect.x, rect.y, rect.width, rect.height);
    drawHandles(ctx, boxCorners(a, b), state);
  },
  hitTest(doc, transform, point, tolerance) {
    const [a, b] = anchorPoints(transform, doc) ?? [];
    if (!a || !b) return null;
    const handle = hitHandles(boxCorners(a, b), point, tolerance);
    if (handle) return handle;
    const rect = rectFromPoints(a, b);
    const filled = Boolean(doc.style.fill_color) && pointInRect(point, rect);
    return filled || distanceToRectBorder(point, rect) <= tolerance
      ? {part: 'body'}
      : null;
  },
  moveHandle: (doc, handleIndex, anchor) =>
    moveBoxCorner(doc.anchors, handleIndex, anchor),
  describe: doc =>
    `Rectangle ${doc.anchors[0]?.price ?? '?'} to ${doc.anchors[1]?.price ?? '?'}`,
};
