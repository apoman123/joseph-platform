/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Engine series of engine-backed drawings (anchored VWAP,
 * regression channel).
 *
 * The browser never computes trading values. An engine-backed tool only
 * describes which `/v1/indicators/series` request its anchors imply; the
 * host fetches it (`DrawingSeriesSync`) and the tool renders the returned
 * values. Mapping an anchor time to its bar is pure geometry.
 */

import {
  CONTINUOUS_CALENDAR,
  Decimal,
  type BarCalendar,
  type IndicatorSeriesPoint,
  type Timeframe,
} from '@joseph/contracts';

/**
 * Bars back the engine computes anchored values from (anchored VWAP,
 * regression channel). Informational only: the terminal never refuses an
 * anchor itself; when the engine refuses one, its message is shown.
 */
export const ENGINE_ANCHOR_BAR_CAP = 50_000;

/** Shown while the first response for a request is pending. */
export const SERIES_LOADING_MESSAGE = 'Loading engine values…';

/** Engine series a drawing displays. */
export interface DrawingSeriesRequest {
  /** Engine plugin id, e.g. `anchored_vwap`. */
  pluginId: string;
  /** Inclusive first bar start (UTC ms). */
  fromMs: number;
  /**
   * Plugin inputs. Decimals are strings and integers are numbers, like the
   * per-chart indicator settings.
   */
  settings: Readonly<Record<string, string | number | boolean>>;
}

/** Fetch state of one drawing's series. */
export interface DrawingSeriesState {
  /** `loading` until the first response for `request` arrives. */
  status: 'loading' | 'ready' | 'error';
  /** Request the state answers. */
  request: DrawingSeriesRequest;
  /** Chart timeframe of the request. */
  timeframe: Timeframe;
  /** Session calendar the request was built with (UTC bars when absent). */
  calendar?: BarCalendar;
  /** Points of the latest response, oldest first. */
  points: readonly IndicatorSeriesPoint[];
  /** Response `as_of_ms`, or null before the first response. */
  asOfMs: number | null;
  /** Error message when `status` is `error`. */
  error: string | null;
  /**
   * The engine's own message when it refused the request (a 4xx answer,
   * e.g. an anchor beyond the bars it computes from); null otherwise.
   */
  refusal?: string | null;
}

/**
 * Start of the bar an anchor time belongs to. Anchors are placed at the
 * pointer, which may sit in either half of a bar, so the time is rounded to
 * the nearest bar centre first; on session calendars the second half of a
 * session's last bar rounds to the next session's first bar.
 * @param timeMs Anchor time.
 * @param timeframe Chart timeframe.
 * @param calendar Session calendar of the instrument (default UTC bars).
 * @return Bar start (UTC ms).
 */
export function barStartAt(
  timeMs: number,
  timeframe: Timeframe,
  calendar: BarCalendar = CONTINUOUS_CALENDAR
): number {
  const start = calendar.floor(timeframe, Math.max(0, timeMs));
  const end = calendar.endOf(timeframe, start);
  if (timeMs - start < Math.floor((end - start) / 2)) return start;
  return calendar.continuous ? end : calendar.shift(timeframe, start, 1);
}

/**
 * Stable identity of a request (re-fetch when it changes).
 * @param request Request.
 * @param timeframe Timeframe.
 * @return Key.
 */
export function seriesRequestKey(
  request: DrawingSeriesRequest,
  timeframe: Timeframe
): string {
  const settings = Object.keys(request.settings)
    .sort()
    .map(key => [key, request.settings[key]]);
  return JSON.stringify([
    request.pluginId,
    timeframe,
    request.fromMs,
    settings,
  ]);
}

/**
 * Finds the point of one bar.
 * @param points Points sorted by bar start.
 * @param barStartMs Bar start.
 * @return Point or undefined.
 */
export function seriesPointAt(
  points: readonly IndicatorSeriesPoint[],
  barStartMs: number
): IndicatorSeriesPoint | undefined {
  let lo = 0;
  let hi = points.length - 1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    const start = points[mid]?.bar_start_ms ?? 0;
    if (start === barStartMs) return points[mid];
    if (start < barStartMs) lo = mid + 1;
    else hi = mid - 1;
  }
  return undefined;
}

/**
 * Reads a decimal-string value.
 * @param point Series point.
 * @param key Value key.
 * @return Decimal string, or null when missing, null or malformed.
 */
export function decimalValue(
  point: IndicatorSeriesPoint | undefined,
  key: string
): string | null {
  const value = point?.values[key];
  return typeof value === 'string' && Decimal.isValid(value) ? value : null;
}

/**
 * Reads an integer value.
 * @param point Series point.
 * @param key Value key.
 * @return Integer, or null when missing or not an integer.
 */
export function integerValue(
  point: IndicatorSeriesPoint | undefined,
  key: string
): number | null {
  const value = point?.values[key];
  return typeof value === 'number' && Number.isInteger(value) ? value : null;
}

/**
 * Point-in-time view of a series for bar replay: only bars that closed
 * before `atMs` are known.
 * @param state Series state.
 * @param atMs Replay as-of time, or null when live.
 * @return Filtered state (the same object when live).
 */
export function seriesAsOf(
  state: DrawingSeriesState | null,
  atMs: number | null
): DrawingSeriesState | null {
  if (!state || atMs === null) return state;
  return {
    ...state,
    points: state.points.filter(point => point.bar_start_ms < atMs),
    asOfMs: state.asOfMs === null ? null : Math.min(state.asOfMs, atMs),
  };
}

/**
 * Message for a series that is not ready.
 * @param state Series state (null before any request).
 * @return Loading or error text, or null when ready.
 */
export function seriesStatusMessage(
  state: DrawingSeriesState | null | undefined
): string | null {
  if (!state || state.status === 'loading') return SERIES_LOADING_MESSAGE;
  if (state.status === 'error') {
    if (state.refusal) {
      return state.refusal.charAt(0).toUpperCase() + state.refusal.slice(1);
    }
    return `Engine values unavailable: ${state.error ?? 'request failed'}`;
  }
  return null;
}
