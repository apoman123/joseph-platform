/**
 * @fileoverview Horizontal ray: a price level starting at a point in time.
 */
import {
  anchorPoints,
  applyStroke,
  distanceToSegment,
  drawHandles,
  drawLabel,
  formatPrice,
  hitHandles,
  strokePath,
  type ToolPlugin,
} from '@joseph/drawing-core';

/** Horizontal ray tool. */
export const horizontalRayTool: ToolPlugin = {
  manifest: {
    id: 'horizontal-ray',
    version: 1,
    displayName: 'Horizontal ray',
    group: 'lines',
    icon: 'arrow-right-from-line',
    anchorCount: 1,
  },
  defaultStyle: palette => ({
    color: palette.user,
    line_width: 1,
    line_style: 'solid',
    show_labels: true,
  }),
  render(ctx, doc, transform, state) {
    const [p] = anchorPoints(transform, doc) ?? [];
    if (!p) return;
    applyStroke(ctx, doc.style, state);
    strokePath(ctx, [p, {x: transform.width, y: p.y}]);
    if (doc.style.show_labels !== false) {
      drawLabel(
        ctx,
        formatPrice(doc.anchors[0]?.price ?? '0', state.tickSize),
        p.x + 6,
        p.y - 10,
        doc.style.color,
        state
      );
    }
    drawHandles(ctx, [p], state);
  },
  hitTest(doc, transform, point, tolerance) {
    const [p] = anchorPoints(transform, doc) ?? [];
    if (!p) return null;
    return (
      hitHandles([p], point, tolerance) ??
      (distanceToSegment(point, p, {x: transform.width, y: p.y}) <= tolerance
        ? {part: 'body'}
        : null)
    );
  },
  describe: doc => `Horizontal ray from ${doc.anchors[0]?.price ?? '?'}`,
};
