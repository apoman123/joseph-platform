/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Horizontal line: one price level across the whole chart.
 */

import {
  anchorPoints,
  applyStroke,
  drawHandles,
  drawLabel,
  formatPrice,
  hitHandles,
  strokePath,
  type ToolPlugin,
} from '@joseph/drawing-core';

/** Horizontal line tool. */
export const horizontalLineTool: ToolPlugin = {
  manifest: {
    id: 'horizontal-line',
    version: 1,
    displayName: 'Horizontal line',
    group: 'lines',
    icon: 'minus',
    anchorCount: 1,
    hotkey: 'alt+h',
  },
  defaultStyle: palette => ({
    color: palette.user,
    line_width: 1,
    line_style: 'solid',
    show_labels: true,
  }),
  render(ctx, doc, transform, state) {
    const [p] = anchorPoints(transform, doc) ?? [];
    if (!p) return;
    applyStroke(ctx, doc.style, state);
    strokePath(ctx, [
      {x: 0, y: p.y},
      {x: transform.width, y: p.y},
    ]);
    const price = doc.anchors[0]?.price ?? '0';
    if (doc.style.show_labels !== false) {
      drawLabel(
        ctx,
        formatPrice(price, state.tickSize),
        transform.width - 80,
        p.y,
        doc.style.color,
        state
      );
    }
    drawHandles(ctx, [p], state);
  },
  hitTest(doc, transform, point, tolerance) {
    const [p] = anchorPoints(transform, doc) ?? [];
    if (!p) return null;
    return (
      hitHandles([p], point, tolerance) ??
      (Math.abs(point.y - p.y) <= tolerance ? {part: 'body'} : null)
    );
  },
  describe: doc => `Horizontal line at ${doc.anchors[0]?.price ?? '?'}`,
};
