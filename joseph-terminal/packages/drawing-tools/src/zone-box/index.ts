/**
 * @fileoverview Zone box: a user-marked zone (for example a fair value gap
 * the trader wants to watch) with a 50% midline, an optional name and an
 * optional extension to the right edge. It is an annotation only; engine
 * AOIs are drawn by the indicator layers.
 */
import {
  anchorPoints,
  applyStroke,
  distanceToRectBorder,
  drawHandles,
  drawLabel,
  formatPrice,
  hitHandles,
  pointInRect,
  rectFromPoints,
  type Point,
  type Rect,
  type ToolPlugin,
} from '@joseph/drawing-core';
import {Decimal} from '@joseph/contracts';

/**
 * Lays out the zone, extended to the right edge when asked.
 * @param a First corner.
 * @param b Second corner.
 * @param width Plot width.
 * @param extendRight Whether the zone runs to the right edge.
 * @return Rectangle.
 */
export function zoneRect(
  a: Point,
  b: Point,
  width: number,
  extendRight: boolean
): Rect {
  const rect = rectFromPoints(a, b);
  return extendRight
    ? {...rect, width: Math.max(rect.width, width - rect.x)}
    : rect;
}

/**
 * Midpoint price of two decimal prices.
 * @param upper First price.
 * @param lower Second price.
 * @return Exact midpoint as a decimal string.
 */
export function midPrice(upper: string, lower: string): string {
  return Decimal.parse(upper)
    .add(Decimal.parse(lower))
    .div(Decimal.parse('2'))
    .toString();
}

/** Zone box tool. */
export const zoneBoxTool: ToolPlugin = {
  manifest: {
    id: 'zone-box',
    version: 1,
    displayName: 'Zone box',
    group: 'shapes',
    icon: 'rectangle-horizontal',
    anchorCount: 2,
  },
  defaultStyle: palette => ({
    color: palette.user,
    line_width: 1,
    line_style: 'solid',
    fill_color: `${palette.user}1f`,
    extend_right: true,
    show_labels: true,
  }),
  render(ctx, doc, transform, state) {
    const [a, b] = anchorPoints(transform, doc) ?? [];
    if (!a || !b) return;
    const rect = zoneRect(
      a,
      b,
      transform.width,
      doc.style.extend_right === true
    );
    if (doc.style.fill_color) {
      ctx.fillStyle = doc.style.fill_color;
      ctx.fillRect(rect.x, rect.y, rect.width, rect.height);
    }
    applyStroke(ctx, doc.style, state);
    ctx.strokeRect(rect.x, rect.y, rect.width, rect.height);
    const mid = rect.y + rect.height / 2;
    ctx.setLineDash([3, 3]);
    ctx.beginPath();
    ctx.moveTo(rect.x, mid);
    ctx.lineTo(rect.x + rect.width, mid);
    ctx.stroke();
    if (doc.style.show_labels !== false) {
      const [first, second] = doc.anchors;
      const midText =
        first && second
          ? formatPrice(midPrice(first.price, second.price), state.tickSize)
          : '';
      const name = doc.text?.trim() ? doc.text.trim() : 'Zone';
      drawLabel(
        ctx,
        `${name} · 50% ${midText}`,
        rect.x + 4,
        rect.y + 10,
        doc.style.color,
        state
      );
    }
    drawHandles(ctx, [a, b, {x: a.x, y: b.y}, {x: b.x, y: a.y}], state);
  },
  hitTest(doc, transform, point, tolerance) {
    const [a, b] = anchorPoints(transform, doc) ?? [];
    if (!a || !b) return null;
    const handle = hitHandles([a, b], point, tolerance);
    if (handle) return handle;
    const rect = zoneRect(
      a,
      b,
      transform.width,
      doc.style.extend_right === true
    );
    return pointInRect(point, rect) ||
      distanceToRectBorder(point, rect) <= tolerance
      ? {part: 'body'}
      : null;
  },
  describe: doc =>
    `Zone ${doc.anchors[0]?.price ?? '?'} to ${doc.anchors[1]?.price ?? '?'}`,
};
