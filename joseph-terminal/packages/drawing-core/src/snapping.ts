/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Magnet snapping of pointer positions to bar OHLC.
 */

import type {Point} from './geometry';
import type {PixelTransform, SnapBar} from './surface';

/**
 * Snaps a point to the nearest open/high/low/close of the bar under it when
 * within `radiusPx`; otherwise returns the point unchanged.
 * @param point Pointer position.
 * @param bars Bars sorted by time.
 * @param transform Transform.
 * @param durationMs Bar duration.
 * @param radiusPx Snap radius in pixels.
 * @return Snapped point.
 */
export function magnetSnap(
  point: Point,
  bars: readonly SnapBar[],
  transform: PixelTransform,
  durationMs: number,
  radiusPx = 18
): Point {
  const timeMs = transform.xToTime(point.x);
  if (timeMs === null || bars.length === 0) return point;
  let lo = 0;
  let hi = bars.length - 1;
  let index = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if ((bars[mid]?.timeMs ?? Infinity) <= timeMs) {
      index = mid;
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }
  const bar = bars[index];
  if (!bar || timeMs >= bar.timeMs + durationMs) return point;
  const x = transform.timeToX(bar.timeMs);
  let best: Point = point;
  let bestDistance = radiusPx;
  for (const price of [bar.open, bar.high, bar.low, bar.close]) {
    const y = transform.priceToY(price);
    if (y === null) continue;
    const d = Math.abs(y - point.y);
    if (d <= bestDistance) {
      bestDistance = d;
      best = {x: x ?? point.x, y};
    }
  }
  return best;
}
