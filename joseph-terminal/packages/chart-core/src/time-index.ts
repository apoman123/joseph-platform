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

/** Cached conversions per index (anchors are re-projected every frame). */
const CACHE_LIMIT = 512;

/** Sorted, de-duplicated bar time index. */
export class TimeIndex {
  private readonly times: number[];
  private readonly session: TimeIndexCalendar | null;
  private readonly logicalCache = new Map<number, number>();

  /**
   * Creates the index.
   * @param bars Bars (any order).
   * @param durationMs Bar duration used to extrapolate outside the data.
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
   * Number of bars.
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
   * Finds the index of the last bar starting at or before a time.
   * @param timeMs Time.
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
   * Length of the bar starting at a time (session bars may be cut short at
   * the close).
   * @param startMs Bar start.
   * @return Length in ms (at least 1).
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
   * Session-bar offset of a time from a bar on the chart.
   * @param fromMs Bar start on the chart (first or last bar).
   * @param timeMs Time outside the data.
   * @return Fractional bars from `fromMs` (negative before it).
   */
  private sessionOffset(fromMs: number, timeMs: number): number {
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
   * Converts a time to a fractional logical index.
   * @param timeMs Time.
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
    const span = Math.max(1, Math.min(next - start, this.barLength(start)));
    return index + Math.min(1, (timeMs - start) / span);
  }

  /**
   * Converts a fractional logical index back to a time.
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
   * @param timeMs Time.
   * @return Bar start (extrapolated outside the data).
   */
  snap(timeMs: number): number | null {
    const logical = this.toLogical(timeMs);
    if (logical === null) return null;
    return this.toTime(Math.floor(logical + 1e-9));
  }
}
