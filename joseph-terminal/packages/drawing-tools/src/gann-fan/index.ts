/**
 * @fileoverview Gann fan.
 *
 * - Anchors: `[A, B]`. A is the fan origin; B fixes the 1×1 angle, i.e.
 *   one unit of time is `dx = B.x - A.x` and one unit of price is
 *   `dy = B.y - A.y` (pixels).
 * - Geometry: nine rays from A for the price×time ratios 1×8, 1×4, 1×3,
 *   1×2, 1×1, 2×1, 3×1, 4×1 and 8×1. The ray for `p×t` has direction
 *   `(t * dx, p * dy)`, so 1×1 passes through B, and every ray is clipped
 *   to the plot edge.
 * - Hit-testing: handles first, then any ray.
 * - Handles: A and B (handle index = anchor index).
 * - Style options: color, width, dash, `show_labels` (ratio near the end
 *   of each ray).
 */
import {
  anchorPoints,
  applyStroke,
  distanceToSegment,
  drawHandles,
  drawLabel,
  hitHandles,
  rayToEdge,
  strokePath,
  type Point,
  type ToolPlugin,
} from '@joseph/drawing-core';

/** Price×time ratios of the fan, steepest last. */
export const GANN_FAN_RATIOS: readonly (readonly [number, number])[] = [
  [1, 8],
  [1, 4],
  [1, 3],
  [1, 2],
  [1, 1],
  [2, 1],
  [3, 1],
  [4, 1],
  [8, 1],
];

/** One fan ray. */
export interface GannRay {
  /** Ratio label such as `2×1`. */
  label: string;
  /** Direction from the origin in pixels. */
  direction: Point;
  /** Where the ray leaves the plot. */
  end: Point;
}

/**
 * Computes the fan rays.
 * @param origin Fan origin (anchor A).
 * @param scale Point fixing the 1×1 angle (anchor B).
 * @param width Plot width.
 * @param height Plot height.
 * @return Rays, or an empty list when A and B coincide.
 */
export function gannFanRays(
  origin: Point,
  scale: Point,
  width: number,
  height: number
): GannRay[] {
  const dx = scale.x - origin.x;
  const dy = scale.y - origin.y;
  if (dx === 0 && dy === 0) return [];
  return GANN_FAN_RATIOS.map(([price, time]) => {
    const direction = {x: time * dx, y: price * dy};
    return {
      label: `${price}×${time}`,
      direction,
      end: rayToEdge(
        origin,
        {x: origin.x + direction.x, y: origin.y + direction.y},
        width,
        height
      ),
    };
  });
}

/** Gann fan tool. */
export const gannFanTool: ToolPlugin = {
  manifest: {
    id: 'gann-fan',
    version: 1,
    displayName: 'Gann fan',
    group: 'gann',
    icon: 'trending-up-down',
    anchorCount: 2,
  },
  defaultStyle: palette => ({
    color: palette.user,
    line_width: 1,
    line_style: 'solid',
    show_labels: true,
  }),
  render(ctx, doc, transform, state) {
    const [a, b] = anchorPoints(transform, doc) ?? [];
    if (!a || !b) return;
    applyStroke(ctx, doc.style, state);
    const rays = gannFanRays(a, b, transform.width, transform.height);
    for (const ray of rays) strokePath(ctx, [a, ray.end]);
    if (doc.style.show_labels !== false) {
      ctx.font = `11px ${state.palette.fontFamily}`;
      for (const ray of rays) {
        const length = Math.hypot(ray.end.x - a.x, ray.end.y - a.y);
        if (length < 1) continue;
        // Pull the label back from the plot edge along the ray.
        const back = Math.min(length / 2, 36);
        const x = ray.end.x - ((ray.end.x - a.x) / length) * back;
        const y = ray.end.y - ((ray.end.y - a.y) / length) * back;
        const width = ctx.measureText(ray.label).width;
        drawLabel(ctx, ray.label, x - width / 2, y, doc.style.color, state);
      }
    }
    drawHandles(ctx, [a, b], state);
  },
  hitTest(doc, transform, point, tolerance) {
    const [a, b] = anchorPoints(transform, doc) ?? [];
    if (!a || !b) return null;
    const handle = hitHandles([a, b], point, tolerance);
    if (handle) return handle;
    return gannFanRays(a, b, transform.width, transform.height).some(
      ray => distanceToSegment(point, a, ray.end) <= tolerance
    )
      ? {part: 'body'}
      : null;
  },
  describe: doc => `Gann fan from ${doc.anchors[0]?.price ?? '?'}`,
};
