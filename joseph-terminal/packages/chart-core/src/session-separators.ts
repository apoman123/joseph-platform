/**
 * @fileoverview Session separators: thin vertical lines where a new UTC
 * session (day, week or month, depending on the timeframe) begins.
 *
 * Crypto perpetuals trade around the clock, so sessions are calendar
 * boundaries in UTC rather than exchange opening hours. The layer is pure
 * presentation; it never changes bars or indicator inputs.
 */
import type {Timeframe} from '@joseph/contracts';
import {TIMEFRAME_MS} from '@joseph/contracts';
import type {CanvasLayer, CoordinateTransform} from './chart-port';
import {withAlpha} from './theme';

/** Calendar unit that separates sessions on a timeframe. */
export type SessionUnit = 'day' | 'week' | 'month';

/** Weekday names used in labels (UTC). */
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
 * Chooses the session unit for a timeframe: days below 1d, weeks on 1d and
 * months on 1w.
 * @param timeframe Chart timeframe.
 * @return Session unit.
 */
export function sessionUnitFor(timeframe: Timeframe): SessionUnit {
  const duration = TIMEFRAME_MS[timeframe];
  if (duration < TIMEFRAME_MS['1d']) return 'day';
  if (duration < TIMEFRAME_MS['1w']) return 'week';
  return 'month';
}

/**
 * Returns a key identifying the session that contains a time.
 * @param timeMs UTC ms.
 * @param unit Session unit.
 * @return Key; equal keys mean the same session.
 */
function sessionKey(timeMs: number, unit: SessionUnit): number {
  const date = new Date(timeMs);
  if (unit === 'month') return date.getUTCFullYear() * 12 + date.getUTCMonth();
  const day = Math.floor(timeMs / 86_400_000);
  // 1970-01-01 was a Thursday; shift so weeks start on Monday.
  return unit === 'week' ? Math.floor((day + 3) / 7) : day;
}

/**
 * Finds the first bar of every session after the first visible one.
 * @param times Bar start times in ascending order.
 * @param timeframe Chart timeframe.
 * @return Bar start times where a new session begins.
 */
export function sessionBoundaries(
  times: readonly number[],
  timeframe: Timeframe
): number[] {
  const unit = sessionUnitFor(timeframe);
  const result: number[] = [];
  let previous: number | null = null;
  for (const time of times) {
    const key = sessionKey(time, unit);
    if (previous !== null && key !== previous) result.push(time);
    previous = key;
  }
  return result;
}

/**
 * Formats a separator label such as `Tue 12`, `Mon 9 Jun` or `Jun 2026`.
 * @param timeMs Session start (UTC ms).
 * @param unit Session unit.
 * @return Label.
 */
export function sessionLabel(timeMs: number, unit: SessionUnit): string {
  const date = new Date(timeMs);
  const day = date.getUTCDate();
  const month = MONTHS[date.getUTCMonth()] ?? '';
  if (unit === 'month') return `${month} ${date.getUTCFullYear()}`;
  const weekday = WEEKDAYS[date.getUTCDay()] ?? '';
  return unit === 'week' ? `${weekday} ${day} ${month}` : `${weekday} ${day}`;
}

/** Inputs of the separator layer, read on every frame. */
export interface SessionSeparatorOptions {
  /** Bar start times shown on the chart (ascending). */
  times: () => readonly number[];
  /** Chart timeframe. */
  timeframe: () => Timeframe;
  /** Line and label color (CSS hex). */
  color: () => string;
  /** Font family for labels. */
  fontFamily: () => string;
}

/**
 * Paints separators for the given boundaries.
 * @param ctx Context in CSS pixels.
 * @param transform Frame transform.
 * @param boundaries Session start times.
 * @param unit Session unit (for labels).
 * @param color Line color.
 * @param fontFamily Label font.
 */
export function paintSessionSeparators(
  ctx: CanvasRenderingContext2D,
  transform: CoordinateTransform,
  boundaries: readonly number[],
  unit: SessionUnit,
  color: string,
  fontFamily: string
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
    const px = Math.round(x) + 0.5;
    ctx.beginPath();
    ctx.moveTo(px, 0);
    ctx.lineTo(px, transform.height);
    ctx.stroke();
    if (x - lastLabelX > 56) {
      ctx.fillStyle = withAlpha(color, 0.9);
      ctx.fillText(sessionLabel(time, unit), px + 3, transform.height - 3);
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
      paintSessionSeparators(
        ctx,
        transform,
        sessionBoundaries(options.times(), timeframe),
        sessionUnitFor(timeframe),
        options.color(),
        options.fontFamily()
      );
    },
  };
}
