/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Measure (price range) tool: the price change, percent
 * change, bar count and duration between two anchors, drawn as a box
 * tinted with the bid color when price rises and the ask color when it
 * falls.
 */

import {Decimal, type BarCalendar, type Timeframe} from '@joseph/contracts';
import {
  anchorPoints,
  applyStroke,
  drawHandles,
  drawLabel,
  formatPrice,
  hitHandles,
  pointInRect,
  rectFromPoints,
  type ToolPlugin,
} from '@joseph/drawing-core';

/** Measurement result. */
export interface Measurement {
  /** Signed price change `to - from` (decimal string). */
  delta: string;
  /** Signed percent change from `from`, 2 decimals (`'0.00'` from price 0). */
  pct: string;
  /** Number of bars spanned. */
  bars: number;
  /** Wall-clock span: `<d>d <h>h` from one day, else `<h>h <m>m`. */
  duration: string;
}

/**
 * Measures the span between two anchors. With a session calendar the bar
 * count only counts session bars (no overnight or weekend bars).
 * @param from Start anchor (epoch milliseconds, decimal price).
 * @param to End anchor (epoch milliseconds, decimal price).
 * @param durationMs Bar duration in milliseconds, for the bar count without
 *     a session calendar.
 * @param sessions Chart timeframe and session calendar, if any.
 * @return Measurement.
 * @throws `RangeError` when an anchor price is not a decimal string.
 */
export function measure(
  from: {time_ms: number; price: string},
  to: {time_ms: number; price: string},
  durationMs: number,
  sessions?: {timeframe?: Timeframe; calendar?: BarCalendar}
): Measurement {
  const start = Decimal.parse(from.price);
  const delta = Decimal.parse(to.price).sub(start);
  const pct = start.isZero()
    ? '0.00'
    : delta.div(start).mul(Decimal.parse('100')).toFixed(2);
  const spanMs = Math.abs(to.time_ms - from.time_ms);
  const minutes = Math.round(spanMs / 60_000);
  const days = Math.floor(minutes / 1440);
  const hours = Math.floor((minutes % 1440) / 60);
  const mins = minutes % 60;
  const duration = days > 0 ? `${days}d ${hours}h` : `${hours}h ${mins}m`;
  const {calendar, timeframe} = sessions ?? {};
  let bars = Math.round(spanMs / Math.max(1, durationMs));
  if (calendar && timeframe && !calendar.continuous) {
    try {
      const [early, late] = [from.time_ms, to.time_ms].sort((x, y) => x - y);
      bars = calendar.barsBetween(
        timeframe,
        calendar.floor(timeframe, early ?? 0),
        calendar.floor(timeframe, late ?? 0)
      );
    } catch {
      // Keep the duration-based count outside the calendar's range.
    }
  }
  return {delta: delta.toString(), pct, bars, duration};
}

/** Price range (ruler) tool. */
export const priceRangeTool: ToolPlugin = {
  manifest: {
    id: 'price-range',
    version: 1,
    displayName: 'Measure',
    group: 'measure',
    icon: 'ruler',
    anchorCount: 2,
    hotkey: 'alt+m',
  },
  defaultStyle: palette => ({
    color: palette.user,
    line_width: 1,
    line_style: 'dashed',
  }),
  render(ctx, doc, transform, state) {
    const [a, b] = anchorPoints(transform, doc) ?? [];
    const [from, to] = doc.anchors;
    if (!a || !b || !from || !to) return;
    const rect = rectFromPoints(a, b);
    const m = measure(from, to, state.durationMs, state);
    const up = !m.delta.startsWith('-');
    const tone = up ? state.palette.bid : state.palette.ask;
    // A hex alpha byte on the `#rrggbb` tone: 0x22, about 13 % opacity.
    ctx.fillStyle = `${tone}22`;
    ctx.fillRect(rect.x, rect.y, rect.width, rect.height);
    applyStroke(ctx, doc.style, state);
    ctx.strokeRect(rect.x, rect.y, rect.width, rect.height);
    const sign = up ? '+' : '';
    drawLabel(
      ctx,
      `${sign}${formatPrice(m.delta, state.tickSize)} (${sign}${m.pct}%)`,
      rect.x + 4,
      rect.y - 12,
      tone,
      state
    );
    drawLabel(
      ctx,
      `${m.bars} bars, ${m.duration}`,
      rect.x + 4,
      rect.y + rect.height + 12,
      state.palette.text,
      state
    );
    drawHandles(ctx, [a, b], state);
  },
  hitTest(doc, transform, point, tolerance) {
    const [a, b] = anchorPoints(transform, doc) ?? [];
    if (!a || !b) return null;
    return (
      hitHandles([a, b], point, tolerance) ??
      (pointInRect(point, rectFromPoints(a, b), tolerance)
        ? {part: 'body'}
        : null)
    );
  },
  describe: doc =>
    `Measure ${doc.anchors[0]?.price ?? '?'} to ${doc.anchors[1]?.price ?? '?'}`,
};
