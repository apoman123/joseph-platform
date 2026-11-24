/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Shared long/short position (risk/reward) tool behaviour.
 *
 * Anchors: `[entry, target, stop]`. The entry anchor's time is the left
 * edge; the target anchor's time is the right edge. Levels are tick-rounded
 * exact decimals. The tool is a planning aid only: it never creates orders.
 */

import {
  Decimal,
  type BarCalendar,
  type DrawingAnchor,
  type Timeframe,
} from '@joseph/contracts';
import {anchorPoints} from './document';
import {pointInRect, rectFromPoints, type Point} from './geometry';
import {
  drawHandles,
  drawLabel,
  formatPrice,
  hitHandles,
  roundToTick,
  type ToolManifest,
  type ToolPlugin,
} from './tool';

/** Position direction. */
export type PositionSide = 'long' | 'short';

/** Derived position metrics. */
export interface PositionMetrics {
  /**
   * Reward-to-risk ratio as a decimal string with 2 decimals, null when risk
   * is zero.
   */
  riskReward: string | null;
  /**
   * Target distance in percent of entry, 2 decimals (negative when the
   * target is on the wrong side; `0` when entry is zero).
   */
  targetPct: string;
  /** Stop distance in percent of entry, 2 decimals (as `targetPct`). */
  stopPct: string;
  /** Whether target and stop sit on the correct sides of entry. */
  valid: boolean;
}

/**
 * Computes reward/risk metrics with exact decimals.
 * @param side Direction.
 * @param entry Entry price, decimal string.
 * @param target Target price, decimal string.
 * @param stop Stop price, decimal string.
 * @return Metrics.
 * @throws `RangeError` when a price is not a decimal string.
 */
export function positionMetrics(
  side: PositionSide,
  entry: string,
  target: string,
  stop: string
): PositionMetrics {
  const e = Decimal.parse(entry);
  const t = Decimal.parse(target);
  const s = Decimal.parse(stop);
  const reward = side === 'long' ? t.sub(e) : e.sub(t);
  const risk = side === 'long' ? e.sub(s) : s.sub(e);
  const hundred = Decimal.parse('100');
  const pct = (value: Decimal) =>
    e.isZero() ? '0' : value.div(e).mul(hundred).toFixed(2);
  const valid = !reward.isNegative() && !risk.isNegative() && !risk.isZero();
  return {
    riskReward: risk.isZero() ? null : reward.div(risk).toFixed(2),
    targetPct: pct(reward),
    stopPct: pct(risk),
    valid,
  };
}

/**
 * Expands a single entry anchor into entry, target and stop, 24 bars wide
 * (session bars when a calendar is given). The target is 2% and the stop 1%
 * away from entry: above and below for a long, below and above for a
 * short.
 * @param side Direction.
 * @param anchors User anchors (first is entry).
 * @param durationMs Bar duration in milliseconds (used without a session
 *     calendar).
 * @param tickSize Tick size the levels round to, or null for none.
 * @param sessions Timeframe and session calendar of the chart, if any.
 * @return `[entry, target, stop]`; the first three anchors when three or
 *     more are given; `anchors` unchanged when empty.
 * @throws `RangeError` when the entry price or `tickSize` is not a valid
 *     decimal string.
 */
export function expandPositionAnchors(
  side: PositionSide,
  anchors: DrawingAnchor[],
  durationMs: number,
  tickSize: string | null,
  sessions?: {timeframe?: Timeframe; calendar?: BarCalendar}
): DrawingAnchor[] {
  const entry = anchors[0];
  if (!entry) return anchors;
  if (anchors.length >= 3) return anchors.slice(0, 3);
  const price = Decimal.parse(entry.price);
  const up = side === 'long' ? '1.02' : '0.98';
  const down = side === 'long' ? '0.99' : '1.01';
  const {calendar, timeframe} = sessions ?? {};
  const right =
    calendar && timeframe && !calendar.continuous
      ? calendar.shift(timeframe, calendar.floor(timeframe, entry.time_ms), 24)
      : entry.time_ms + 24 * durationMs;
  return [
    entry,
    {
      time_ms: right,
      price: roundToTick(price.mul(Decimal.parse(up)), tickSize),
    },
    {
      time_ms: right,
      price: roundToTick(price.mul(Decimal.parse(down)), tickSize),
    },
  ];
}

/**
 * Creates a long or short position tool.
 * @param side Direction.
 * @param manifest Tool manifest.
 * @return Tool plugin.
 */
export function createPositionTool(
  side: PositionSide,
  manifest: ToolManifest
): ToolPlugin {
  /**
   * Computes the pixel geometry: anchor points, the left and right edges
   * (entry and target x, in either order) and the three handles.
   * @param doc Drawing to measure.
   * @param transform Transforms of the current frame.
   * @return Geometry in CSS pixels, or null until all three anchors can be
   *     placed.
   */
  const geometry = (
    doc: Parameters<ToolPlugin['render']>[1],
    transform: Parameters<ToolPlugin['render']>[2]
  ) => {
    const points = anchorPoints(transform, doc);
    const [entry, target, stop] = points ?? [];
    if (!entry || !target || !stop) return null;
    const left = Math.min(entry.x, target.x);
    const right = Math.max(entry.x, target.x);
    const handles: Point[] = [
      {x: left, y: entry.y},
      {x: right, y: target.y},
      {x: right, y: stop.y},
    ];
    return {entry, target, stop, left, right, handles};
  };
  return {
    manifest,
    defaultStyle: palette => ({
      color: palette.user,
      line_width: 1,
      line_style: 'solid',
      show_labels: true,
    }),
    expandAnchors: (anchors, env) =>
      expandPositionAnchors(side, anchors, env.durationMs, env.tickSize, env),
    render(ctx, doc, transform, state) {
      const g = geometry(doc, transform);
      const [entry, target, stop] = doc.anchors;
      if (!g || !entry || !target || !stop) return;
      const width = Math.max(1, g.right - g.left);
      ctx.globalAlpha = state.preview ? 0.6 : 1;
      // A `33` hex alpha suffix makes the profit and loss zones 20% opaque.
      ctx.fillStyle = `${state.palette.bid}33`;
      ctx.fillRect(
        g.left,
        Math.min(g.entry.y, g.target.y),
        width,
        Math.abs(g.target.y - g.entry.y)
      );
      ctx.fillStyle = `${state.palette.ask}33`;
      ctx.fillRect(
        g.left,
        Math.min(g.entry.y, g.stop.y),
        width,
        Math.abs(g.stop.y - g.entry.y)
      );
      ctx.strokeStyle = doc.style.color;
      ctx.lineWidth = 1.5;
      ctx.setLineDash([]);
      ctx.beginPath();
      ctx.moveTo(g.left, g.entry.y);
      ctx.lineTo(g.right, g.entry.y);
      ctx.stroke();
      const metrics = positionMetrics(
        side,
        entry.price,
        target.price,
        stop.price
      );
      if (doc.style.show_labels !== false) {
        // Level labels sit 10 px inside their zone, towards the entry line.
        drawLabel(
          ctx,
          `Target ${formatPrice(target.price, state.tickSize)} (${metrics.targetPct}%)`,
          g.left + 4,
          g.target.y + (g.target.y < g.entry.y ? 10 : -10),
          state.palette.bid,
          state
        );
        drawLabel(
          ctx,
          `Stop ${formatPrice(stop.price, state.tickSize)} (${metrics.stopPct}%)`,
          g.left + 4,
          g.stop.y + (g.stop.y < g.entry.y ? 10 : -10),
          state.palette.ask,
          state
        );
        drawLabel(
          ctx,
          metrics.valid
            ? `${side === 'long' ? 'Long' : 'Short'} ${formatPrice(entry.price, state.tickSize)}, R ${metrics.riskReward ?? '-'}`
            : 'Target and stop are on the wrong side of entry',
          g.left + 4,
          g.entry.y,
          doc.style.color,
          state
        );
      }
      drawHandles(ctx, g.handles, state);
    },
    hitTest(doc, transform, point, tolerance) {
      const g = geometry(doc, transform);
      if (!g) return null;
      const handle = hitHandles(g.handles, point, tolerance);
      if (handle) return handle;
      const top = Math.min(g.target.y, g.stop.y);
      const bottom = Math.max(g.target.y, g.stop.y);
      const rect = rectFromPoints({x: g.left, y: top}, {x: g.right, y: bottom});
      return pointInRect(point, rect, tolerance) ? {part: 'body'} : null;
    },
    describe: doc => {
      const [entry, target, stop] = doc.anchors;
      return `${side === 'long' ? 'Long' : 'Short'} position entry ${entry?.price ?? '?'}, target ${target?.price ?? '?'}, stop ${stop?.price ?? '?'}`;
    },
  };
}
