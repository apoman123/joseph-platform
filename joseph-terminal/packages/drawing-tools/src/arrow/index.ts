/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Arrow: an annotation line with an arrowhead at the second
 * anchor, used to point at a level or a candle.
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
 * Base arrowhead barb length in CSS pixels; each unit of line width adds
 * 2 px.
 */
const HEAD_PX = 9;

/**
 * Computes the two barbs of an arrowhead.
 * @param from Tail, in CSS pixels.
 * @param to Tip, in CSS pixels.
 * @param length Barb length in CSS pixels.
 * @return Barb ends on either side of the shaft, in CSS pixels.
 */
export function arrowHead(
  from: Point,
  to: Point,
  length: number
): [Point, Point] {
  const angle = Math.atan2(to.y - from.y, to.x - from.x);
  // Each barb is π/7 (about 26 degrees) off the shaft.
  const spread = Math.PI / 7;
  return [
    {
      x: to.x - length * Math.cos(angle - spread),
      y: to.y - length * Math.sin(angle - spread),
    },
    {
      x: to.x - length * Math.cos(angle + spread),
      y: to.y - length * Math.sin(angle + spread),
    },
  ];
}

/** Arrow tool. */
export const arrowTool: ToolPlugin = {
  manifest: {
    id: 'arrow',
    version: 1,
    displayName: 'Arrow',
    group: 'annotation',
    icon: 'arrow-right',
    anchorCount: 2,
  },
  defaultStyle: palette => ({
    color: palette.user,
    line_width: 2,
    line_style: 'solid',
  }),
  render(ctx, doc, transform, state) {
    const [a, b] = anchorPoints(transform, doc) ?? [];
    if (!a || !b) return;
    applyStroke(ctx, doc.style, state);
    strokePath(ctx, [a, b]);
    const [left, right] = arrowHead(a, b, HEAD_PX + doc.style.line_width * 2);
    ctx.setLineDash([]);
    ctx.fillStyle = doc.style.color;
    ctx.beginPath();
    ctx.moveTo(b.x, b.y);
    ctx.lineTo(left.x, left.y);
    ctx.lineTo(right.x, right.y);
    ctx.closePath();
    ctx.fill();
    drawHandles(ctx, [a, b], state);
  },
  hitTest(doc, transform, point, tolerance) {
    const [a, b] = anchorPoints(transform, doc) ?? [];
    if (!a || !b) return null;
    return (
      hitHandles([a, b], point, tolerance) ??
      (distanceToSegment(point, a, b) <= tolerance ? {part: 'body'} : null)
    );
  },
  describe: doc => `Arrow pointing at ${doc.anchors[1]?.price ?? '?'}`,
};
