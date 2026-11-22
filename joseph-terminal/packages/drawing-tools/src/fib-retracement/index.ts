/**
 * @fileoverview Fibonacci retracement between a swing start and end.
 *
 * Level `r` sits at `end + (start - end) * r`: 0 at the end anchor and 1 at
 * the start anchor. Level prices are computed with exact decimals and
 * rounded to the instrument tick.
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

/** Default retracement levels. */
export const FIB_RETRACEMENT_LEVELS = [
  '0',
  '0.236',
  '0.382',
  '0.5',
  '0.618',
  '0.786',
  '1',
];

/**
 * Computes level prices.
 * @param start Start price.
 * @param end End price.
 * @param levels Level ratios.
 * @param tickSize Tick size.
 * @return Level/price pairs.
 */
export function retracementPrices(
  start: string,
  end: string,
  levels: readonly string[],
  tickSize: string | null
): {level: string; price: string}[] {
  const s = Decimal.parse(start);
  const e = Decimal.parse(end);
  return levels.map(level => ({
    level,
    price: roundToTick(e.add(s.sub(e).mul(Decimal.parse(level))), tickSize),
  }));
}

/**
 * Reads levels from props.
 * @param props Drawing props.
 * @return Level ratios.
 */
function levelsOf(props: Record<string, unknown> | undefined): string[] {
  const levels = props?.levels;
  return Array.isArray(levels) &&
    levels.every(l => typeof l === 'string' && Decimal.isValid(l))
    ? (levels as string[])
    : FIB_RETRACEMENT_LEVELS;
}

/** Fibonacci retracement tool. */
export const fibRetracementTool: ToolPlugin = {
  manifest: {
    id: 'fib-retracement',
    version: 1,
    displayName: 'Fib retracement',
    group: 'fibonacci',
    icon: 'align-justify',
    anchorCount: 2,
    hotkey: 'alt+f',
  },
  defaultStyle: palette => ({
    color: palette.user,
    line_width: 1,
    line_style: 'solid',
    show_labels: true,
  }),
  defaultProps: () => ({levels: [...FIB_RETRACEMENT_LEVELS]}),
  render(ctx, doc, transform, state) {
    const [a, b] = anchorPoints(transform, doc) ?? [];
    const [start, end] = doc.anchors;
    if (!a || !b || !start || !end) return;
    const left = Math.min(a.x, b.x);
    const right = Math.max(a.x, b.x);
    applyStroke(ctx, doc.style, state);
    for (const {level, price} of retracementPrices(
      start.price,
      end.price,
      levelsOf(doc.props),
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
    ctx.setLineDash([3, 3]);
    strokePath(ctx, [a, b]);
    drawHandles(ctx, [a, b], state);
  },
  hitTest(doc, transform, point, tolerance) {
    const [a, b] = anchorPoints(transform, doc) ?? [];
    const [start, end] = doc.anchors;
    if (!a || !b || !start || !end) return null;
    const handle = hitHandles([a, b], point, tolerance);
    if (handle) return handle;
    const left = Math.min(a.x, b.x);
    const right = Math.max(a.x, b.x);
    for (const {price} of retracementPrices(
      start.price,
      end.price,
      levelsOf(doc.props),
      null
    )) {
      const y = transform.priceToY(Number(price));
      if (
        y !== null &&
        distanceToSegment(point, {x: left, y}, {x: right, y}) <= tolerance
      ) {
        return {part: 'body'};
      }
    }
    return null;
  },
  describe: doc =>
    `Fib retracement ${doc.anchors[0]?.price ?? '?'} to ${doc.anchors[1]?.price ?? '?'}`,
};
