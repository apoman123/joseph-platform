/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Curve.
 *
 * - Anchors: 3, the start, a control point and the end.
 * - Geometry: quadratic Bézier from start to end bent toward the control
 *   point, in pixel space (the curve passes through the midpoint of the
 *   control point and the start/end midpoint). While the end is pending a
 *   segment start-control is drawn. When selected, dashed control lines
 *   start-control-end show how the control point shapes the curve.
 * - Hit-testing: handles first; then the sampled curve within tolerance
 *   (the control lines are not hit).
 * - Handles: start, control and end (indices 0-2).
 * - Style options: color, line width and dash. No fill.
 */

import {
  anchorPoints,
  applyStroke,
  distanceToPolyline,
  drawHandles,
  hitHandles,
  sampleQuadratic,
  strokePath,
  type Point,
  type ToolPlugin,
} from '@joseph/drawing-core';

/**
 * Sampled path of the curve for the anchor pixels placed so far.
 * @param points Anchor pixels (2 while creating, 3 when complete).
 * @return Path vertices from start to end.
 */
export function curvePath(points: readonly Point[]): Point[] {
  const [a, control, b] = points;
  if (!a || !control) return [];
  return b ? sampleQuadratic(a, control, b) : [a, control];
}

/** Curve tool. */
export const curveTool: ToolPlugin = {
  manifest: {
    id: 'curve',
    version: 1,
    displayName: 'Curve',
    group: 'shapes',
    icon: 'spline-pointer',
    anchorCount: 3,
  },
  defaultStyle: palette => ({
    color: palette.user,
    line_width: 1,
    line_style: 'solid',
  }),
  render(ctx, doc, transform, state) {
    const points = (anchorPoints(transform, doc) ?? []).slice(0, 3);
    const path = curvePath(points);
    if (path.length < 2) return;
    applyStroke(ctx, doc.style, state);
    strokePath(ctx, path);
    if (points.length === 3 && (state.selected || state.preview)) {
      ctx.lineWidth = 1;
      ctx.globalAlpha = 0.6;
      ctx.setLineDash([3, 3]);
      strokePath(ctx, points);
    }
    drawHandles(ctx, points, state);
  },
  hitTest(doc, transform, point, tolerance) {
    const points = (anchorPoints(transform, doc) ?? []).slice(0, 3);
    const path = curvePath(points);
    if (path.length < 2) return null;
    return (
      hitHandles(points, point, tolerance) ??
      (distanceToPolyline(point, path) <= tolerance ? {part: 'body'} : null)
    );
  },
  describe: doc =>
    `Curve ${doc.anchors[0]?.price ?? '?'} to ${doc.anchors[2]?.price ?? '?'}`,
};
