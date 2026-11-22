/**
 * @fileoverview Polyline: connected segments placed one click at a time and
 * finished with a double-click or Escape.
 */
import {
  MAX_ANCHORS,
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
 * Distance from a point to the nearest segment of a path.
 * @param point Point.
 * @param points Path vertices.
 * @return Distance in pixels (Infinity for fewer than two vertices).
 */
export function distanceToPath(point: Point, points: readonly Point[]): number {
  let best = Infinity;
  for (let i = 1; i < points.length; i += 1) {
    const a = points[i - 1];
    const b = points[i];
    if (a && b) best = Math.min(best, distanceToSegment(point, a, b));
  }
  return best;
}

/** Polyline tool. */
export const polylineTool: ToolPlugin = {
  manifest: {
    id: 'polyline',
    version: 1,
    displayName: 'Polyline',
    group: 'shapes',
    icon: 'spline',
    anchorCount: 2,
    creation: 'multi-click',
    maxAnchors: MAX_ANCHORS,
  },
  defaultStyle: palette => ({
    color: palette.user,
    line_width: 2,
    line_style: 'solid',
  }),
  render(ctx, doc, transform, state) {
    const points = anchorPoints(transform, doc) ?? [];
    if (points.length < 2) return;
    applyStroke(ctx, doc.style, state);
    strokePath(ctx, points);
    drawHandles(ctx, points, state);
  },
  hitTest(doc, transform, point, tolerance) {
    const points = anchorPoints(transform, doc) ?? [];
    if (points.length < 2) return null;
    return (
      hitHandles(points, point, tolerance) ??
      (distanceToPath(point, points) <= tolerance ? {part: 'body'} : null)
    );
  },
  describe: doc => `Polyline with ${doc.anchors.length} points`,
};
