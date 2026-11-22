/**
 * @fileoverview Time-axis presentation per timeframe: seconds on the axis
 * and crosshair label for sub-minute bars, dates only from daily bars up.
 */
import {
  TIMEFRAME_MS,
  isSecondsTimeframe,
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
