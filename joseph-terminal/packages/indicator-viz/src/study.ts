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
 * `min_delta_share` input, exactly as the Pine pane draws them. Each bar
 * says how its delta was obtained (`values.method`): `taker_volume` is the
 * true delta from the venue's taker buy/sell volume, `trade_classification`
 * is buy/sell volume classified from trades against the prevailing quote
 * (Lee–Ready, on stock exchanges that publish no aggressor side), anything
 * else is the candle-direction estimate. A bar is only called "true" or
 * "classified" when the engine says so.
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

/** Pine default of `min_delta_share`. */
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
  /** Delta % of the bar. */
  value: number;
  /** True (taker volume), classified (Lee–Ready) or estimate. */
  method: DeltaMethod;
  /** Taker-buy base volume (decimal string), when published. */
  buyVolume: string | null;
  /** Taker-sell base volume (decimal string), when published. */
  sellVolume: string | null;
}

/** Legend/tooltip text explaining true versus estimated delta. */
export const DELTA_LEGEND =
  'Delta % per bar from the engine. True: taker buy minus sell volume published by the venue. Classified (Lee–Ready): trades classified as buys or sells against the prevailing quote, where the exchange publishes no aggressor side. Estimate: bar volume signed by candle direction (drawn faint).';

/**
 * Reads a numeric indicator value.
 * @param value Published value (decimal string, number or null).
 * @return Finite number or null.
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
 * Column opacity of a delta method.
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
 * Reads a published volume (decimal string) without converting it.
 * @param value Published value.
 * @return Decimal string or null.
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
 * @return Share in 0..1.
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
 * @param atMs Replay cursor close (UTC ms); later bars are dropped.
 * @return Bars with a numeric Delta %, oldest first.
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
 * Pane title naming the delta methods in view.
 * @param bars Delta bars.
 * @return `Delta % (true)`, `Delta % (classified)`, `Delta % (estimate)`
 *     or a mixed legend.
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
  /** Replay cursor close (UTC ms); later points are dropped. */
  atMs?: number | null;
}

/**
 * Builds the Delta % pane: columns colored by sign (estimated bars faint),
 * a zero line and the positive/negative thresholds.
 * @param series Engine series for `order_flow_pressure`.
 * @param options Threshold and replay cursor.
 * @return Study pane spec.
 */
export function deltaStudyFromSeries(
  series: IndicatorSeries,
  options: DeltaStudyOptions = {}
): StudyPaneSpec {
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
