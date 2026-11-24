/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Trend-based Fibonacci extension from three anchors A, B, C.
 *
 * Level `r` sits at `C + (B - A) * r`, using exact decimals rounded to the
 * instrument tick: the A-B swing projected from the retracement point C.
 * The A-B-C path is dashed; each level is a line from C's x, up to 160 px.
 * The levels are `props.levels` (new drawings store
 * `FIB_EXTENSION_LEVELS` there). Hit-testing covers the handles and the
 * A-B-C path, not the level lines.
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
 * @param a Swing start price (decimal string).
 * @param b Swing end price (decimal string).
 * @param c Retracement point price (decimal string).
 * @param levels Ratios as decimal strings.
 * @param tickSize Tick size, or null to leave prices unrounded.
 * @return Level/price pairs in `levels` order; prices are decimal strings.
 * @throws `RangeError` when a price, level or the tick size is not a
 *     decimal string.
 */
export function extensionPrices(
  a: string,
  b: string,
  c: string,
  levels: readonly string[],
  tickSize: string | null
): Array<{level: string; price: string}> {
  const leg = Decimal.parse(b).sub(Decimal.parse(a));
  const origin = Decimal.parse(c);
  return levels.map(level => ({
    level,
    price: roundToTick(origin.add(leg.mul(Decimal.parse(level))), tickSize),
  }));
}

/**
 * Reads levels from props.
 * @param props Drawing props.
 * @return `props.levels` when it is a list of decimal strings (an empty list
 *     draws no levels), else `FIB_EXTENSION_LEVELS`.
 */
function levelsOf(props: Record<string, unknown> | undefined): string[] {
  const levels = props?.levels;
  // The cast is safe: `every` checks that each level is a decimal string.
  return Array.isArray(levels) &&
    levels.every(l => typeof l === 'string' && Decimal.isValid(l))
    ? (levels as string[])
    : FIB_EXTENSION_LEVELS;
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
      if (from && to && distanceToSegment(point, from, to) <= tolerance) {
        return {part: 'body'};
      }
    }
    return null;
  },
  describe: () => 'Fib extension',
};
