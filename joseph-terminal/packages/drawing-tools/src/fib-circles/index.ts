/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Fibonacci circles.
 *
 * - Anchors: `[A, B]`. A is the centre; B lies on the 1.0 circle.
 * - Geometry (pixels): with `r = |B - A|`, one circle of radius
 *   `r * level` per level (default 0.236 ... 2.618, stored in
 *   `props.levels` as decimal strings), plus a dashed A-B radius line.
 *   Circles are true circles on screen and rescale with the anchors.
 * - Hit-testing: handles first, then `|distance(point, A) - r * level|`
 *   within the tolerance for any level, then the A-B line.
 * - Handles: A and B (handle index = anchor index).
 * - Style options: color, width, dash, `show_labels` (level above each
 *   circle).
 */

import {Decimal} from '@joseph/contracts';
import {
  anchorPoints,
  applyStroke,
  distance,
  distanceToSegment,
  drawHandles,
  drawLabel,
  hitHandles,
  strokePath,
  type Point,
  type ToolPlugin,
} from '@joseph/drawing-core';

/** Default circle levels, as multiples of the A-B radius. */
export const FIB_CIRCLE_LEVELS = [
  '0.236',
  '0.382',
  '0.5',
  '0.618',
  '0.786',
  '1',
  '1.618',
  '2.618',
];

/**
 * Reads levels from props.
 * @param props Drawing props.
 * @return `props.levels` when it is a non-empty list of decimal strings,
 *     else `FIB_CIRCLE_LEVELS`.
 */
function levelsOf(props: Record<string, unknown> | undefined): string[] {
  const levels = props?.levels;
  // The cast is safe: `every` checks that each level is a decimal string.
  return Array.isArray(levels) &&
    levels.length > 0 &&
    levels.every(l => typeof l === 'string' && Decimal.isValid(l))
    ? (levels as string[])
    : FIB_CIRCLE_LEVELS;
}

/**
 * Computes the circle radii.
 * @param center Centre (anchor A), in CSS pixels.
 * @param onUnit Point on the 1.0 circle (anchor B), in CSS pixels.
 * @param levels Level ratios as decimal strings.
 * @return Level/radius pairs, radius in CSS pixels, in `levels` order.
 */
export function circleRadii(
  center: Point,
  onUnit: Point,
  levels: readonly string[]
): Array<{level: string; radius: number}> {
  const unit = distance(center, onUnit);
  return levels.map(level => ({level, radius: unit * Number(level)}));
}

/** Fibonacci circles tool. */
export const fibCirclesTool: ToolPlugin = {
  manifest: {
    id: 'fib-circles',
    version: 1,
    displayName: 'Fib circles',
    group: 'fibonacci',
    icon: 'target',
    anchorCount: 2,
  },
  defaultStyle: palette => ({
    color: palette.user,
    line_width: 1,
    line_style: 'solid',
    show_labels: true,
  }),
  defaultProps: () => ({levels: [...FIB_CIRCLE_LEVELS]}),
  render(ctx, doc, transform, state) {
    const [a, b] = anchorPoints(transform, doc) ?? [];
    if (!a || !b) return;
    applyStroke(ctx, doc.style, state);
    const radii = circleRadii(a, b, levelsOf(doc.props));
    for (const {radius} of radii) {
      if (radius <= 0) continue;
      ctx.beginPath();
      ctx.arc(a.x, a.y, radius, 0, 2 * Math.PI);
      ctx.stroke();
    }
    if (doc.style.show_labels !== false) {
      for (const {level, radius} of radii) {
        if (radius <= 0) continue;
        drawLabel(ctx, level, a.x + 4, a.y - radius, doc.style.color, state);
      }
    }
    applyStroke(ctx, doc.style, state);
    ctx.setLineDash([3, 3]);
    strokePath(ctx, [a, b]);
    drawHandles(ctx, [a, b], state);
  },
  hitTest(doc, transform, point, tolerance) {
    const [a, b] = anchorPoints(transform, doc) ?? [];
    if (!a || !b) return null;
    const handle = hitHandles([a, b], point, tolerance);
    if (handle) return handle;
    const fromCenter = distance(point, a);
    const onCircle = circleRadii(a, b, levelsOf(doc.props)).some(
      ({radius}) => radius > 0 && Math.abs(fromCenter - radius) <= tolerance
    );
    return onCircle || distanceToSegment(point, a, b) <= tolerance
      ? {part: 'body'}
      : null;
  },
  describe: doc => `Fib circles around ${doc.anchors[0]?.price ?? '?'}`,
};
