/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview DEMO engine series for the engine-backed drawing tools:
 * `anchored_vwap` and `regression_channel`.
 *
 * The mock provider stands in for the market engine, so it computes these
 * values (the browser terminal never does). Definitions:
 *
 * - Anchored VWAP: cumulative `Σ(src·v) / Σv` from the anchor bar;
 *   `stdev = sqrt(Σ(src²·v) / Σv − vwap²)`; band k = `vwap ± m_k·stdev`
 *   when the multiplier `m_k` is above zero, null otherwise.
 * - Regression channel: least squares of `src` against the bar index over
 *   the bars in `[anchor_ms, point bar]`; σ is the sample standard
 *   deviation of the residuals (n − 1); channel = line ± deviations·σ;
 *   Pearson's R of price against bar index; `max_up`/`max_down` are the
 *   largest residuals above/below the line.
 *
 * Like the engine, anchors may lie up to 50 000 closed bars back; an older
 * anchor is refused with a validation error (the engine answers 422) whose
 * message the terminal shows as is. Bar boundaries follow the instrument's
 * session calendar.
 */

import {
  ApiError,
  CONTINUOUS_CALENDAR,
  type BarCalendar,
  type Candle,
  Decimal,
  type IndicatorSeries,
  type IndicatorSeriesPoint,
  type IndicatorSeriesQuery,
  type IndicatorValue,
} from '@joseph/contracts';
import {demoCandle, type DemoInstrumentSpec} from './prices';

/** Bars back the engine computes anchored values from at most. */
export const DEMO_SERIES_BAR_CAP = 50_000;

/** Regression points computed in full after the end bar (DEMO cost cap). */
const REGRESSION_POINT_CAP = 500;

/**
 * Refusal message for an anchor beyond the cap, worded like the engine's
 * 422 (`indicators.service`).
 * @param closed Closed bars between the anchor bar and now.
 * @param timeframe Chart timeframe.
 * @return Message.
 */
export function demoAnchorRefusal(closed: number, timeframe: string): string {
  return `the anchor is ${closed} closed ${timeframe} bars back; anchored studies are computed for at most ${DEMO_SERIES_BAR_CAP} bars. Move the anchor closer or use a higher timeframe.`;
}

/** Plugins served by this module. */
export const DEMO_DRAWING_PLUGINS: readonly string[] = [
  'anchored_vwap',
  'regression_channel',
];

/** Values of a bar outside the anchored window (anchor too old). */
const NULL_VWAP_VALUES: Readonly<Record<string, IndicatorValue>> = {
  vwap: null,
  stdev: null,
  upper_1: null,
  lower_1: null,
  upper_2: null,
  lower_2: null,
  upper_3: null,
  lower_3: null,
  anchor_ms: null,
  bars: 0,
};

/** Price sources understood by the DEMO series. */
const SOURCES: Readonly<Record<string, (c: Candle) => number>> = {
  open: c => Number(c.open),
  high: c => Number(c.high),
  low: c => Number(c.low),
  close: c => Number(c.close),
  hl2: c => (Number(c.high) + Number(c.low)) / 2,
  hlc3: c => (Number(c.high) + Number(c.low) + Number(c.close)) / 3,
  ohlc4: c =>
    (Number(c.open) + Number(c.high) + Number(c.low) + Number(c.close)) / 4,
};

/**
 * Formats a computed number as a decimal string.
 * @param value Number.
 * @return Decimal string, or null when not finite.
 */
function dec(value: number): string | null {
  return Number.isFinite(value)
    ? Decimal.fromNumber(value, 8).toString()
    : null;
}

/**
 * Reads a price source.
 * @param settings Plugin settings.
 * @param fallback Default source.
 * @return Source accessor.
 * @throws {Error} For unknown sources (the engine answers 422).
 */
function sourceOf(
  settings: Readonly<Record<string, unknown>>,
  fallback: string
): (c: Candle) => number {
  const name = settings.source ?? fallback;
  const source = typeof name === 'string' ? SOURCES[name] : undefined;
  if (!source) throw new Error(`Invalid setting source: ${String(name)}`);
  return source;
}

/**
 * Reads a non-negative decimal-string setting.
 * @param settings Plugin settings.
 * @param key Key.
 * @param fallback Default.
 * @return Value.
 * @throws {Error} When the value is not a decimal string.
 */
function decimalSetting(
  settings: Readonly<Record<string, unknown>>,
  key: string,
  fallback: string
): number {
  const raw = settings[key] ?? fallback;
  if (typeof raw !== 'string' || !Decimal.isValid(raw) || Number(raw) < 0) {
    throw new Error(`Invalid setting ${key}: expected a decimal string`);
  }
  return Number(raw);
}

/**
 * Anchored VWAP values for candles that start at the anchor bar.
 * @param candles Candles from the anchor bar on (oldest first).
 * @param settings Plugin settings (`source`, `band_1..3`).
 * @return One point per candle.
 */
export function anchoredVwapPoints(
  candles: readonly Candle[],
  settings: Readonly<Record<string, unknown>>
): IndicatorSeriesPoint[] {
  const source = sourceOf(settings, 'hlc3');
  const bands = [1, 2, 3].map(k =>
    decimalSetting(settings, `band_${k}`, k === 1 ? '1' : '0')
  );
  const anchorMs = candles[0]?.start_ms ?? null;
  let volume = 0;
  let weighted = 0;
  let weightedSquares = 0;
  return candles.map((candle, i) => {
    const src = source(candle);
    const v = Number(candle.base_volume);
    volume += v;
    weighted += src * v;
    weightedSquares += src * src * v;
    const values: Record<string, IndicatorValue> = {
      anchor_ms: anchorMs,
      bars: i + 1,
    };
    const vwap = volume > 0 ? weighted / volume : NaN;
    const stdev =
      volume > 0
        ? Math.sqrt(Math.max(0, weightedSquares / volume - vwap * vwap))
        : NaN;
    values.vwap = dec(vwap);
    values.stdev = dec(stdev);
    bands.forEach((multiplier, index) => {
      const k = index + 1;
      const on = multiplier > 0 && Number.isFinite(vwap);
      values[`upper_${k}`] = on ? dec(vwap + multiplier * stdev) : null;
      values[`lower_${k}`] = on ? dec(vwap - multiplier * stdev) : null;
    });
    return {bar_start_ms: candle.start_ms, values};
  });
}

/**
 * Regression channel over candles (the first one is the anchor bar).
 * @param candles Candles from `anchor_ms` through the point bar.
 * @param settings Plugin settings (`source`, `deviations`).
 * @return Point values, all null when fewer than two bars.
 */
export function regressionValues(
  candles: readonly Candle[],
  settings: Readonly<Record<string, unknown>>
): Record<string, IndicatorValue> {
  const source = sourceOf(settings, 'close');
  const deviations = decimalSetting(settings, 'deviations', '2');
  const n = candles.length;
  const empty: Record<string, IndicatorValue> = {
    start_ms: null,
    start: null,
    end: null,
    upper_start: null,
    upper_end: null,
    lower_start: null,
    lower_end: null,
    slope: null,
    stdev: null,
    pearson_r: null,
    max_up: null,
    max_down: null,
    bars: n,
  };
  if (n < 2) return empty;
  const ys = candles.map(source);
  const meanX = (n - 1) / 2;
  const meanY = ys.reduce((sum, y) => sum + y, 0) / n;
  let sxx = 0;
  let sxy = 0;
  let syy = 0;
  ys.forEach((y, x) => {
    sxx += (x - meanX) ** 2;
    sxy += (x - meanX) * (y - meanY);
    syy += (y - meanY) ** 2;
  });
  const slope = sxy / sxx;
  const intercept = meanY - slope * meanX;
  let squared = 0;
  let maxUp = 0;
  let maxDown = 0;
  ys.forEach((y, x) => {
    const residual = y - (intercept + slope * x);
    squared += residual * residual;
    maxUp = Math.max(maxUp, residual);
    maxDown = Math.max(maxDown, -residual);
  });
  const sigma = Math.sqrt(squared / (n - 1));
  const start = intercept;
  const end = intercept + slope * (n - 1);
  const offset = deviations * sigma;
  return {
    ...empty,
    start_ms: candles[0]?.start_ms ?? null,
    start: dec(start),
    end: dec(end),
    upper_start: dec(start + offset),
    upper_end: dec(end + offset),
    lower_start: dec(start - offset),
    lower_end: dec(end - offset),
    slope: dec(slope),
    stdev: dec(sigma),
    pearson_r: syy > 0 ? dec(sxy / Math.sqrt(sxx * syy)) : null,
    max_up: dec(maxUp),
    max_down: dec(maxDown),
  };
}

/**
 * Serves a DEMO series for the drawing plugins.
 * @param spec DEMO instrument.
 * @param query Series query.
 * @param nowMs Clock.
 * @param calendar Session calendar of the instrument (default `24x7`).
 * @return Series, or null when the plugin is not a drawing plugin.
 * @throws {Error} For invalid settings (the engine answers 422).
 * @throws {ApiError} When the anchor is more than 50 000 bars back.
 */
export function demoDrawingSeries(
  spec: DemoInstrumentSpec,
  query: IndicatorSeriesQuery,
  nowMs: number,
  calendar: BarCalendar = CONTINUOUS_CALENDAR
): IndicatorSeries | null {
  if (!DEMO_DRAWING_PLUGINS.includes(query.pluginId)) return null;
  const settings = query.settings ?? {};
  const tf = query.timeframe;
  const lastStart = calendar.lastClosedStart(tf, nowMs) ?? 0;
  const anchorRaw = settings.anchor_ms;
  if (typeof anchorRaw !== 'number' || !Number.isInteger(anchorRaw)) {
    throw new Error('Invalid setting anchor_ms: expected an integer');
  }
  const anchorMs = calendar.floor(query.timeframe, anchorRaw);
  const barsBack =
    anchorMs <= lastStart ? calendar.barsBetween(tf, anchorMs, lastStart) : 0;
  // Like the engine: refuse when more than the cap of closed bars lie
  // between the anchor bar and now (the anchor bar itself included).
  if (barsBack + 1 > DEMO_SERIES_BAR_CAP) {
    const message = demoAnchorRefusal(barsBack + 1, tf);
    throw new ApiError(422, {
      code: 'VALIDATION_FAILED',
      message,
      trace_id: 'demo',
      retryable: false,
      field_errors: [{field: 'settings.anchor_ms', message}],
    });
  }
  const requested = calendar.floor(
    query.timeframe,
    query.fromMs ?? calendar.shift(tf, lastStart, -199)
  );
  const fromMs =
    calendar.barsBetween(tf, requested, lastStart) >= DEMO_SERIES_BAR_CAP
      ? calendar.shift(tf, lastStart, -(DEMO_SERIES_BAR_CAP - 1))
      : requested;
  const inWindow = anchorMs <= lastStart;
  const candles: Candle[] = [];
  if (inWindow) {
    for (let t = anchorMs; t <= lastStart; t = calendar.shift(tf, t, 1)) {
      candles.push(demoCandle(spec, query.timeframe, t, nowMs, calendar));
    }
  }
  const points: IndicatorSeriesPoint[] = [];
  if (query.pluginId === 'anchored_vwap') {
    if ((settings.anchor ?? 'time') !== 'time') {
      throw new Error("Invalid setting anchor: only 'time' is supported");
    }
    const byStart = new Map(
      anchoredVwapPoints(candles, settings).map(p => [p.bar_start_ms, p])
    );
    for (let t = fromMs; t <= lastStart; t = calendar.shift(tf, t, 1)) {
      points.push(
        byStart.get(t) ?? {bar_start_ms: t, values: NULL_VWAP_VALUES}
      );
    }
  } else {
    // Bars from the anchor are consecutive in `candles`, so the count is the
    // number of candles that start at or before the point's bar.
    let count = 0;
    for (let t = fromMs; t <= lastStart; t = calendar.shift(tf, t, 1)) {
      while (
        inWindow &&
        count < candles.length &&
        (candles[count]?.start_ms ?? Infinity) <= t
      ) {
        count += 1;
      }
      // The channel reads the point of its end bar (the first point); later
      // points are filled for the first few hundred bars only, which keeps
      // anchors far back cheap in the browser.
      points.push({
        bar_start_ms: t,
        values: regressionValues(
          points.length < REGRESSION_POINT_CAP ? candles.slice(0, count) : [],
          settings
        ),
      });
    }
  }
  return {
    instrument_id: spec.instrumentId,
    timeframe: query.timeframe,
    plugin_id: query.pluginId,
    indicator_version: 'demo',
    points,
    as_of_ms: calendar.endOf(tf, lastStart),
    feed_state: 'DEMO',
  };
}
