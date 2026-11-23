/**
 * @fileoverview Time-axis presentation per timeframe: seconds on the axis
 * and crosshair label for sub-minute bars, dates only from daily bars up,
 * and exchange-local labels for session markets (a TWSE chart reads
 * 09:00-13:30, an NYSE chart 09:30-16:00). `24x7` markets keep UTC labels.
 */
import {
  TIMEFRAME_MS,
  isSecondsTimeframe,
  zonedParts,
  type Timeframe,
} from '@joseph/contracts';

/** Time-axis label options (mirrors the chart library's time scale). */
export interface TimeScaleLabels {
  /** Show hours and minutes. */
  timeVisible: boolean;
  /** Show seconds too. */
  secondsVisible: boolean;
}

/**
 * Chooses time-axis labels for a timeframe.
 * @param timeframe Chart timeframe.
 * @return Label options.
 */
export function timeScaleLabelsFor(timeframe: Timeframe): TimeScaleLabels {
  return {
    timeVisible: TIMEFRAME_MS[timeframe] < TIMEFRAME_MS['1d'],
    secondsVisible: isSecondsTimeframe(timeframe),
  };
}

/** Tick label granularity (the chart library's tick mark types). */
export type TickLabelKind = 'year' | 'month' | 'day' | 'time' | 'seconds';

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
 * Pads to two digits.
 * @param value Number.
 * @return Text.
 */
function pad(value: number): string {
  return String(value).padStart(2, '0');
}

/**
 * Formats a time-axis tick in a time zone (shortest form, ≤ 8 characters).
 * @param timeMs UTC ms.
 * @param kind Tick granularity.
 * @param timeZone IANA time zone.
 * @return Label such as `2026`, `Nov`, `23`, `09:30` or `09:30:05`.
 */
export function zonedTickLabel(
  timeMs: number,
  kind: TickLabelKind,
  timeZone: string
): string {
  const p = zonedParts(timeMs, timeZone);
  switch (kind) {
    case 'year':
      return String(p.year);
    case 'month':
      return MONTHS[p.month - 1] ?? '';
    case 'day':
      return String(p.day);
    case 'seconds':
      return `${pad(p.hour)}:${pad(p.minute)}:${pad(p.second)}`;
    default:
      return `${pad(p.hour)}:${pad(p.minute)}`;
  }
}

/**
 * Short zone label for captions, e.g. `ET` or `TPE`.
 * @param timeZone IANA time zone.
 * @return Abbreviation.
 */
export function zoneAbbreviation(timeZone: string): string {
  if (timeZone === 'America/New_York') return 'ET';
  if (timeZone === 'Asia/Taipei') return 'TPE';
  if (timeZone === 'UTC') return 'UTC';
  return timeZone.split('/').at(-1)?.replace(/_/g, ' ') ?? timeZone;
}

/**
 * Formats the crosshair time label in a time zone, e.g.
 * `Mon 23 Nov '26 09:30 ET` (dates only from daily bars up).
 * @param timeMs Bar start (UTC ms).
 * @param timeframe Chart timeframe.
 * @param timeZone IANA time zone.
 * @return Label.
 */
export function zonedCrosshairLabel(
  timeMs: number,
  timeframe: Timeframe,
  timeZone: string
): string {
  const p = zonedParts(timeMs, timeZone);
  const date = `${p.day} ${MONTHS[p.month - 1] ?? ''} '${pad(p.year % 100)}`;
  const labels = timeScaleLabelsFor(timeframe);
  if (!labels.timeVisible) return date;
  const time = labels.secondsVisible
    ? `${pad(p.hour)}:${pad(p.minute)}:${pad(p.second)}`
    : `${pad(p.hour)}:${pad(p.minute)}`;
  return `${date} ${time} ${zoneAbbreviation(timeZone)}`;
}
