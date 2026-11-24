/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Session separators: thin vertical lines where a new
 * session (minute, hour, day, week, month or year, depending on the
 * timeframe) begins.
 *
 * Crypto perpetuals trade around the clock, so their sessions are calendar
 * boundaries in UTC. Exchange-listed instruments use the exchange's local
 * date: on intraday charts every trading session starts with a break (the
 * chart has no bars between sessions, so the break marks where the
 * overnight or weekend gap was taken out). The layer is pure presentation;
 * it never changes bars or indicator inputs.
 */

import {
  TIMEFRAME_MS,
  isCalendarTimeframe,
  zonedParts,
  type Timeframe,
} from '@joseph/contracts';
import type {CanvasLayer, CoordinateTransform} from './chart-port';
import {withAlpha} from './theme';

/** Calendar unit that separates sessions on a timeframe. */
export type SessionUnit = 'minute' | 'hour' | 'day' | 'week' | 'month' | 'year';

/** Weekday names used in labels, indexed by `Date.getUTCDay()`. */
const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
/** Month names used in labels. */
const MONTHS = [
  'Jan',
  'Feb',
  'Mar',
  'Apr',
  'May',
  'Jun',
  'Jul',
  'Aug',
  'Sep',
  'Oct',
  'Nov',
  'Dec',
];

/**
 * Chooses the session unit for a timeframe: minutes on 1s, hours on 5s to
 * 30s, days from 1m below 1d, weeks on 1d, months on 1w and years on 1M.
 * @param timeframe Chart timeframe.
 * @return Session unit.
 */
export function sessionUnitFor(timeframe: Timeframe): SessionUnit {
  if (isCalendarTimeframe(timeframe)) return 'year';
  const duration = TIMEFRAME_MS[timeframe];
  if (duration < TIMEFRAME_MS['5s']) return 'minute';
  if (duration < TIMEFRAME_MS['1m']) return 'hour';
  if (duration < TIMEFRAME_MS['1d']) return 'day';
  if (duration < TIMEFRAME_MS['1w']) return 'week';
  return 'month';
}

/**
 * Returns a key identifying the session that contains a time.
 * @param timeMs UTC ms.
 * @param unit Session unit.
 * @param timeZone Time zone of the dates (`UTC` for 24x7 markets).
 * @return Key; equal keys mean the same session.
 */
function sessionKey(
  timeMs: number,
  unit: SessionUnit,
  timeZone: string
): number {
  if (timeZone !== 'UTC') {
    // Count local calendar days since 1970-01-01 so keys follow the
    // exchange's dates; weeks use the same Monday start as below.
    const p = zonedParts(timeMs, timeZone);
    const day = Date.UTC(p.year, p.month - 1, p.day) / 86_400_000;
    if (unit === 'year') return p.year;
    if (unit === 'month') return p.year * 12 + p.month - 1;
    if (unit === 'minute') return day * 1440 + p.hour * 60 + p.minute;
    if (unit === 'hour') return day * 24 + p.hour;
    return unit === 'week' ? Math.floor((day + 3) / 7) : day;
  }
  const date = new Date(timeMs);
  if (unit === 'year') return date.getUTCFullYear();
  if (unit === 'month') return date.getUTCFullYear() * 12 + date.getUTCMonth();
  if (unit === 'minute') return Math.floor(timeMs / 60_000);
  if (unit === 'hour') return Math.floor(timeMs / 3_600_000);
  const day = Math.floor(timeMs / 86_400_000);
  // 1970-01-01 was a Thursday; shift so weeks start on Monday.
  return unit === 'week' ? Math.floor((day + 3) / 7) : day;
}

/**
 * Finds the first bar of every session except the first session in `times`
 * (its start is not known to be on the chart).
 * @param times Bar start times in ascending order.
 * @param timeframe Chart timeframe.
 * @param timeZone Exchange time zone (default `UTC`, 24x7 markets).
 * @return Bar start times where a new session begins.
 */
export function sessionBoundaries(
  times: readonly number[],
  timeframe: Timeframe,
  timeZone = 'UTC'
): number[] {
  const unit = sessionUnitFor(timeframe);
  const result: number[] = [];
  let previous: number | null = null;
  for (const time of times) {
    const key = sessionKey(time, unit, timeZone);
    if (previous !== null && key !== previous) result.push(time);
    previous = key;
  }
  return result;
}

/**
 * Formats a separator label such as `14:05`, `Tue 12`, `Mon 9 Jun`,
 * `Jun 2026` or `2026`. Minute and hour separators at midnight name the
 * day instead.
 * @param timeMs Session start (UTC ms).
 * @param unit Session unit.
 * @param timeZone Time zone of the label (default `UTC`).
 * @return Label.
 */
export function sessionLabel(
  timeMs: number,
  unit: SessionUnit,
  timeZone = 'UTC'
): string {
  let shifted = timeMs;
  if (timeZone !== 'UTC') {
    // Encode the zone's wall-clock time as a UTC instant, so the getUTC*
    // readers below return exchange-local fields.
    const p = zonedParts(timeMs, timeZone);
    shifted = Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute, p.second);
  }
  const date = new Date(shifted);
  const day = date.getUTCDate();
  const month = MONTHS[date.getUTCMonth()] ?? '';
  const weekday = WEEKDAYS[date.getUTCDay()] ?? '';
  if (unit === 'year') return String(date.getUTCFullYear());
  if (unit === 'month') return `${month} ${date.getUTCFullYear()}`;
  if (unit === 'minute' || unit === 'hour') {
    const midnight = date.getUTCHours() === 0 && date.getUTCMinutes() === 0;
    return midnight ? `${weekday} ${day}` : date.toISOString().slice(11, 16);
  }
  return unit === 'week' ? `${weekday} ${day} ${month}` : `${weekday} ${day}`;
}

/** Inputs of the separator layer, read on every frame. */
export interface SessionSeparatorOptions {
  /** Bar start times shown on the chart (ascending). */
  times: () => readonly number[];
  /** Chart timeframe. */
  timeframe: () => Timeframe;
  /** Exchange time zone (`UTC` for 24x7 markets; default `UTC`). */
  timeZone?: () => string;
  /** Line and label color (CSS hex). */
  color: () => string;
  /** Font family for labels. */
  fontFamily: () => string;
}

/**
 * Paints dotted separators for the boundaries inside the plot, labelled at
 * the bottom; a label within 56 px of the previous label is skipped so the
 * labels do not crowd.
 * @param ctx Context in CSS pixels.
 * @param transform Frame transform.
 * @param boundaries Session start times (UTC ms).
 * @param unit Session unit (for labels).
 * @param color Line and label color (`#rrggbb` gets transparency applied).
 * @param fontFamily Label font.
 * @param timeZone Time zone of the labels (default `UTC`).
 */
export function paintSessionSeparators(
  ctx: CanvasRenderingContext2D,
  transform: CoordinateTransform,
  boundaries: readonly number[],
  unit: SessionUnit,
  color: string,
  fontFamily: string,
  timeZone = 'UTC'
): void {
  ctx.save();
  ctx.strokeStyle = withAlpha(color, 0.55);
  ctx.lineWidth = 1;
  ctx.setLineDash([2, 4]);
  ctx.font = `10px ${fontFamily}`;
  ctx.textBaseline = 'bottom';
  let lastLabelX = -Infinity;
  for (const time of boundaries) {
    const x = transform.timeToX(time);
    if (x === null || x < 0 || x > transform.width) continue;
    // Half-pixel x keeps the 1 px line on one pixel column (sharp).
    const px = Math.round(x) + 0.5;
    ctx.beginPath();
    ctx.moveTo(px, 0);
    ctx.lineTo(px, transform.height);
    ctx.stroke();
    if (x - lastLabelX > 56) {
      ctx.fillStyle = withAlpha(color, 0.9);
      ctx.fillText(
        sessionLabel(time, unit, timeZone),
        px + 3,
        transform.height - 3
      );
      lastLabelX = x;
    }
  }
  ctx.restore();
}

/**
 * Creates the canvas layer that draws session separators.
 * @param options Accessors evaluated on every frame.
 * @return Canvas layer (painted below drawings).
 */
export function createSessionSeparatorLayer(
  options: SessionSeparatorOptions
): CanvasLayer {
  return {
    id: 'session-separators',
    zIndex: 5,
    draw(ctx, transform) {
      const timeframe = options.timeframe();
      const timeZone = options.timeZone?.() ?? 'UTC';
      paintSessionSeparators(
        ctx,
        transform,
        sessionBoundaries(options.times(), timeframe, timeZone),
        sessionUnitFor(timeframe),
        options.color(),
        options.fontFamily(),
        timeZone
      );
    },
  };
}
