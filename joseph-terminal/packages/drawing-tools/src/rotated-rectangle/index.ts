/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Rotated rectangle: a rectangle at any angle, from one side
 * and a width.
 *
 * - Anchors: 3. A and B are the ends of one side at any angle; C sets the
 *   width as its signed perpendicular distance from line AB (C may sit
 *   anywhere along the opposite side).
 * - Geometry: in pixel space, with `n` the unit normal of AB and `h` the
 *   signed distance of C along `n`, the corners are A, B, B + n·h and
 *   A + n·h. While C is pending only the side AB is drawn.
 * - Hit-testing: handles first; then any side within tolerance, or
 *   anywhere inside when filled.
 * - Handles: A, B and C (indices 0-2) at their anchor positions.
 * - Style options: color, line width and dash, fill (on by default).
 */

import {
  anchorPoints,
  applyStroke,
  distanceToPolyline,
  drawHandles,
  hitHandles,
  pointInPolygon,
  type Point,
  type ToolPlugin,
} from '@joseph/drawing-core';

/**
 * Returns the corners of the rectangle with side AB and width set by C.
 * @param a First corner of the base side, in CSS pixels.
 * @param b Second corner of the base side, in CSS pixels.
 * @param c Point setting the width (perpendicular distance from AB), in CSS
 *     pixels.
 * @return Corners in CSS pixels in order A, B, B + n·h, A + n·h; `[A, B, B,
 *     A]` when A = B.
 */
export function rotatedRectCorners(
  a: Point,
  b: Point,
  c: Point
): [Point, Point, Point, Point] {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const length = Math.hypot(dx, dy);
  if (length === 0) return [a, b, b, a];
  // Unit normal of AB, and C's signed distance along it.
  const n = {x: -dy / length, y: dx / length};
  const h = (c.x - a.x) * n.x + (c.y - a.y) * n.y;
  return [
    a,
    b,
    {x: b.x + n.x * h, y: b.y + n.y * h},
    {x: a.x + n.x * h, y: a.y + n.y * h},
  ];
}

/** Rotated rectangle tool. */
export const rotatedRectangleTool: ToolPlugin = {
  manifest: {
    id: 'rotated-rectangle',
    version: 1,
    displayName: 'Rotated rectangle',
    group: 'shapes',
    icon: 'vector-square',
    anchorCount: 3,
  },
  defaultStyle: palette => ({
    color: palette.user,
    line_width: 1,
    line_style: 'solid',
    // A hex alpha byte on the `#rrggbb` user color: 0x26, about 15 % opacity.
    fill_color: `${palette.user}26`,
  }),
  render(ctx, doc, transform, state) {
    const [a, b, c] = anchorPoints(transform, doc) ?? [];
    if (!a || !b) return;
    const corners = c ? rotatedRectCorners(a, b, c) : [a, b];
    ctx.beginPath();
    corners.forEach((p, i) =>
      i === 0 ? void ctx.moveTo(p.x, p.y) : void ctx.lineTo(p.x, p.y)
    );
    if (c) {
      ctx.closePath();
      if (doc.style.fill_color) {
        ctx.fillStyle = doc.style.fill_color;
        ctx.fill();
      }
    }
    applyStroke(ctx, doc.style, state);
    ctx.stroke();
    drawHandles(ctx, c ? [a, b, c] : [a, b], state);
  },
  hitTest(doc, transform, point, tolerance) {
    const [a, b, c] = anchorPoints(transform, doc) ?? [];
    if (!a || !b) return null;
    const handle = hitHandles(c ? [a, b, c] : [a, b], point, tolerance);
    if (handle) return handle;
    if (!c) {
      return distanceToPolyline(point, [a, b]) <= tolerance
        ? {part: 'body'}
        : null;
    }
    const corners = rotatedRectCorners(a, b, c);
    const filled =
      Boolean(doc.style.fill_color) && pointInPolygon(point, corners);
    return filled || distanceToPolyline(point, corners, true) <= tolerance
      ? {part: 'body'}
      : null;
  },
  describe: doc =>
    `Rotated rectangle from ${doc.anchors[0]?.price ?? '?'} to ${doc.anchors[1]?.price ?? '?'}`,
};
