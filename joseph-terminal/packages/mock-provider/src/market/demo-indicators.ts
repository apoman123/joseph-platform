/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Static DEMO indicator fixture: the pivot levels of the
 * `pivot_points` built-in.
 *
 * Records are hand-written shapes, not computed from candles in the browser
 * and not exported by the Python engine: each AOI (area of interest) is
 * stored relative to the latest closed bar (`barsAgo`) and the latest close
 * (`lowerRatio`/`upperRatio`) and is materialized at request time by
 * `DemoMarketDataClient.getAois`, so it fits any instrument and timeframe.
 * The levels follow the classic floor formulas from a made-up previous
 * period's high, low and close; the period before that is kept as an
 * expired set.
 */

import type {AoiKind, AoiState, ZoneSide} from '@joseph/contracts';

/** Relative AOI fixture entry. */
export interface RelativeAoi {
  /** Stable id part; the record id is `demo-<key>-<timeframe>`. */
  key: string;
  /** Producing plugin id. */
  source: string;
  /** AOI kind. */
  kind: AoiKind;
  /** Side. */
  side: ZoneSide;
  /** State. */
  state: AoiState;
  /** Bars before the latest closed bar where it originates. */
  barsAgo: number;
  /** Bars after the origin bar until confirmation, at that bar's close. */
  confirmBars: number;
  /** Lower bound as a ratio of the latest close. */
  lowerRatio: number;
  /** Upper bound as a ratio of the latest close. */
  upperRatio: number;
  /** Quality score 0-100, or null for kinds without one. */
  score: number | null;
  /**
   * Bars before the latest closed bar when an ended AOI ended (its
   * `invalidated_at_ms` is that bar's start); unset while it is live.
   */
  endedBarsAgo?: number;
}

/** High, low and close of a period, as ratios of the latest close. */
interface PeriodRatios {
  /** Period high. */
  high: number;
  /** Period low. */
  low: number;
  /** Period close. */
  close: number;
}

/**
 * Computes the classic floor pivot levels of a period.
 * @param period High, low and close.
 * @return Levels by kind suffix (`P`, `R1` … `R3`, `S1` … `S3`), in the
 *     same unit as the inputs.
 */
export function pivotLevels(period: PeriodRatios): Record<string, number> {
  const {high, low, close} = period;
  const p = (high + low + close) / 3;
  return {
    P: p,
    R1: 2 * p - low,
    R2: p + (high - low),
    R3: high + 2 * (p - low),
    S1: 2 * p - high,
    S2: p - (high - low),
    S3: low - 2 * (high - p),
  };
}

/** Side of each pivot level: resistance above, support below. */
const PIVOT_SIDES: Readonly<Record<string, ZoneSide>> = {
  P: 'neutral',
  R1: 'bearish',
  R2: 'bearish',
  R3: 'bearish',
  S1: 'bullish',
  S2: 'bullish',
  S3: 'bullish',
};

/** Bars from the start of the current pivot period to the latest bar. */
const CURRENT_PERIOD_BARS = 96;

/**
 * Builds the seven level fixtures of one pivot period.
 * @param keyPrefix Id prefix, e.g. `pivot` (record ids `pivot-s1` …).
 * @param period Previous period's high, low and close.
 * @param barsAgo Bars before the latest closed bar the period starts.
 * @param endedBarsAgo Bars before the latest closed bar the levels expired,
 *     or undefined while they are live.
 * @return Level fixtures in the order P, R1 … R3, S1 … S3.
 */
function pivotPeriod(
  keyPrefix: string,
  period: PeriodRatios,
  barsAgo: number,
  endedBarsAgo?: number
): RelativeAoi[] {
  return Object.entries(pivotLevels(period)).map(([level, ratio]) => ({
    key: `${keyPrefix}-${level.toLowerCase()}`,
    source: 'pivot_points',
    kind: `PIVOT_${level}`,
    side: PIVOT_SIDES[level] ?? 'neutral',
    state: endedBarsAgo === undefined ? 'active' : 'expired',
    barsAgo,
    confirmBars: 0,
    lowerRatio: ratio,
    upperRatio: ratio,
    score: null,
    ...(endedBarsAgo === undefined ? {} : {endedBarsAgo}),
  }));
}

/**
 * DEMO AOIs: the live pivot levels of the current period (ids
 * `demo-pivot-<level>-<timeframe>`) and the expired levels of the period
 * before (`demo-pivot-prev-<level>-<timeframe>`).
 */
export const DEMO_AOIS: readonly RelativeAoi[] = [
  ...pivotPeriod(
    'pivot',
    {high: 1.012, low: 0.986, close: 0.998},
    CURRENT_PERIOD_BARS
  ),
  ...pivotPeriod(
    'pivot-prev',
    {high: 1.006, low: 0.978, close: 0.991},
    2 * CURRENT_PERIOD_BARS,
    CURRENT_PERIOD_BARS
  ),
];
