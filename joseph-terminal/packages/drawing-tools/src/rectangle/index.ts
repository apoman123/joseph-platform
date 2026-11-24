/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Rectangle zone between two corners.
 */

import {
  anchorPoints,
  applyStroke,
  distanceToRectBorder,
  drawHandles,
  hitHandles,
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
    drawHandles(ctx, [a, b, {x: a.x, y: b.y}, {x: b.x, y: a.y}], state);
  },
  hitTest(doc, transform, point, tolerance) {
    const [a, b] = anchorPoints(transform, doc) ?? [];
    if (!a || !b) return null;
    const handle = hitHandles([a, b], point, tolerance);
    if (handle) return handle;
    const rect = rectFromPoints(a, b);
    const filled = Boolean(doc.style.fill_color) && pointInRect(point, rect);
    return filled || distanceToRectBorder(point, rect) <= tolerance
      ? {part: 'body'}
      : null;
  },
  describe: doc =>
    `Rectangle ${doc.anchors[0]?.price ?? '?'} to ${doc.anchors[1]?.price ?? '?'}`,
};
