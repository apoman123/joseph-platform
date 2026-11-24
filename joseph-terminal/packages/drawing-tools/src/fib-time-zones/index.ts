/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Fibonacci time zones.
 *
 * - Anchors: `[A, B]`. A starts zone 0 and B starts zone 1; only their
 *   times matter for the zones (prices only place the handles).
 * - Geometry: with `unit = B.time_ms - A.time_ms`, full-height vertical
 *   lines sit at `A.time_ms + unit * F` for the Fibonacci numbers
 *   0, 1, 2, 3, 5, 8, 13, 21, 34, 55, 89 (exact integer milliseconds, so
 *   zones stay on the same bars at any zoom). Lines off the plot are
 *   skipped; a dashed segment joins A and B.
 * - Hit-testing: handles first, then any drawn vertical line or the A-B
 *   segment.
 * - Handles: A and B (handle index = anchor index).
 * - Style options: color, width, dash, `show_labels` (Fibonacci number at
 *   the top of each line).
 */

import {
  anchorPoints,
  applyStroke,
  distanceToSegment,
  drawHandles,
  drawLabel,
  hitHandles,
  strokePath,
  type PixelTransform,
  type ToolPlugin,
} from '@joseph/drawing-core';
import type {DrawingDocument} from '@joseph/contracts';

/** Fibonacci numbers of the zones. */
export const FIB_TIME_ZONE_NUMBERS: readonly number[] = [
  0, 1, 2, 3, 5, 8, 13, 21, 34, 55, 89,
];

/**
 * Computes zone start times.
 * @param startMs Zone 0 time (anchor A), epoch milliseconds.
 * @param unitMs Zone unit (B - A) in milliseconds; negative runs back in
 *     time.
 * @param numbers Fibonacci numbers (`FIB_TIME_ZONE_NUMBERS` by default).
 * @return Number/time pairs; times before the epoch and duplicates (zero
 *     unit) are dropped.
 */
export function timeZoneTimes(
  startMs: number,
  unitMs: number,
  numbers: readonly number[] = FIB_TIME_ZONE_NUMBERS
): Array<{number: number; timeMs: number}> {
  const seen = new Set<number>();
  const zones: Array<{number: number; timeMs: number}> = [];
  for (const number of numbers) {
    const timeMs = startMs + unitMs * number;
    if (timeMs < 0 || seen.has(timeMs)) continue;
    seen.add(timeMs);
    zones.push({number, timeMs});
  }
  return zones;
}

/**
 * Returns the zone lines that fall on the plot.
 * @param doc Document.
 * @param transform Current chart transform.
 * @return Fibonacci number and x (CSS pixels) of each line within the plot
 *     width; empty without two anchors.
 */
function visibleLines(
  doc: DrawingDocument,
  transform: PixelTransform
): Array<{number: number; x: number}> {
  const [a, b] = doc.anchors;
  if (!a || !b) return [];
  const lines: Array<{number: number; x: number}> = [];
  for (const zone of timeZoneTimes(a.time_ms, b.time_ms - a.time_ms)) {
    const x = transform.timeToX(zone.timeMs);
    if (x === null || x < 0 || x > transform.width) continue;
    lines.push({number: zone.number, x});
  }
  return lines;
}

/** Fibonacci time zones tool. */
export const fibTimeZonesTool: ToolPlugin = {
  manifest: {
    id: 'fib-time-zones',
    version: 1,
    displayName: 'Fib time zones',
    group: 'fibonacci',
    icon: 'tally-5',
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
    for (const {number, x} of visibleLines(doc, transform)) {
      strokePath(ctx, [
        {x, y: 0},
        {x, y: transform.height},
      ]);
      if (doc.style.show_labels !== false) {
        drawLabel(ctx, String(number), x + 4, 10, doc.style.color, state);
      }
    }
    ctx.setLineDash([3, 3]);
    strokePath(ctx, [a, b]);
    drawHandles(ctx, [a, b], state);
  },
  hitTest(doc, transform, point, tolerance) {
    const [a, b] = anchorPoints(transform, doc) ?? [];
    if (!a || !b) return null;
    const handle = hitHandles([a, b], point, tolerance);
    if (handle) return handle;
    const onLine = visibleLines(doc, transform).some(
      ({x}) =>
        Math.abs(point.x - x) <= tolerance &&
        point.y >= -tolerance &&
        point.y <= transform.height + tolerance
    );
    return onLine || distanceToSegment(point, a, b) <= tolerance
      ? {part: 'body'}
      : null;
  },
  describe: doc => {
    const [a, b] = doc.anchors;
    const minutes =
      a && b ? Math.round(Math.abs(b.time_ms - a.time_ms) / 60_000) : 0;
    return `Fib time zones, unit ${minutes} min`;
  },
};
