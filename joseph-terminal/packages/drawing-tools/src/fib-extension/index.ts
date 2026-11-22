/**
 * @fileoverview Trend-based Fibonacci extension from three anchors A, B, C.
 *
 * Level `r` sits at `C + (B - A) * r`, using exact decimals.
 */
import {Decimal} from '@joseph/contracts';
import {
  anchorPoints,
  applyStroke,
  distanceToSegment,
  drawHandles,
  drawLabel,
  formatPrice,
  hitHandles,
  roundToTick,
  strokePath,
  type ToolPlugin,
} from '@joseph/drawing-core';

/** Default extension levels. */
export const FIB_EXTENSION_LEVELS = [
  '0',
  '0.618',
  '1',
  '1.272',
  '1.618',
  '2.618',
];

/**
 * Computes extension prices.
 * @param a Swing start.
 * @param b Swing end.
 * @param c Retracement point.
 * @param levels Ratios.
 * @param tickSize Tick size.
 * @return Level/price pairs.
 */
export function extensionPrices(
  a: string,
  b: string,
  c: string,
  levels: readonly string[],
  tickSize: string | null
): {level: string; price: string}[] {
  const leg = Decimal.parse(b).sub(Decimal.parse(a));
  const origin = Decimal.parse(c);
  return levels.map(level => ({
    level,
    price: roundToTick(origin.add(leg.mul(Decimal.parse(level))), tickSize),
  }));
}

/** Fibonacci extension tool. */
export const fibExtensionTool: ToolPlugin = {
  manifest: {
    id: 'fib-extension',
    version: 1,
    displayName: 'Fib extension',
    group: 'fibonacci',
    icon: 'git-branch',
    anchorCount: 3,
  },
  defaultStyle: palette => ({
    color: palette.user,
    line_width: 1,
    line_style: 'solid',
    show_labels: true,
  }),
  defaultProps: () => ({levels: [...FIB_EXTENSION_LEVELS]}),
  render(ctx, doc, transform, state) {
    const points = anchorPoints(transform, doc) ?? [];
    const [a, b, c] = doc.anchors;
    applyStroke(ctx, doc.style, state);
    ctx.setLineDash([3, 3]);
    strokePath(ctx, points);
    if (a && b && c && points.length === 3) {
      applyStroke(ctx, doc.style, state);
      const left = points[2]?.x ?? 0;
      const right = Math.min(transform.width, left + 160);
      for (const {level, price} of extensionPrices(
        a.price,
        b.price,
        c.price,
        FIB_EXTENSION_LEVELS,
        state.tickSize
      )) {
        const y = transform.priceToY(Number(price));
        if (y === null) continue;
        strokePath(ctx, [
          {x: left, y},
          {x: right, y},
        ]);
        if (doc.style.show_labels !== false) {
          drawLabel(
            ctx,
            `${level} (${formatPrice(price, state.tickSize)})`,
            right + 6,
            y,
            doc.style.color,
            state
          );
        }
      }
    }
    drawHandles(ctx, points, state);
  },
  hitTest(doc, transform, point, tolerance) {
    const points = anchorPoints(transform, doc) ?? [];
    const handle = hitHandles(points, point, tolerance);
    if (handle) return handle;
    for (let i = 0; i + 1 < points.length; i += 1) {
      const from = points[i];
      const to = points[i + 1];
      if (from && to && distanceToSegment(point, from, to) <= tolerance)
        return {part: 'body'};
    }
    return null;
  },
  describe: () => 'Fib extension',
};
