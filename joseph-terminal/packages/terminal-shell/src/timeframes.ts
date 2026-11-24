/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Timeframe choices: TradingView-style typed resolutions
 * (`15`, `4H`, `1D`, `5S`, `1M`), menu groups, what a venue offers
 * (from its published capabilities) and the countdown to the bar close.
 * Resolutions the engine does not offer are reported, never approximated.
 */

import {
  TIMEFRAMES,
  TIMEFRAME_MS,
  floorToTimeframe,
  isCalendarTimeframe,
  timeframeEnd,
  type BarCalendar,
  type Timeframe,
  type VenueCapabilities,
} from '@joseph/contracts';

/** Parse result of a custom timeframe. */
export type TimeframeParse =
  {ok: true; timeframe: Timeframe} | {ok: false; error: string};

/** Fixed-length units in milliseconds. */
const UNITS: Readonly<Record<string, number>> = {
  s: 1_000,
  m: 60_000,
  h: 3_600_000,
  d: 86_400_000,
  w: 604_800_000,
};

/** Help text shown for unparseable input. */
const PARSE_HINT = 'Type a resolution such as 15, 4h, 1D, 5S or 1M.';

/**
 * Maps a typed unit to a canonical unit. Case matters only for `m`/`M`:
 * lowercase is minutes, uppercase (or `mo`, `mon`, `month`) is months.
 * @param raw Unit text (may be empty).
 * @return `s`, `m`, `h`, `d`, `w`, `M` or null when unknown.
 */
function canonicalUnit(raw: string): string | null {
  if (raw === '' || raw === 'm') return 'm';
  if (raw === 'M') return 'M';
  const lower = raw.toLowerCase();
  if (/^(mo|mon|month|months)$/.test(lower)) return 'M';
  if (/^(min|mins|minute|minutes)$/.test(lower)) return 'm';
  if (/^(s|sec|secs|second|seconds)$/.test(lower)) return 's';
  if (/^(h|hr|hrs|hour|hours)$/.test(lower)) return 'h';
  if (/^(d|day|days)$/.test(lower)) return 'd';
  if (/^(w|wk|week|weeks)$/.test(lower)) return 'w';
  return null;
}

/**
 * Normalizes an optional offered-timeframe list.
 * @param offered Timeframes the venue offers (null = unknown, allow all).
 * @return Set or null.
 */
function offeredSet(
  offered: Iterable<Timeframe> | null | undefined
): ReadonlySet<Timeframe> | null {
  return offered ? new Set(offered) : null;
}

/**
 * Parses a typed resolution. A bare number means minutes, as on most
 * charting tools; `S` is seconds and uppercase `M` is calendar months.
 * @param input Text such as `15`, `15m`, `4h`, `1D`, `1W`, `5S`, `1M`.
 * @param offered Timeframes the chart's venue offers; null or omitted
 *     accepts every contract timeframe.
 * @return Supported timeframe or an actionable error.
 */
export function parseTimeframeInput(
  input: string,
  offered?: Iterable<Timeframe> | null
): TimeframeParse {
  const text = input.trim();
  const match = /^(\d{1,5})?\s*([A-Za-z]*)$/.exec(text);
  if (!text || !match) return {ok: false, error: PARSE_HINT};
  const count = Number(match[1] ?? '1');
  const unit = canonicalUnit(match[2] ?? '');
  if (unit === null || !Number.isInteger(count) || count <= 0) {
    return {ok: false, error: PARSE_HINT};
  }
  let timeframe: Timeframe | undefined;
  let label: string;
  if (unit === 'M') {
    timeframe = count === 1 ? '1M' : undefined;
    label = `${count}M`;
  } else {
    const durationMs = count * (UNITS[unit] ?? 0);
    // Calendar months have no fixed length, so `31D` is never a month.
    timeframe = TIMEFRAMES.find(
      tf => !isCalendarTimeframe(tf) && TIMEFRAME_MS[tf] === durationMs
    );
    label = `${count}${unit}`;
  }
  if (!timeframe) {
    return {
      ok: false,
      error: `${label} bars are not offered. Available: ${TIMEFRAMES.join(', ')}.`,
    };
  }
  const allowed = offeredSet(offered);
  if (allowed && !allowed.has(timeframe)) {
    return {
      ok: false,
      error: `${timeframe} bars are not offered by this venue. Available: ${TIMEFRAMES.filter(tf => allowed.has(tf)).join(', ')}.`,
    };
  }
  return {ok: true, timeframe};
}

/** A labelled group of timeframes in the timeframe menu. */
export interface TimeframeGroup {
  /** Group label. */
  label: string;
  /** Timeframes, shortest first. */
  timeframes: readonly Timeframe[];
}

/** Timeframe menu groups (every contract timeframe appears once). */
export const TIMEFRAME_GROUPS: readonly TimeframeGroup[] = [
  {label: 'Seconds', timeframes: ['1s', '5s', '15s', '30s']},
  {label: 'Minutes', timeframes: ['1m', '3m', '5m', '15m', '30m']},
  {label: 'Hours', timeframes: ['1h', '2h', '4h', '6h', '12h']},
  {label: 'Days', timeframes: ['1d', '1w', '1M']},
];

/** Unit words for timeframe titles. */
const UNIT_WORDS: Readonly<Record<string, string>> = {
  s: 'second',
  m: 'minute',
  h: 'hour',
  d: 'day',
  w: 'week',
  M: 'month',
};

/**
 * Spells a timeframe out, so `1m` and `1M` are never confused.
 * @param timeframe Timeframe.
 * @return Text such as `15 seconds`, `1 minute` or `1 month`.
 */
export function timeframeTitle(timeframe: Timeframe): string {
  const match = /^(\d+)([smhdwM])$/.exec(timeframe);
  const count = Number(match?.[1] ?? '1');
  const word = UNIT_WORDS[match?.[2] ?? 'm'] ?? 'minute';
  return `${count} ${word}${count === 1 ? '' : 's'}`;
}

/** Whether a venue offers a timeframe, and how. */
export interface TimeframeAvailability {
  /** Whether the chart can request it on this venue. */
  offered: boolean;
  /** Bars the engine builds from the venue's public trades. */
  tradeBuilt: boolean;
  /** Tooltip explaining the state. */
  note: string;
}

/** Venue capability fields that decide timeframe availability. */
export type TimeframeCapabilities = Pick<
  VenueCapabilities,
  'display_name' | 'timeframes' | 'trade_built_timeframes'
>;

/**
 * Timeframes a venue offers according to its published capabilities.
 * @param capabilities Venue capabilities (null while unknown).
 * @return Offered timeframes, or null when unknown (nothing is hidden).
 */
export function offeredTimeframes(
  capabilities: TimeframeCapabilities | null | undefined
): ReadonlySet<Timeframe> | null {
  if (!capabilities) return null;
  return new Set([
    ...capabilities.timeframes,
    ...(capabilities.trade_built_timeframes ?? []),
  ]);
}

/**
 * Describes a timeframe for a venue (menu state and tooltip).
 * @param timeframe Timeframe.
 * @param capabilities Venue capabilities (null while unknown).
 * @return Availability.
 */
export function timeframeAvailability(
  timeframe: Timeframe,
  capabilities: TimeframeCapabilities | null | undefined
): TimeframeAvailability {
  const title = timeframeTitle(timeframe);
  if (!capabilities) return {offered: true, tradeBuilt: false, note: title};
  const tradeBuilt = (capabilities.trade_built_timeframes ?? []).includes(
    timeframe
  );
  const offered = tradeBuilt || capabilities.timeframes.includes(timeframe);
  if (!offered) {
    return {
      offered,
      tradeBuilt,
      note: `${title}: not offered by ${capabilities.display_name}`,
    };
  }
  return {
    offered,
    tradeBuilt,
    note: tradeBuilt
      ? `${title}: built by the engine from ${capabilities.display_name} public trades; history is limited to the venue's trade history`
      : title,
  };
}

/**
 * Time left until the bar containing `nowMs` closes (calendar-aware).
 * @param timeframe Chart timeframe.
 * @param nowMs Clock.
 * @param calendar Session calendar (default UTC bars, which never pause).
 * @return Milliseconds until the close (> 0; 0 between sessions).
 */
export function msUntilBarClose(
  timeframe: Timeframe,
  nowMs: number,
  calendar?: BarCalendar | null
): number {
  if (!calendar || calendar.continuous) {
    return timeframeEnd(timeframe, floorToTimeframe(timeframe, nowMs)) - nowMs;
  }
  const end = calendar.endOf(timeframe, calendar.floor(timeframe, nowMs));
  return Math.max(0, end - nowMs);
}

/** What the bar countdown chip shows. */
export type BarCountdown =
  {kind: 'closes'; ms: number} | {kind: 'opens'; ms: number} | {kind: 'closed'};

/**
 * Countdown of the chart's bar: time to the bar close while in session,
 * otherwise time to the next session open.
 * @param timeframe Chart timeframe.
 * @param nowMs Clock.
 * @param calendar Session calendar (default UTC bars).
 * @return Countdown.
 */
export function barCountdown(
  timeframe: Timeframe,
  nowMs: number,
  calendar?: BarCalendar | null
): BarCountdown {
  const closes = msUntilBarClose(timeframe, nowMs, calendar);
  if (closes > 0 || !calendar) return {kind: 'closes', ms: closes};
  try {
    const next = calendar.shift(timeframe, nowMs, 0);
    return {kind: 'opens', ms: Math.max(0, next - nowMs)};
  } catch {
    return {kind: 'closed'};
  }
}

/**
 * Formats a countdown TradingView-style: `0:42`, `12:05`, `3:04:15` or,
 * from a day up, `6d 04:15`.
 * @param ms Remaining milliseconds.
 * @return Countdown text.
 */
export function formatCountdown(ms: number): string {
  const total = Math.max(0, Math.ceil(ms / 1000));
  const days = Math.floor(total / 86_400);
  const hours = Math.floor((total % 86_400) / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  const pad = (n: number) => String(n).padStart(2, '0');
  if (days > 0) return `${days}d ${pad(hours)}:${pad(minutes)}`;
  if (hours > 0) return `${hours}:${pad(minutes)}:${pad(seconds)}`;
  return `${minutes}:${pad(seconds)}`;
}
