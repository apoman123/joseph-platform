/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Arc.
 *
 * - Anchors: 3, the start, a point the arc passes through, and the end.
 * - Geometry: the circular arc through the three anchors in pixel space
 *   (the arc of their circumcircle from start to end that contains the
 *   middle anchor). Collinear anchors give the straight segment start-end;
 *   while the end is pending a segment start-middle is drawn.
 * - Hit-testing: handles first; then the sampled arc within tolerance.
 * - Handles: start, through point and end (indices 0-2).
 * - Style options: color, line width and dash. No fill.
 */

import {
  anchorPoints,
  applyStroke,
  distanceToPolyline,
  drawHandles,
  hitHandles,
  sampleArc,
  strokePath,
  type Point,
  type ToolPlugin,
} from '@joseph/drawing-core';

/**
 * Sampled path of the arc for the anchor pixels placed so far.
 * @param points Anchor pixels (2 while creating, 3 when complete).
 * @return Path vertices from start to end.
 */
export function arcPath(points: readonly Point[]): Point[] {
  const [a, b, c] = points;
  if (!a || !b) return [];
  return c ? sampleArc(a, b, c) : [a, b];
}

/** Arc tool. */
export const arcTool: ToolPlugin = {
  manifest: {
    id: 'arc',
    version: 1,
    displayName: 'Arc',
    group: 'shapes',
    icon: 'rainbow',
    anchorCount: 3,
  },
  defaultStyle: palette => ({
    color: palette.user,
    line_width: 1,
    line_style: 'solid',
  }),
  render(ctx, doc, transform, state) {
    const points = (anchorPoints(transform, doc) ?? []).slice(0, 3);
    const path = arcPath(points);
    if (path.length < 2) return;
    applyStroke(ctx, doc.style, state);
    strokePath(ctx, path);
    drawHandles(ctx, points, state);
  },
  hitTest(doc, transform, point, tolerance) {
    const points = (anchorPoints(transform, doc) ?? []).slice(0, 3);
    const path = arcPath(points);
    if (path.length < 2) return null;
    return (
      hitHandles(points, point, tolerance) ??
      (distanceToPolyline(point, path) <= tolerance ? {part: 'body'} : null)
    );
  },
  describe: doc =>
    `Arc ${doc.anchors[0]?.price ?? '?'} to ${doc.anchors[2]?.price ?? '?'}`,
};
