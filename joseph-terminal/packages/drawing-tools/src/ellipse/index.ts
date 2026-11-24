/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Ellipse: an axis-aligned ellipse inscribed in a two-corner box.
 *
 * - Anchors: 2, opposite corners of the ellipse's bounding box (any order).
 * - Geometry: axis-aligned ellipse in pixel space centred on the box
 *   midpoint, with radii half the box width and height. Anchors are
 *   time/price, so the ellipse follows zoom and pan.
 * - Hit-testing: handles first; then the outline within tolerance, or
 *   anywhere inside when filled.
 * - Handles: the two anchors (indices 0 and 1).
 * - Style options: color, line width and dash, fill (on by default).
 */

import {
  anchorPoints,
  applyStroke,
  distanceToPolyline,
  drawHandles,
  hitHandles,
  sampleEllipse,
  type Point,
  type ToolPlugin,
} from '@joseph/drawing-core';

/** Axis-aligned ellipse in CSS pixels. */
export interface EllipseGeometry {
  /** Centre. */
  center: Point;
  /** Horizontal radius in CSS pixels. */
  rx: number;
  /** Vertical radius in CSS pixels. */
  ry: number;
}

/**
 * Returns the ellipse inscribed in the box spanned by two corners.
 * @param a Corner, in CSS pixels.
 * @param b Opposite corner, in CSS pixels.
 * @return Centre and radii.
 */
export function ellipseGeometry(a: Point, b: Point): EllipseGeometry {
  return {
    center: {x: (a.x + b.x) / 2, y: (a.y + b.y) / 2},
    rx: Math.abs(b.x - a.x) / 2,
    ry: Math.abs(b.y - a.y) / 2,
  };
}

/**
 * Returns whether a point lies inside (or on) an ellipse.
 * @param p Point, in CSS pixels.
 * @param e Ellipse.
 * @return True when inside or on the outline; false for a flat ellipse
 *     (a zero radius).
 */
export function pointInEllipse(p: Point, e: EllipseGeometry): boolean {
  if (e.rx <= 0 || e.ry <= 0) return false;
  const nx = (p.x - e.center.x) / e.rx;
  const ny = (p.y - e.center.y) / e.ry;
  return nx * nx + ny * ny <= 1;
}

/**
 * Returns the approximate distance from a point to an ellipse outline.
 * @param p Point, in CSS pixels.
 * @param e Ellipse.
 * @return Distance in CSS pixels.
 */
export function distanceToEllipse(p: Point, e: EllipseGeometry): number {
  // On a circle, 96 vertices stay within about 0.05 % of the radius of the
  // true outline (0.5 px at 1000 px), well inside the hit tolerance.
  return distanceToPolyline(p, sampleEllipse(e.center, e.rx, e.ry, 96), true);
}

/** Ellipse tool. */
export const ellipseTool: ToolPlugin = {
  manifest: {
    id: 'ellipse',
    version: 1,
    displayName: 'Ellipse',
    group: 'shapes',
    icon: 'ellipse',
    anchorCount: 2,
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
    const e = ellipseGeometry(a, b);
    ctx.beginPath();
    ctx.ellipse(e.center.x, e.center.y, e.rx, e.ry, 0, 0, 2 * Math.PI);
    if (doc.style.fill_color) {
      ctx.fillStyle = doc.style.fill_color;
      ctx.fill();
    }
    applyStroke(ctx, doc.style, state);
    ctx.stroke();
    drawHandles(ctx, [a, b], state);
  },
  hitTest(doc, transform, point, tolerance) {
    const [a, b] = anchorPoints(transform, doc) ?? [];
    if (!a || !b) return null;
    const handle = hitHandles([a, b], point, tolerance);
    if (handle) return handle;
    const e = ellipseGeometry(a, b);
    const filled = Boolean(doc.style.fill_color) && pointInEllipse(point, e);
    return filled || distanceToEllipse(point, e) <= tolerance
      ? {part: 'body'}
      : null;
  },
  describe: doc =>
    `Ellipse ${doc.anchors[0]?.price ?? '?'} to ${doc.anchors[1]?.price ?? '?'}`,
};
