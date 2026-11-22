/**
 * @fileoverview Triangle.
 *
 * - Anchors: 3, the vertices in placement order.
 * - Geometry: the closed polygon through the three anchors (a segment
 *   while the third vertex is still being placed).
 * - Hit-testing: handles first; then any edge within tolerance, or
 *   anywhere inside when filled.
 * - Handles: the three vertices (indices 0-2).
 * - Style options: color, line width and dash, fill (on by default).
 */
import {
  anchorPoints,
  applyStroke,
  distanceToPolyline,
  drawHandles,
  hitHandles,
  pointInPolygon,
  type ToolPlugin,
} from '@joseph/drawing-core';

/** Triangle tool. */
export const triangleTool: ToolPlugin = {
  manifest: {
    id: 'triangle',
    version: 1,
    displayName: 'Triangle',
    group: 'shapes',
    icon: 'triangle',
    anchorCount: 3,
  },
  defaultStyle: palette => ({
    color: palette.user,
    line_width: 1,
    line_style: 'solid',
    fill_color: `${palette.user}26`,
  }),
  render(ctx, doc, transform, state) {
    const points = (anchorPoints(transform, doc) ?? []).slice(0, 3);
    if (points.length < 2) return;
    ctx.beginPath();
    points.forEach((p, i) =>
      i === 0 ? ctx.moveTo(p.x, p.y) : ctx.lineTo(p.x, p.y)
    );
    if (points.length === 3) {
      ctx.closePath();
      if (doc.style.fill_color) {
        ctx.fillStyle = doc.style.fill_color;
        ctx.fill();
      }
    }
    applyStroke(ctx, doc.style, state);
    ctx.stroke();
    drawHandles(ctx, points, state);
  },
  hitTest(doc, transform, point, tolerance) {
    const points = (anchorPoints(transform, doc) ?? []).slice(0, 3);
    if (points.length < 2) return null;
    const handle = hitHandles(points, point, tolerance);
    if (handle) return handle;
    const closed = points.length === 3;
    const filled =
      closed && Boolean(doc.style.fill_color) && pointInPolygon(point, points);
    return filled || distanceToPolyline(point, points, closed) <= tolerance
      ? {part: 'body'}
      : null;
  },
  describe: doc => `Triangle ${doc.anchors.map(a => a.price).join(', ')}`,
};
