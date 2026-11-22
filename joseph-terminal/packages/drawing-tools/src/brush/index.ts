/**
 * @fileoverview Brush: a freehand stroke recorded while the pointer is held
 * down. Points are stored as time/price anchors (at most 64), so the stroke
 * stays attached to the candles on zoom and resize.
 */
import {
  MAX_ANCHORS,
  anchorPoints,
  applyStroke,
  distanceToSegment,
  drawHandles,
  hitHandles,
  type Point,
  type ToolPlugin,
} from '@joseph/drawing-core';

/**
 * Strokes a smooth curve through points (quadratic curves via midpoints).
 * @param ctx Context.
 * @param points Points.
 */
export function strokeSmooth(
  ctx: CanvasRenderingContext2D,
  points: readonly Point[]
): void {
  const first = points[0];
  if (!first) return;
  ctx.beginPath();
  ctx.moveTo(first.x, first.y);
  for (let i = 1; i < points.length - 1; i += 1) {
    const p = points[i];
    const next = points[i + 1];
    if (!p || !next) continue;
    ctx.quadraticCurveTo(p.x, p.y, (p.x + next.x) / 2, (p.y + next.y) / 2);
  }
  const last = points.at(-1);
  if (last && points.length > 1) ctx.lineTo(last.x, last.y);
  ctx.stroke();
}

/** Brush tool. */
export const brushTool: ToolPlugin = {
  manifest: {
    id: 'brush',
    version: 1,
    displayName: 'Brush',
    group: 'annotation',
    icon: 'brush',
    anchorCount: 2,
    creation: 'freehand',
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
    ctx.lineJoin = 'round';
    ctx.lineCap = 'round';
    strokeSmooth(ctx, points);
    const ends = [points[0], points.at(-1)].filter(
      (p): p is Point => p !== undefined
    );
    drawHandles(ctx, ends, state);
  },
  hitTest(doc, transform, point, tolerance) {
    const points = anchorPoints(transform, doc) ?? [];
    if (points.length < 2) return null;
    const first = points[0];
    const last = points.at(-1);
    if (first && last) {
      const handle = hitHandles([first, last], point, tolerance);
      if (handle) {
        return {
          part: 'handle',
          handleIndex: handle.handleIndex === 0 ? 0 : points.length - 1,
        };
      }
    }
    for (let i = 1; i < points.length; i += 1) {
      const a = points[i - 1];
      const b = points[i];
      if (a && b && distanceToSegment(point, a, b) <= tolerance) {
        return {part: 'body'};
      }
    }
    return null;
  },
  describe: doc => `Brush stroke with ${doc.anchors.length} points`,
};
