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
 * describes which built-in script run its anchors imply (script id, first
 * bar and inputs); the host runs it (`DrawingSeriesSync`) and the tool
 * renders the returned values, read per bar from the script's plots and
 * lines (`seriesPointsFromResult`). Mapping an anchor time to its bar is
 * pure geometry.
 */

import {
  CONTINUOUS_CALENDAR,
  Decimal,
  type BarCalendar,
  type ScriptResult,
  type Timeframe,
} from '@joseph/contracts';

/**
 * Bars a drawing's script run reads at most (the engine's run limit).
 * Informational only: the terminal never refuses an anchor itself; an
 * anchor before the loaded bars is reported by the script.
 */
export const ENGINE_ANCHOR_BAR_CAP = 5_000;

/**
 * Engine values of one bar: script plot values by plot name (decimal
 * strings) and script lines ending on the bar (`<line id>.from_price`,
 * `<line id>.to_price` as decimal strings, `<line id>.from_ms` as an
 * integer).
 */
export interface DrawingSeriesPoint {
  /** Bar start, epoch ms UTC. */
  bar_start_ms: number;
  /** Values by key. */
  values: Readonly<Record<string, string | number | boolean | null>>;
}

/**
 * Converts a script result to per-bar drawing values.
 * @param result Script run result.
 * @return Points sorted by bar start; plot values become decimal strings
 *     with at most 8 decimal places, null values are left out.
 */
export function seriesPointsFromResult(
  result: Pick<ScriptResult, 'plots' | 'visuals'>
): DrawingSeriesPoint[] {
  const points = new Map<number, Record<string, string | number | null>>();
  const at = (time: number) => {
    let values = points.get(time);
    if (!values) {
      values = {};
      points.set(time, values);
    }
    return values;
  };
  const decimal = (value: unknown) =>
    typeof value === 'number' && Number.isFinite(value)
      ? Decimal.fromNumber(value, 8).toString()
      : null;
  for (const plot of result.plots) {
    for (const point of plot.points) {
      const value = decimal(point.value);
      if (value !== null) at(point.time_ms)[plot.name] = value;
    }
  }
  for (const visual of result.visuals ?? []) {
    const {from_ms: from, to_ms: to, from_price, to_price} = visual.data;
    if (visual.kind !== 'line' || typeof to !== 'number') continue;
    const values = at(to);
    values[`${visual.id}.from_price`] = decimal(from_price);
    values[`${visual.id}.to_price`] = decimal(to_price);
    values[`${visual.id}.from_ms`] = typeof from === 'number' ? from : null;
  }
  return [...points]
    .sort(([a], [b]) => a - b)
    .map(([time, values]) => ({bar_start_ms: time, values}));
}

/** Shown while the first response for a request is pending. */
export const SERIES_LOADING_MESSAGE = 'Loading engine values…';

/** Script run a drawing displays. */
export interface DrawingSeriesRequest {
  /** Built-in script id, e.g. `builtin:anchored_vwap`. */
  scriptId: string;
  /** Inclusive first bar start, epoch ms UTC. */
  fromMs: number;
  /** Script inputs. */
  inputs: Readonly<Record<string, string | number | boolean>>;
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
  points: readonly DrawingSeriesPoint[];
  /** Response `as_of_ms` (epoch ms), or null before the first response. */
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
 * Returns the start of the bar an anchor time belongs to. Anchors are
 * placed at the pointer, which may sit in either half of a bar, so the time
 * is rounded to the nearest bar boundary: the first half of a bar maps to
 * its start, the second half to the next bar's start. On session calendars
 * the second half of a session's last bar maps to the next session's first
 * bar.
 * @param timeMs Anchor time in epoch ms UTC; negative times count as 0.
 * @param timeframe Chart timeframe.
 * @param calendar Session calendar of the instrument (default UTC bars).
 * @return Bar start in epoch ms UTC.
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
 * Returns a stable identity of a request; the series is re-fetched when it
 * changes.
 * @param request Series request.
 * @param timeframe Chart timeframe the request is sent with.
 * @return JSON key, independent of the order of the settings' keys.
 */
export function seriesRequestKey(
  request: DrawingSeriesRequest,
  timeframe: Timeframe
): string {
  const inputs = Object.keys(request.inputs)
    .sort()
    .map(key => [key, request.inputs[key]]);
  return JSON.stringify([
    request.scriptId,
    timeframe,
    request.fromMs,
    inputs,
  ]);
}

/**
 * Finds the point of one bar by binary search.
 * @param points Points sorted by bar start, ascending.
 * @param barStartMs Exact bar start in epoch ms UTC.
 * @return Point, or undefined when no point starts at that time.
 */
export function seriesPointAt(
  points: readonly DrawingSeriesPoint[],
  barStartMs: number
): DrawingSeriesPoint | undefined {
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
 * Reads a decimal-string value of a series point.
 * @param point Series point, or undefined for none.
 * @param key Value key, e.g. `vwap`.
 * @return Decimal string, or null when missing, null or malformed.
 */
export function decimalValue(
  point: DrawingSeriesPoint | undefined,
  key: string
): string | null {
  const value = point?.values[key];
  return typeof value === 'string' && Decimal.isValid(value) ? value : null;
}

/**
 * Reads an integer value of a series point.
 * @param point Series point, or undefined for none.
 * @param key Value key, e.g. `bars`.
 * @return Integer, or null when missing or not an integer.
 */
export function integerValue(
  point: DrawingSeriesPoint | undefined,
  key: string
): number | null {
  const value = point?.values[key];
  return typeof value === 'number' && Number.isInteger(value) ? value : null;
}

/**
 * Returns the point-in-time view of a series for bar replay: only points of
 * bars that start before `atMs` are kept (with `atMs` on a bar boundary,
 * the bars closed by then).
 * @param state Series state, or null without one.
 * @param atMs Replay as-of time in epoch ms UTC, or null when live.
 * @return Filtered copy with `asOfMs` capped at `atMs`; `state` itself
 *     when live or null.
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
 * Returns the message for a series that is not ready.
 * @param state Series state (null or undefined before any request).
 * @return Loading text, the engine's refusal (capitalized) or a generic
 *     error text, or null when ready.
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
