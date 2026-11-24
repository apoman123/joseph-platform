/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Maps backend per-bar indicator values to study panes.
 *
 * Render-only: values come from the engine's `/v1/indicators/series` and
 * panes are built from the plugin's display metadata (`display.panes` and
 * the `display.plots` drawn in them). The volume delta pane adds method
 * shading: each bar says how its delta was obtained (`values.method`):
 * `taker_volume` is the true delta from the venue's taker buy/sell volume,
 * `trade_classification` is buy/sell volume classified from trades against
 * the prevailing quote (Lee–Ready, on stock exchanges that publish no
 * aggressor side), anything else is the candle-direction estimate. A bar is
 * only called "true" or "classified" when the engine says so. Column
 * opacity shows the method (true darkest, estimate faintest), and buy/sell
 * volumes are kept only for true and classified bars.
 */

import type {StudyLevel, StudyPaneSpec, StudyPoint} from '@joseph/chart-core';
import type {
  IndicatorPluginInfo,
  IndicatorSeries,
  PaneStyle,
  PlotStyle,
} from '@joseph/contracts';
import {feedStateLabel, toneOf} from './mapping';

/** Plugin id of the built-in volume delta study. */
export const DELTA_PLUGIN_ID = 'volume_delta';

/** Pane id the volume delta plugin declares in `display.panes`. */
export const DELTA_PANE_ID = 'delta';

/** Study id of the volume delta pane. */
export const DELTA_STUDY_ID = 'volume_delta:delta_pct';

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

/** One bar of the engine's volume delta series (display values only). */
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
 * Reads the per-bar delta values of a volume delta series.
 * @param series Engine series for `volume_delta`.
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

/**
 * Returns the provenance caption of a series.
 * @param series Engine series.
 * @param atMs Replay cursor (UTC ms), or null when live.
 * @return Caption such as `as of 12:05 UTC (live)`.
 */
export function seriesCaption(
  series: IndicatorSeries,
  atMs: number | null = null
): string {
  const time = new Date(atMs ?? series.as_of_ms).toISOString().slice(11, 16);
  const provenance =
    atMs != null ? 'replay' : feedStateLabel(series.feed_state);
  return `as of ${time} UTC (${provenance})`;
}

/** Options of the Delta % pane. */
export interface DeltaStudyOptions {
  /** Replay cursor (UTC ms); bars starting at or after it are dropped. */
  atMs?: number | null;
}

/**
 * Builds the Delta % pane: columns colored by sign with opacity by method
 * and a zero line.
 * @param series Engine series for `volume_delta`.
 * @param options Replay cursor.
 * @return Study pane spec.
 */
export function deltaStudyFromSeries(
  series: IndicatorSeries,
  options: DeltaStudyOptions = {}
): StudyPaneSpec {
  const atMs = options.atMs ?? null;
  const bars = deltaBarsFromSeries(series, atMs);
  const points: StudyPoint[] = bars.map(bar => ({
    timeMs: bar.timeMs,
    value: bar.value,
    tone: bar.value >= 0 ? 'bid' : 'ask',
    opacity: deltaOpacity(bar.method),
  }));
  return {
    id: DELTA_STUDY_ID,
    title: deltaStudyTitle(bars),
    style: 'histogram',
    points,
    levels: [{value: 0, label: '', tone: 'muted', lineStyle: 'dotted'}],
    precision: 2,
    caption: seriesCaption(series, atMs),
  };
}

/** One plotted value of a series (display number, null for a gap). */
export interface PlotPoint {
  /** Bar start (UTC ms). */
  timeMs: number;
  /** Value, or null where the plugin published none. */
  value: number | null;
}

/**
 * Reads one published value per bar of a series.
 * @param series Engine series.
 * @param key Value key, as declared in `output_values`.
 * @param atMs Replay cursor (UTC ms); bars starting at or after it are
 *     dropped. Null keeps every bar.
 * @return Points in series order (oldest first); non-numeric values are
 *     gaps.
 */
export function plotPoints(
  series: IndicatorSeries,
  key: string,
  atMs: number | null = null
): PlotPoint[] {
  return series.points
    .filter(point => atMs == null || point.bar_start_ms < atMs)
    .map(point => ({
      timeMs: point.bar_start_ms,
      value: numericValue(point.values[key]),
    }));
}

/**
 * Returns the study id of one of a plugin's panes.
 * @param pluginId Plugin id.
 * @param paneId Pane id from `display.panes`.
 * @return `DELTA_STUDY_ID` for the volume delta plugin, otherwise
 *     `<plugin_id>:<pane id>`.
 */
export function studyPaneId(pluginId: string, paneId: string): string {
  return pluginId === DELTA_PLUGIN_ID
    ? DELTA_STUDY_ID
    : `${pluginId}:${paneId}`;
}

/**
 * Returns the plots a plugin draws in a pane.
 * @param info Plugin descriptor.
 * @param paneId `price` or a pane id from `display.panes`.
 * @return Plots in declaration order.
 */
export function panePlots(
  info: IndicatorPluginInfo,
  paneId: string
): PlotStyle[] {
  return info.display.plots.filter(plot => plot.pane === paneId);
}

/**
 * Builds a study pane from a plugin's pane metadata. A pane holds one
 * series: the first plot declared for it, drawn as a line or histogram (a
 * `sign` tone colors columns by sign), plus the pane's reference levels.
 * @param info Plugin descriptor.
 * @param pane Pane metadata from `display.panes`.
 * @param series Engine series of the plugin.
 * @param atMs Replay cursor (UTC ms); bars starting at or after it are
 *     dropped. Null keeps every bar.
 * @return Study pane spec; it has no points when no plot targets the pane.
 */
export function studyPaneFromSeries(
  info: IndicatorPluginInfo,
  pane: PaneStyle,
  series: IndicatorSeries,
  atMs: number | null = null
): StudyPaneSpec {
  const plot = panePlots(info, pane.id)[0];
  const histogram = plot?.render === 'histogram';
  const points: StudyPoint[] = [];
  for (const point of plot ? plotPoints(series, plot.value, atMs) : []) {
    if (point.value === null) continue;
    points.push({
      timeMs: point.timeMs,
      value: point.value,
      ...(histogram ? {tone: toneOf(plot.tone ?? 'accent', point.value)} : {}),
    });
  }
  const levels: StudyLevel[] = (pane.levels ?? []).map(level => ({
    value: level.value,
    label: level.label,
    tone: toneOf(level.tone ?? 'muted', level.value),
    lineStyle: level.line_style ?? 'dotted',
  }));
  return {
    id: studyPaneId(info.plugin_id, pane.id),
    title: pane.title,
    style: histogram ? 'histogram' : 'line',
    points,
    levels,
    precision: pane.precision ?? 2,
    caption: seriesCaption(series, atMs),
  };
}
