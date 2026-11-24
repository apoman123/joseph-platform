/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Maps backend per-bar indicator values to study panes.
 *
 * Render-only: the order-flow Delta % comes from the engine's
 * `/v1/indicators/series`. Thresholds follow the plugin's
 * `min_delta_share` input, exactly as the reference Pine Script pane draws
 * them. Each bar says how its delta was obtained (`values.method`):
 * `taker_volume` is the true delta from the venue's taker buy/sell volume,
 * `trade_classification` is buy/sell volume classified from trades against
 * the prevailing quote (Lee–Ready, on stock exchanges that publish no
 * aggressor side), anything else is the candle-direction estimate. A bar is
 * only called "true" or "classified" when the engine says so. Column
 * opacity shows the method (true darkest, estimate faintest), and buy/sell
 * volumes are kept only for true and classified bars.
 */

import type {StudyLevel, StudyPaneSpec, StudyPoint} from '@joseph/chart-core';
import type {IndicatorSeries} from '@joseph/contracts';
import {feedStateLabel} from './mapping';

/** Study id of the order-flow Delta % pane. */
export const DELTA_STUDY_ID = 'order_flow_pressure:delta_pct';

/** Plugins that publish a sub-pane, with the value they plot. */
export const STUDY_PLUGINS: Readonly<Record<string, string>> = {
  order_flow_pressure: 'delta_pct',
};

/** Default of the plugin's `min_delta_share` input (as in Pine Script). */
export const DEFAULT_MIN_DELTA_SHARE = 0.2;

/** Column opacity of true (measured) delta bars. */
export const TRUE_DELTA_OPACITY = 0.7;

/** Column opacity of estimated delta bars (drawn faint). */
export const ESTIMATE_DELTA_OPACITY = 0.32;

/** Column opacity of classified (Lee–Ready) delta bars. */
export const CLASSIFIED_DELTA_OPACITY = 0.55;

/** How one bar's delta was obtained. */
export type DeltaMethod = 'true' | 'classified' | 'estimate';

/** Engine `values.method` of the true delta. */
export const TRUE_DELTA_METHOD = 'taker_volume';

/** Engine `values.method` of trades classified against quotes. */
export const CLASSIFIED_DELTA_METHOD = 'trade_classification';

/** Data-window wording of each delta method. */
export const DELTA_METHOD_LABELS: Readonly<Record<DeltaMethod, string>> = {
  true: 'True (taker volume)',
  classified: 'Classified (Lee–Ready)',
  estimate: 'Estimate',
};

/** One bar of the engine's order-flow series (display values only). */
export interface DeltaBar {
  /** Bar start (UTC ms). */
  timeMs: number;
  /** Delta % of the bar (display number). */
  value: number;
  /** True (taker volume), classified (Lee–Ready) or estimate. */
  method: DeltaMethod;
  /**
   * Buy base volume (decimal string) of a true or classified bar, when
   * published; always null for estimates.
   */
  buyVolume: string | null;
  /**
   * Sell base volume (decimal string) of a true or classified bar, when
   * published; always null for estimates.
   */
  sellVolume: string | null;
}

/** Legend/tooltip text explaining true versus estimated delta. */
export const DELTA_LEGEND =
  'Delta % per bar from the engine. True: taker buy minus sell volume published by the venue. Classified (Lee–Ready): trades classified as buys or sells against the prevailing quote, where the exchange publishes no aggressor side. Estimate: bar volume signed by candle direction (drawn faint).';

/**
 * Reads a numeric indicator value for display.
 * @param value Published value (decimal string, number or null).
 * @return Finite number, or null for anything else (blank strings, NaN,
 *     other types).
 */
export function numericValue(value: unknown): number | null {
  if (typeof value === 'number') return Number.isFinite(value) ? value : null;
  if (typeof value === 'string' && value.trim() !== '') {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

/**
 * Classifies the engine's `values.method`. Unknown or missing methods
 * count as an estimate, never as true or classified delta.
 * @param value Published method.
 * @return Delta method.
 */
export function deltaMethodOf(value: unknown): DeltaMethod {
  if (value === TRUE_DELTA_METHOD) return 'true';
  if (value === CLASSIFIED_DELTA_METHOD) return 'classified';
  return 'estimate';
}

/**
 * Returns the column opacity of a delta method.
 * @param method Delta method.
 * @return Opacity 0..1.
 */
export function deltaOpacity(method: DeltaMethod): number {
  if (method === 'true') return TRUE_DELTA_OPACITY;
  return method === 'classified'
    ? CLASSIFIED_DELTA_OPACITY
    : ESTIMATE_DELTA_OPACITY;
}

/**
 * Reads a published volume as text, keeping a decimal string exactly as
 * published.
 * @param value Published value.
 * @return The string when it parses as a finite number, `String(value)`
 *     for a finite number, otherwise null.
 */
function volumeText(value: unknown): string | null {
  if (typeof value === 'number' && Number.isFinite(value)) return String(value);
  return typeof value === 'string' && numericValue(value) !== null
    ? value
    : null;
}

/**
 * Resolves the Delta threshold share from user inputs.
 * @param settings Plugin inputs (may be null).
 * @return `min_delta_share` when it is a number in 0..1, otherwise
 *     DEFAULT_MIN_DELTA_SHARE.
 */
export function minDeltaShare(
  settings: Readonly<Record<string, unknown>> | null | undefined
): number {
  const share = numericValue(settings?.min_delta_share);
  return share !== null && share >= 0 && share <= 1
    ? share
    : DEFAULT_MIN_DELTA_SHARE;
}

/**
 * Reads the per-bar delta values of an order-flow series.
 * @param series Engine series for `order_flow_pressure`.
 * @param atMs Replay cursor (UTC ms); bars starting at or after it are
 *     dropped. Null keeps every bar.
 * @return Bars with a numeric Delta %, in series order (oldest first).
 */
export function deltaBarsFromSeries(
  series: IndicatorSeries,
  atMs: number | null = null
): DeltaBar[] {
  const bars: DeltaBar[] = [];
  for (const point of series.points) {
    if (atMs != null && point.bar_start_ms >= atMs) continue;
    const value = numericValue(point.values.delta_pct);
    if (value === null) continue;
    const method = deltaMethodOf(point.values.method);
    const measured = method !== 'estimate';
    bars.push({
      timeMs: point.bar_start_ms,
      value,
      method,
      buyVolume: measured ? volumeText(point.values.buy_volume) : null,
      sellVolume: measured ? volumeText(point.values.sell_volume) : null,
    });
  }
  return bars;
}

/**
 * Returns the pane title naming the delta methods in view.
 * @param bars Delta bars.
 * @return `Delta %` without bars; `Delta % (true)`, `Delta % (classified)`
 *     or `Delta % (estimate)` for one method; `Delta % (true; classified)`
 *     for both measured methods; or `Delta % (<measured>; faint =
 *     estimate)` when estimates mix in (`true` named over `classified`).
 */
export function deltaStudyTitle(
  bars: ReadonlyArray<Pick<DeltaBar, 'method'>>
): string {
  const methods = new Set(bars.map(bar => bar.method));
  if (methods.size === 0) return 'Delta %';
  if (methods.size === 1) {
    if (methods.has('true')) return 'Delta % (true)';
    return methods.has('classified')
      ? 'Delta % (classified)'
      : 'Delta % (estimate)';
  }
  const measured = methods.has('true') ? 'true' : 'classified';
  if (!methods.has('estimate')) return 'Delta % (true; classified)';
  return `Delta % (${measured}; faint = estimate)`;
}

/** Options of the Delta % pane. */
export interface DeltaStudyOptions {
  /** Plugin inputs (threshold). */
  settings?: Readonly<Record<string, unknown>> | null;
  /** Replay cursor (UTC ms); bars starting at or after it are dropped. */
  atMs?: number | null;
}

/**
 * Builds the Delta % pane: columns colored by sign with opacity by method,
 * a zero line and the positive/negative thresholds.
 * @param series Engine series for `order_flow_pressure`.
 * @param options Threshold and replay cursor.
 * @return Study pane spec.
 */
export function deltaStudyFromSeries(
  series: IndicatorSeries,
  options: DeltaStudyOptions = {}
): StudyPaneSpec {
  // The share (0..1) becomes a percentage with at most two decimals, the
  // unit of the plotted Delta %.
  const threshold = Math.round(minDeltaShare(options.settings) * 10_000) / 100;
  const bars = deltaBarsFromSeries(series, options.atMs ?? null);
  const points: StudyPoint[] = bars.map(bar => ({
    timeMs: bar.timeMs,
    value: bar.value,
    tone: bar.value >= 0 ? 'bid' : 'ask',
    opacity: deltaOpacity(bar.method),
  }));
  const levels: StudyLevel[] = [
    {value: 0, label: '', tone: 'muted', lineStyle: 'dotted'},
    {
      value: threshold,
      label: `+${threshold}%`,
      tone: 'bid',
      lineStyle: 'dashed',
    },
    {
      value: -threshold,
      label: `−${threshold}%`,
      tone: 'ask',
      lineStyle: 'dashed',
    },
  ];
  const asOf = options.atMs ?? series.as_of_ms;
  const time = new Date(asOf).toISOString().slice(11, 16);
  const provenance =
    options.atMs != null ? 'replay' : feedStateLabel(series.feed_state);
  return {
    id: DELTA_STUDY_ID,
    title: deltaStudyTitle(bars),
    style: 'histogram',
    points,
    levels,
    precision: 2,
    caption: `as of ${time} UTC (${provenance})`,
  };
}
