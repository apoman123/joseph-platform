/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Vertical line: marks one moment in time (UTC).
 */

import {
  anchorPoints,
  applyStroke,
  drawHandles,
  drawLabel,
  hitHandles,
  strokePath,
  type ToolPlugin,
} from '@joseph/drawing-core';

/**
 * Formats a UTC time for the label.
 * @param timeMs UTC ms.
 * @return `YYYY-MM-DD HH:MM UTC`.
 */
function formatUtc(timeMs: number): string {
  return `${new Date(timeMs).toISOString().slice(0, 16).replace('T', ' ')} UTC`;
}

/** Vertical line tool. */
export const verticalLineTool: ToolPlugin = {
  manifest: {
    id: 'vertical-line',
    version: 1,
    displayName: 'Vertical line',
    group: 'lines',
    icon: 'separator-vertical',
    anchorCount: 1,
  },
  defaultStyle: palette => ({
    color: palette.user,
    line_width: 1,
    line_style: 'dashed',
    show_labels: true,
  }),
  render(ctx, doc, transform, state) {
    const [p] = anchorPoints(transform, doc) ?? [];
    if (!p) return;
    applyStroke(ctx, doc.style, state);
    strokePath(ctx, [
      {x: p.x, y: 0},
      {x: p.x, y: transform.height},
    ]);
    if (doc.style.show_labels !== false) {
      drawLabel(
        ctx,
        formatUtc(doc.anchors[0]?.time_ms ?? 0),
        p.x + 6,
        14,
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
      (Math.abs(point.x - p.x) <= tolerance ? {part: 'body'} : null)
    );
  },
  describe: doc =>
    `Vertical line at ${formatUtc(doc.anchors[0]?.time_ms ?? 0)}`,
};
