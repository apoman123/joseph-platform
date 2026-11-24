/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Parallel channel: base line A-B plus a parallel line through
 * the third anchor C, a dashed centre line halfway between them and an
 * optional fill. While C is pending only A-B is drawn.
 */

import {
  anchorPoints,
  applyStroke,
  distanceToSegment,
  drawHandles,
  hitHandles,
  strokePath,
  type Point,
  type ToolPlugin,
} from '@joseph/drawing-core';

/**
 * Computes the parallel line through `c`, offset vertically from the base.
 * @param a Base start, in CSS pixels.
 * @param b Base end, in CSS pixels.
 * @param c Point on the parallel line, in CSS pixels.
 * @return Parallel line endpoints at the x of `a` and `b`. A vertical base
 *     (no x extent) is shifted by the y offset of `c` from `a`.
 */
export function parallelThrough(a: Point, b: Point, c: Point): [Point, Point] {
  const dx = b.x - a.x;
  const slope = dx === 0 ? 0 : (b.y - a.y) / dx;
  const baseAtC = a.y + slope * (c.x - a.x);
  const offset = c.y - baseAtC;
  return [
    {x: a.x, y: a.y + offset},
    {x: b.x, y: b.y + offset},
  ];
}

/** Parallel channel tool. */
export const parallelChannelTool: ToolPlugin = {
  manifest: {
    id: 'parallel-channel',
    version: 1,
    displayName: 'Parallel channel',
    group: 'lines',
    icon: 'columns-2',
    anchorCount: 3,
  },
  defaultStyle: palette => ({
    color: palette.user,
    line_width: 1,
    line_style: 'solid',
    // A hex alpha byte on the `#rrggbb` user color: 0x1a, about 10 % opacity.
    fill_color: `${palette.user}1a`,
  }),
  render(ctx, doc, transform, state) {
    const [a, b, c] = anchorPoints(transform, doc) ?? [];
    if (!a || !b) return;
    applyStroke(ctx, doc.style, state);
    strokePath(ctx, [a, b]);
    if (c) {
      const [p1, p2] = parallelThrough(a, b, c);
      strokePath(ctx, [p1, p2]);
      if (doc.style.fill_color) {
        ctx.fillStyle = doc.style.fill_color;
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(b.x, b.y);
        ctx.lineTo(p2.x, p2.y);
        ctx.lineTo(p1.x, p1.y);
        ctx.closePath();
        ctx.fill();
      }
      ctx.setLineDash([3, 3]);
      strokePath(ctx, [
        {x: (a.x + p1.x) / 2, y: (a.y + p1.y) / 2},
        {x: (b.x + p2.x) / 2, y: (b.y + p2.y) / 2},
      ]);
    }
    drawHandles(ctx, [a, b, ...(c ? [c] : [])], state);
  },
  hitTest(doc, transform, point, tolerance) {
    const [a, b, c] = anchorPoints(transform, doc) ?? [];
    if (!a || !b) return null;
    const handle = hitHandles([a, b, ...(c ? [c] : [])], point, tolerance);
    if (handle) return handle;
    if (distanceToSegment(point, a, b) <= tolerance) return {part: 'body'};
    if (c) {
      const [p1, p2] = parallelThrough(a, b, c);
      if (distanceToSegment(point, p1, p2) <= tolerance) return {part: 'body'};
    }
    return null;
  },
  describe: () => 'Parallel channel',
};
