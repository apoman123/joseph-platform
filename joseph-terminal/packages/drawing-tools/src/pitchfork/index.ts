/**
 * @fileoverview Andrews pitchfork.
 *
 * - Anchors: `[P1, P2, P3]`. P1 is the pivot; P2 and P3 are the reaction
 *   high and low (either order).
 * - Geometry (pixels): the median line runs from P1 through
 *   `M = midpoint(P2, P3)`; the upper and lower tines start at P2 and P3 and
 *   run parallel to the median. All three are rays clipped to the plot
 *   edge. P2 and P3 are joined by a segment.
 * - Hit-testing: handles first, then any tine or the P2-P3 segment, then the
 *   inside of the fork when it is filled.
 * - Handles: the three anchors (handle index = anchor index).
 * - Style options: color, width, dash, and `fill_color` between the outer
 *   tines (on by default; the style bar's Fill toggle turns it off).
 */
import {
  anchorPoints,
  applyStroke,
  distanceToSegment,
  drawHandles,
  hitHandles,
  pointInPolygon,
  rayToEdge,
  strokePath,
  type Point,
  type ToolPlugin,
} from '@joseph/drawing-core';

/** Pixel geometry of a complete pitchfork. */
export interface PitchforkGeometry {
  /** Median line from P1 to the plot edge. */
  median: [Point, Point];
  /** Tine from P2 to the plot edge. */
  upper: [Point, Point];
  /** Tine from P3 to the plot edge. */
  lower: [Point, Point];
  /** Fill polygon between the outer tines. */
  fill: Point[];
}

/**
 * Computes the median line and tines of a pitchfork.
 * @param p1 Pivot.
 * @param p2 First reaction point.
 * @param p3 Second reaction point.
 * @param width Plot width.
 * @param height Plot height.
 * @return Geometry in pixels.
 */
export function pitchforkTines(
  p1: Point,
  p2: Point,
  p3: Point,
  width: number,
  height: number
): PitchforkGeometry {
  const mid = {x: (p2.x + p3.x) / 2, y: (p2.y + p3.y) / 2};
  const dx = mid.x - p1.x;
  const dy = mid.y - p1.y;
  const along = (origin: Point): [Point, Point] => [
    origin,
    rayToEdge(origin, {x: origin.x + dx, y: origin.y + dy}, width, height),
  ];
  const median = along(p1);
  const upper = along(p2);
  const lower = along(p3);
  const length = Math.hypot(dx, dy);
  // Extend both outer tines by the same amount so the fill is a band.
  const reach =
    length === 0
      ? 0
      : Math.max(
          Math.hypot(upper[1].x - p2.x, upper[1].y - p2.y),
          Math.hypot(lower[1].x - p3.x, lower[1].y - p3.y)
        ) / length;
  const fill = [
    p2,
    {x: p2.x + dx * reach, y: p2.y + dy * reach},
    {x: p3.x + dx * reach, y: p3.y + dy * reach},
    p3,
  ];
  return {median, upper, lower, fill};
}

/** Andrews pitchfork tool. */
export const pitchforkTool: ToolPlugin = {
  manifest: {
    id: 'pitchfork',
    version: 1,
    displayName: 'Andrews pitchfork',
    group: 'lines',
    icon: 'git-fork',
    anchorCount: 3,
  },
  defaultStyle: palette => ({
    color: palette.user,
    line_width: 1,
    line_style: 'solid',
    fill_color: `${palette.user}14`,
  }),
  render(ctx, doc, transform, state) {
    const points = anchorPoints(transform, doc) ?? [];
    const [p1, p2, p3] = points;
    if (!p1 || !p2) return;
    applyStroke(ctx, doc.style, state);
    if (!p3) {
      strokePath(ctx, [p1, p2]);
      drawHandles(ctx, points, state);
      return;
    }
    const g = pitchforkTines(p1, p2, p3, transform.width, transform.height);
    if (doc.style.fill_color) {
      ctx.fillStyle = doc.style.fill_color;
      ctx.beginPath();
      g.fill.forEach((p, i) =>
        i === 0 ? ctx.moveTo(p.x, p.y) : ctx.lineTo(p.x, p.y)
      );
      ctx.closePath();
      ctx.fill();
    }
    strokePath(ctx, g.median);
    strokePath(ctx, g.upper);
    strokePath(ctx, g.lower);
    strokePath(ctx, [p2, p3]);
    drawHandles(ctx, [p1, p2, p3], state);
  },
  hitTest(doc, transform, point, tolerance) {
    const [p1, p2, p3] = anchorPoints(transform, doc) ?? [];
    if (!p1 || !p2 || !p3) return null;
    const handle = hitHandles([p1, p2, p3], point, tolerance);
    if (handle) return handle;
    const g = pitchforkTines(p1, p2, p3, transform.width, transform.height);
    const lines: [Point, Point][] = [g.median, g.upper, g.lower, [p2, p3]];
    if (lines.some(([a, b]) => distanceToSegment(point, a, b) <= tolerance))
      return {part: 'body'};
    return doc.style.fill_color && pointInPolygon(point, g.fill)
      ? {part: 'body'}
      : null;
  },
  describe: doc => `Andrews pitchfork from ${doc.anchors[0]?.price ?? '?'}`,
};
