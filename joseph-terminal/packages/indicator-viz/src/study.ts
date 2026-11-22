/**
 * @fileoverview Maps backend per-bar indicator values to study panes.
 *
 * Render-only: the order-flow Delta % comes from the engine's
 * `/v1/indicators/series`. Thresholds follow the plugin's
 * `min_delta_share` input, exactly as the Pine pane draws them.
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

/** Options of the Delta % pane. */
export interface DeltaStudyOptions {
  /** Plugin inputs (threshold). */
  settings?: Readonly<Record<string, unknown>> | null;
  /** Replay cursor close (UTC ms); later points are dropped. */
  atMs?: number | null;
}

/**
 * Builds the Delta % pane: columns colored by sign, a zero line and the
 * positive/negative thresholds.
 * @param series Engine series for `order_flow_pressure`.
 * @param options Threshold and replay cursor.
 * @return Study pane spec.
 */
export function deltaStudyFromSeries(
  series: IndicatorSeries,
  options: DeltaStudyOptions = {}
): StudyPaneSpec {
  const threshold = Math.round(minDeltaShare(options.settings) * 10_000) / 100;
  const points: StudyPoint[] = [];
  for (const point of series.points) {
    if (options.atMs != null && point.bar_start_ms >= options.atMs) continue;
    const value = numericValue(point.values.delta_pct);
    if (value === null) continue;
    points.push({
      timeMs: point.bar_start_ms,
      value,
      tone: value >= 0 ? 'bid' : 'ask',
    });
  }
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
    title: 'Delta % (estimate)',
    style: 'histogram',
    points,
    levels,
    precision: 2,
    caption: `as of ${time} UTC (${provenance})`,
  };
}
