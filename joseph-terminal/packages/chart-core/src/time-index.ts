/**
 * @fileoverview Maps arbitrary times to fractional bar indices and back.
 *
 * Drawings anchor to arbitrary times (including the future and gaps), while
 * the renderer positions by logical bar index. Interpolating against the
 * actual bar list keeps anchors correct when bars are missing.
 */
import type {ChartBar} from './chart-port';

/** Sorted, de-duplicated bar time index. */
export class TimeIndex {
  private readonly times: number[];

  /**
   * Creates the index.
   * @param bars Bars (any order).
   * @param durationMs Bar duration used to extrapolate outside the data.
   */
  constructor(
    bars: readonly Pick<ChartBar, 'timeMs'>[],
    readonly durationMs: number
  ) {
    this.times = [...new Set(bars.map(bar => bar.timeMs))].sort(
      (a, b) => a - b
    );
  }

  /**
   * Number of bars.
   * @return Count.
   */
  get length(): number {
    return this.times.length;
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
   * Converts a time to a fractional logical index.
   * @param timeMs Time.
   * @return Logical index, or null when there are no bars.
   */
  toLogical(timeMs: number): number | null {
    const first = this.times[0];
    const lastIndex = this.times.length - 1;
    const last = this.times[lastIndex];
    if (first === undefined || last === undefined) return null;
    if (timeMs <= first) return (timeMs - first) / this.durationMs;
    if (timeMs >= last) return lastIndex + (timeMs - last) / this.durationMs;
    const index = this.floorIndex(timeMs);
    const start = this.times[index] ?? first;
    const next = this.times[index + 1] ?? start + this.durationMs;
    const span = Math.max(1, Math.min(next - start, this.durationMs));
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
    if (logical <= 0) return first + logical * this.durationMs;
    if (logical >= lastIndex) {
      return last + (logical - lastIndex) * this.durationMs;
    }
    const index = Math.floor(logical);
    const start = this.times[index] ?? first;
    return Math.round(start + (logical - index) * this.durationMs);
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
