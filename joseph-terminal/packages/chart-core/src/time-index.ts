/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Maps arbitrary times to fractional bar indices and back.
 *
 * Drawings anchor to arbitrary times (including the future and gaps), while
 * the renderer positions by logical bar index. Interpolating against the
 * actual bar list keeps anchors correct when bars are missing.
 *
 * Session markets: the chart has one slot per bar and nothing for the time
 * between sessions, so the axis never shows overnight or weekend gaps.
 * Inside the data, a time between sessions maps to the start of the next
 * bar. Outside the data (future anchors, history not loaded) the index
 * counts session bars with the instrument's calendar instead of dividing
 * by the bar duration, so "tomorrow 10:00" lands a few bars to the right,
 * not a night's worth of empty bars. `24x7` calendars keep the exact
 * duration-based arithmetic.
 */

import type {BarCalendar, Timeframe} from '@joseph/contracts';
import type {ChartBar} from './chart-port';

/** Calendar-aware extrapolation (session markets only). */
export interface TimeIndexCalendar {
  /** Session calendar. */
  calendar: BarCalendar;
  /** Chart timeframe. */
  timeframe: Timeframe;
}

/**
 * Most session-calendar conversions cached per index. Anchors are
 * re-projected every frame and calendar arithmetic is costly; the cache is
 * simply emptied when full.
 */
const CACHE_LIMIT = 512;

/**
 * Sorted, de-duplicated bar time index. Logical index `i` is the `i`-th bar
 * start; fractions lie within a bar and negative or past-the-end values lie
 * outside the data.
 */
export class TimeIndex {
  private readonly times: number[];
  private readonly session: TimeIndexCalendar | null;
  /** Off-data `toLogical` results of session calendars, by time. */
  private readonly logicalCache = new Map<number, number>();

  /**
   * Creates the index.
   * @param bars Bars (any order).
   * @param durationMs Bar duration (ms). It extrapolates outside the data
   *     and caps the span of a bar followed by a gap.
   * @param calendar Session calendar and timeframe; omitted or `24x7`
   *     extrapolates by `durationMs`.
   */
  constructor(
    bars: ReadonlyArray<Pick<ChartBar, 'timeMs'>>,
    readonly durationMs: number,
    calendar?: TimeIndexCalendar | null
  ) {
    this.times = [...new Set(bars.map(bar => bar.timeMs))].sort(
      (a, b) => a - b
    );
    this.session = calendar && !calendar.calendar.continuous ? calendar : null;
  }

  /**
   * Number of distinct bar times.
   * @return Count.
   */
  get length(): number {
    return this.times.length;
  }

  /**
   * Whether off-data times are counted in session bars.
   * @return True for exchange calendars.
   */
  get sessionAware(): boolean {
    return this.session !== null;
  }

  /**
   * Returns the time of a bar index.
   * @param index Bar index.
   * @return Bar start time or undefined.
   */
  timeAt(index: number): number | undefined {
    return this.times[index];
  }

  /**
   * Finds the index of the last bar starting at or before a time (binary
   * search).
   * @param timeMs Time (UTC ms).
   * @return Index, or -1 before the first bar.
   */
  floorIndex(timeMs: number): number {
    let lo = 0;
    let hi = this.times.length - 1;
    let found = -1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if ((this.times[mid] ?? Infinity) <= timeMs) {
        found = mid;
        lo = mid + 1;
      } else {
        hi = mid - 1;
      }
    }
    return found;
  }

  /**
   * Returns the length of the bar starting at a time (session bars may be
   * cut short at the close).
   * @param startMs Bar start (UTC ms).
   * @return Length in ms (at least 1); `durationMs` when the calendar
   *     cannot place the bar.
   */
  private barLength(startMs: number): number {
    if (!this.session) return this.durationMs;
    const {calendar, timeframe} = this.session;
    try {
      return Math.max(1, calendar.endOf(timeframe, startMs) - startMs);
    } catch {
      return this.durationMs;
    }
  }

  /**
   * Returns the offset of a time from a bar on the chart, counted in
   * session bars: whole bars between the two bar starts plus the fraction
   * of its own bar that `timeMs` has reached.
   * @param fromMs Bar start on the chart (first or last bar).
   * @param timeMs Time outside the data.
   * @return Fractional bars from `fromMs` (negative before it).
   */
  private sessionOffset(fromMs: number, timeMs: number): number {
    // Callers check `this.session` first.
    const {calendar, timeframe} = this.session as TimeIndexCalendar;
    const bar = calendar.floor(timeframe, timeMs);
    const end = calendar.endOf(timeframe, bar);
    const fraction = Math.min(1, Math.max(0, (timeMs - bar) / (end - bar)));
    const whole =
      bar >= fromMs
        ? calendar.barsBetween(timeframe, fromMs, bar)
        : -calendar.barsBetween(timeframe, bar, fromMs);
    return whole + fraction;
  }

  /**
   * Converts a time to a fractional logical index. Inside the data the
   * fraction is the share of its bar that has elapsed, and a time in a gap
   * after a bar maps to the start of the next bar. Outside the data the
   * index is extrapolated in session bars (exchange calendars) or by
   * `durationMs`.
   * @param timeMs Time (UTC ms).
   * @return Logical index, or null when there are no bars.
   */
  toLogical(timeMs: number): number | null {
    const first = this.times[0];
    const lastIndex = this.times.length - 1;
    const last = this.times[lastIndex];
    if (first === undefined || last === undefined) return null;
    if (this.session && (timeMs < first || timeMs > last)) {
      const cached = this.logicalCache.get(timeMs);
      if (cached !== undefined) return cached;
      let logical: number;
      try {
        logical =
          timeMs < first
            ? this.sessionOffset(first, timeMs)
            : lastIndex + this.sessionOffset(last, timeMs);
      } catch {
        // The calendar cannot place the time: extrapolate by duration.
        logical =
          timeMs < first
            ? (timeMs - first) / this.durationMs
            : lastIndex + (timeMs - last) / this.durationMs;
      }
      if (this.logicalCache.size >= CACHE_LIMIT) this.logicalCache.clear();
      this.logicalCache.set(timeMs, logical);
      return logical;
    }
    if (timeMs <= first) return (timeMs - first) / this.durationMs;
    if (timeMs >= last) return lastIndex + (timeMs - last) / this.durationMs;
    const index = this.floorIndex(timeMs);
    const start = this.times[index] ?? first;
    const next = this.times[index + 1] ?? start + this.durationMs;
    // The span is the bar itself, not the distance to the next bar, so a
    // time in the gap after a bar clamps to `index + 1` (the next bar start).
    const span = Math.max(1, Math.min(next - start, this.barLength(start)));
    return index + Math.min(1, (timeMs - start) / span);
  }

  /**
   * Converts a fractional logical index back to a time: the bar start plus
   * the fraction of the bar's length, so inside the data a time in a gap
   * is never returned. Outside the data it extrapolates like `toLogical`.
   * @param logical Logical index.
   * @return Time (UTC ms), or null when there are no bars.
   */
  toTime(logical: number): number | null {
    const first = this.times[0];
    const lastIndex = this.times.length - 1;
    const last = this.times[lastIndex];
    if (first === undefined || last === undefined) return null;
    if (this.session && (logical < 0 || logical > lastIndex)) {
      const {calendar, timeframe} = this.session;
      const offset = logical < 0 ? logical : logical - lastIndex;
      const whole = Math.floor(offset);
      try {
        const start = calendar.shift(
          timeframe,
          logical < 0 ? first : last,
          whole
        );
        const end = calendar.endOf(timeframe, start);
        return Math.round(start + (offset - whole) * (end - start));
      } catch {
        // Fall through to duration-based extrapolation.
      }
    }
    if (logical <= 0) return first + logical * this.durationMs;
    if (logical >= lastIndex) {
      return last + (logical - lastIndex) * this.durationMs;
    }
    const index = Math.floor(logical);
    const start = this.times[index] ?? first;
    const length = this.session ? this.barLength(start) : this.durationMs;
    return Math.round(start + (logical - index) * length);
  }

  /**
   * Snaps a time to the start of the bar containing it.
   * @param timeMs Time (UTC ms).
   * @return Bar start (extrapolated outside the data), or null when there
   *     are no bars.
   */
  snap(timeMs: number): number | null {
    const logical = this.toLogical(timeMs);
    if (logical === null) return null;
    // The epsilon absorbs float error, so a bar start whose index comes out
    // as 6.9999999 still floors to its own bar.
    return this.toTime(Math.floor(logical + 1e-9));
  }
}
