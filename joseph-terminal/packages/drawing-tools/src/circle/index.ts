/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Circle.
 *
 * - Anchors: 2, the centre and a point on the circumference.
 * - Geometry: a true circle in pixel space whose radius is the pixel
 *   distance between the anchors, so it stays round at any zoom while both
 *   anchors keep their time/price positions.
 * - Hit-testing: handles first; then the circumference within tolerance,
 *   or anywhere inside when filled.
 * - Handles: centre (index 0) and the radius point (index 1).
 * - Style options: color, line width and dash, fill (on by default).
 */

import {
  anchorPoints,
  applyStroke,
  distance,
  drawHandles,
  hitHandles,
  type Point,
  type ToolPlugin,
} from '@joseph/drawing-core';

/**
 * Radius of the circle through `edge` around `center`.
 * @param center Centre.
 * @param edge Point on the circumference.
 * @return Radius in pixels.
 */
export function circleRadius(center: Point, edge: Point): number {
  return distance(center, edge);
}

/**
 * Distance from a point to a circle's circumference.
 * @param p Point.
 * @param center Centre.
 * @param radius Radius.
 * @return Distance in pixels.
 */
export function distanceToCircle(
  p: Point,
  center: Point,
  radius: number
): number {
  return Math.abs(distance(p, center) - radius);
}

/** Circle tool. */
export const circleTool: ToolPlugin = {
  manifest: {
    id: 'circle',
    version: 1,
    displayName: 'Circle',
    group: 'shapes',
    icon: 'circle',
    anchorCount: 2,
  },
  defaultStyle: palette => ({
    color: palette.user,
    line_width: 1,
    line_style: 'solid',
    fill_color: `${palette.user}26`,
  }),
  render(ctx, doc, transform, state) {
    const [center, edge] = anchorPoints(transform, doc) ?? [];
    if (!center || !edge) return;
    ctx.beginPath();
    ctx.arc(center.x, center.y, circleRadius(center, edge), 0, 2 * Math.PI);
    if (doc.style.fill_color) {
      ctx.fillStyle = doc.style.fill_color;
      ctx.fill();
    }
    applyStroke(ctx, doc.style, state);
    ctx.stroke();
    drawHandles(ctx, [center, edge], state);
  },
  hitTest(doc, transform, point, tolerance) {
    const [center, edge] = anchorPoints(transform, doc) ?? [];
    if (!center || !edge) return null;
    const handle = hitHandles([center, edge], point, tolerance);
    if (handle) return handle;
    const radius = circleRadius(center, edge);
    const filled =
      Boolean(doc.style.fill_color) && distance(point, center) <= radius;
    return filled || distanceToCircle(point, center, radius) <= tolerance
      ? {part: 'body'}
      : null;
  },
  describe: doc => `Circle centred at ${doc.anchors[0]?.price ?? '?'}`,
};
