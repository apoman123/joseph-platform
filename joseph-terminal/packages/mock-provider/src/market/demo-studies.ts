/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview DEMO engine series for the built-in studies: SMA, EMA,
 * Bollinger Bands, RSI, ATR, the period (session, week, month) VWAP and
 * volume delta.
 *
 * The mock provider stands in for the market engine, so it computes these
 * values over DEMO candles (the browser terminal never does). Definitions
 * follow TradingView's built-ins:
 *
 * - SMA: mean of the last `length` sources.
 * - EMA: α = 2 / (length + 1), seeded with the first source value.
 * - RMA (Wilder): α = 1 / length, seeded with the SMA of the first `length`
 *   values; RSI and ATR use it.
 * - RSI: RMA of gains and of losses; 100 without losses, 0 without gains.
 * - ATR: RMA of the true range; the first bar's true range is high − low.
 * - Bollinger Bands: basis = SMA, bands = basis ± mult × the population
 *   standard deviation; bandwidth in percent of the basis; %B null when the
 *   bands meet.
 * - VWAP: the anchored VWAP of `demo-drawing-series`, restarted at each
 *   session (the calendar's `1d` bar), ISO week or calendar month.
 * - Volume delta: taker (or classified) buy volume against the rest, or the
 *   candle-direction estimate.
 *
 * Values are computed in floating point over enough earlier bars that the
 * first served point is warm, and published as decimal strings with at most
 * 8 decimal places. Series carry `indicator_version: 'demo'`.
 */

import {
  CONTINUOUS_CALENDAR,
  Decimal,
  type BarCalendar,
  type Candle,
  type IndicatorSeries,
  type IndicatorSeriesPoint,
  type IndicatorSeriesQuery,
  type IndicatorValue,
  type Timeframe,
} from '@joseph/contracts';
import {anchoredVwapPoints} from './demo-drawing-series';
import {demoCandle, type DemoInstrumentSpec} from './prices';

/** Plugins served by this module. */
export const DEMO_STUDY_PLUGINS: readonly string[] = [
  'sma',
  'ema',
  'anchored_vwap',
  'bollinger_bands',
  'rsi',
  'atr',
  'volume_delta',
];

/** Points served when the query has no `fromMs`. */
const DEFAULT_POINTS = 200;

/** Points served at most (a DEMO cost cap); earlier `fromMs` are moved up. */
export const DEMO_STUDY_POINT_CAP = 5_000;

/** Earlier bars computed at most to warm a study up (a DEMO cost cap). */
const WARMUP_CAP = 10_000;

/** Price sources by Pine source name. */
const SOURCES: Readonly<Record<string, (c: Candle) => number>> = {
  close: c => Number(c.close),
  open: c => Number(c.open),
  high: c => Number(c.high),
  low: c => Number(c.low),
  hl2: c => (Number(c.high) + Number(c.low)) / 2,
  hlc3: c => (Number(c.high) + Number(c.low) + Number(c.close)) / 3,
  ohlc4: c =>
    (Number(c.open) + Number(c.high) + Number(c.low) + Number(c.close)) / 4,
};

/** Price sources the VWAP accepts. */
const VWAP_SOURCES: readonly string[] = ['close', 'hl2', 'hlc3', 'ohlc4'];

/** Calendar bar that bounds each VWAP period, by `anchor` setting. */
const VWAP_PERIODS: Readonly<Record<string, Timeframe>> = {
  session: '1d',
  week: '1w',
  month: '1M',
};

/** Values of one point by key. */
type PointValues = Record<string, IndicatorValue>;

/**
 * Formats a computed number as a decimal string.
 * @param value Computed value, or null.
 * @return Decimal string rounded to 8 decimal places, or null when the
 *     value is null or not finite.
 */
function decimalText(value: number | null): string | null {
  return value !== null && Number.isFinite(value)
    ? Decimal.fromNumber(value, 8).toString()
    : null;
}

/**
 * Reads an integer setting.
 * @param settings Plugin settings.
 * @param key Setting name.
 * @param fallback Value when unset.
 * @param bounds Inclusive minimum and maximum.
 * @return Value.
 * @throws `Error` when the value is not an integer within the bounds (the
 *     engine answers 422).
 */
function intSetting(
  settings: Readonly<Record<string, unknown>>,
  key: string,
  fallback: number,
  bounds: readonly [number, number]
): number {
  const value = settings[key] ?? fallback;
  const [minimum, maximum] = bounds;
  if (
    typeof value !== 'number' ||
    !Number.isInteger(value) ||
    value < minimum ||
    value > maximum
  ) {
    throw new Error(
      `Invalid setting ${key}: expected an integer from ${minimum} to ${maximum}`
    );
  }
  return value;
}

/**
 * Reads a decimal-string setting.
 * @param settings Plugin settings.
 * @param key Setting name.
 * @param fallback Decimal string used when unset.
 * @param bounds Inclusive minimum and maximum.
 * @return Value as a number.
 * @throws `Error` when the value is not a decimal string within the bounds
 *     (the engine answers 422).
 */
function decimalSetting(
  settings: Readonly<Record<string, unknown>>,
  key: string,
  fallback: string,
  bounds: readonly [number, number]
): number {
  const raw = settings[key] ?? fallback;
  const [minimum, maximum] = bounds;
  const value =
    typeof raw === 'string' && Decimal.isValid(raw) ? Number(raw) : NaN;
  if (!(value >= minimum && value <= maximum)) {
    throw new Error(
      `Invalid setting ${key}: expected a decimal string from ${minimum} to ${maximum}`
    );
  }
  return value;
}

/**
 * Reads a choice setting.
 * @param settings Plugin settings.
 * @param key Setting name.
 * @param fallback Value when unset.
 * @param choices Allowed values.
 * @return Value.
 * @throws `Error` for any other value (the engine answers 422).
 */
function choiceSetting(
  settings: Readonly<Record<string, unknown>>,
  key: string,
  fallback: string,
  choices: readonly string[]
): string {
  const value = settings[key] ?? fallback;
  if (typeof value !== 'string' || !choices.includes(value)) {
    throw new Error(
      `Invalid setting ${key}: expected one of ${choices.join(', ')}`
    );
  }
  return value;
}

/**
 * Computes the simple moving average.
 * @param values Sources, oldest first.
 * @param length Bars averaged.
 * @return Average per bar; null before `length` bars.
 */
export function smaValues(
  values: readonly number[],
  length: number
): Array<number | null> {
  let sum = 0;
  return values.map((value, i) => {
    sum += value;
    if (i >= length) sum -= values[i - length] ?? 0;
    return i >= length - 1 ? sum / length : null;
  });
}

/**
 * Computes the exponential moving average, seeded with the first value
 * (Pine `ta.ema`).
 * @param values Sources, oldest first.
 * @param length Bars; α = 2 / (length + 1).
 * @return Average per bar, from the first bar on.
 */
export function emaValues(
  values: readonly number[],
  length: number
): Array<number | null> {
  const alpha = 2 / (length + 1);
  let ema: number | null = null;
  return values.map(value => {
    ema = ema === null ? value : alpha * value + (1 - alpha) * ema;
    return ema;
  });
}

/**
 * Computes Wilder's moving average, seeded with the simple average of the
 * first `length` values (Pine `ta.rma`).
 * @param values Inputs, oldest first.
 * @param length Bars; α = 1 / length.
 * @return Average per input; null before `length` inputs.
 */
export function rmaValues(
  values: readonly number[],
  length: number
): Array<number | null> {
  const seeds = smaValues(values, length);
  let rma: number | null = null;
  return values.map((value, i) => {
    if (rma === null) {
      rma = seeds[i] ?? null;
    } else {
      rma = (rma * (length - 1) + value) / length;
    }
    return rma;
  });
}

/**
 * Computes the relative strength index.
 * @param values Sources, oldest first.
 * @param length Bars of the Wilder averages.
 * @return RSI 0..100 per bar; null for the first `length` bars.
 */
export function rsiValues(
  values: readonly number[],
  length: number
): Array<number | null> {
  const changes = values.slice(1).map((value, i) => value - (values[i] ?? 0));
  const gains = rmaValues(
    changes.map(change => Math.max(change, 0)),
    length
  );
  const losses = rmaValues(
    changes.map(change => Math.max(-change, 0)),
    length
  );
  const rsi = changes.map((_, i) => {
    const up = gains[i] ?? null;
    const down = losses[i] ?? null;
    if (up === null || down === null) return null;
    if (down === 0) return 100;
    if (up === 0) return 0;
    return 100 - 100 / (1 + up / down);
  });
  return [null, ...rsi];
}

/**
 * Computes the average true range.
 * @param candles Candles, oldest first.
 * @param length Bars of the Wilder average.
 * @return ATR per bar; null before `length` bars.
 */
export function atrValues(
  candles: readonly Candle[],
  length: number
): Array<number | null> {
  const ranges = candles.map((candle, i) => {
    const high = Number(candle.high);
    const low = Number(candle.low);
    const previous = candles[i - 1];
    if (!previous) return high - low;
    const close = Number(previous.close);
    return Math.max(high - low, Math.abs(high - close), Math.abs(low - close));
  });
  return rmaValues(ranges, length);
}

/** Bollinger values of one bar. */
export interface BollingerPoint {
  /** Simple moving average. */
  basis: number;
  /** Basis plus mult standard deviations. */
  upper: number;
  /** Basis minus mult standard deviations. */
  lower: number;
  /** Band width in percent of the basis; null for a zero basis. */
  bandwidth: number | null;
  /** Source position between the bands; null when they meet. */
  percentB: number | null;
}

/**
 * Computes Bollinger Bands with the population standard deviation.
 * @param values Sources, oldest first.
 * @param length Bars.
 * @param mult Band width in standard deviations.
 * @return Bands per bar; null before `length` bars.
 */
export function bollingerValues(
  values: readonly number[],
  length: number,
  mult: number
): Array<BollingerPoint | null> {
  const basis = smaValues(values, length);
  return values.map((value, i) => {
    const mean = basis[i] ?? null;
    if (mean === null) return null;
    let squares = 0;
    for (let j = i - length + 1; j <= i; j += 1) {
      squares += ((values[j] ?? mean) - mean) ** 2;
    }
    const offset = mult * Math.sqrt(squares / length);
    const upper = mean + offset;
    const lower = mean - offset;
    return {
      basis: mean,
      upper,
      lower,
      bandwidth: mean !== 0 ? ((upper - lower) / mean) * 100 : null,
      percentB: upper !== lower ? (value - lower) / (upper - lower) : null,
    };
  });
}

/**
 * Builds one DEMO volume-delta point for a candle. True delta uses the
 * candle's taker-buy volume (buy − sell over the bar volume); on stock and
 * ETF bars that volume is classified against quotes, so the method is
 * `trade_classification`; the estimate signs the bar volume by candle
 * direction, as the engine's fallback does.
 * @param candle DEMO candle; true delta needs `taker_buy_base_volume` and a
 *     non-zero volume, otherwise the estimate is used.
 * @param estimate Whether to use the candle-direction estimate even when
 *     taker volume is present.
 * @return Series point; `delta_pct` is the delta share of volume in percent
 *     (the estimate's body-over-range share is scaled to at most ±60%).
 */
export function demoDeltaPoint(
  candle: Candle,
  estimate: boolean
): IndicatorSeriesPoint {
  const base = Decimal.parse(candle.base_volume);
  const taker = candle.taker_buy_base_volume;
  if (!estimate && taker != null && base.toNumber() > 0) {
    const buy = Decimal.parse(taker);
    const sell = base.sub(buy);
    const share = buy.sub(sell).toNumber() / base.toNumber();
    return {
      bar_start_ms: candle.start_ms,
      values: {
        delta_pct: (share * 100).toFixed(2),
        delta: buy.sub(sell).toString(),
        buy_volume: buy.toString(),
        sell_volume: sell.toString(),
        method:
          candle.taker_buy_method === 'classified'
            ? 'trade_classification'
            : 'taker_volume',
        coverage_pct: '100',
        intrabar_count: 0,
      },
    };
  }
  const open = Number(candle.open);
  const close = Number(candle.close);
  const range = Math.max(Number(candle.high) - Number(candle.low), 1e-9);
  const share = Math.max(-1, Math.min(1, (close - open) / range));
  return {
    bar_start_ms: candle.start_ms,
    values: {
      delta_pct: (share * 60).toFixed(2),
      delta: null,
      buy_volume: null,
      sell_volume: null,
      method: 'candle_direction',
      coverage_pct: '100',
      intrabar_count: 5,
    },
  };
}

/**
 * Computes the period VWAP: an anchored VWAP restarted at the first bar of
 * each period.
 * @param candles Candles, oldest first.
 * @param periodOf Returns the period (its start, UTC ms) of a bar start.
 * @param settings Plugin settings (`source`, `band_1` … `band_3`).
 * @return One point per candle; `anchor_ms` is the period's first bar.
 * @throws `Error` for an invalid source or band multiplier.
 */
export function periodVwapPoints(
  candles: readonly Candle[],
  periodOf: (startMs: number) => number,
  settings: Readonly<Record<string, unknown>>
): IndicatorSeriesPoint[] {
  choiceSetting(settings, 'source', 'hlc3', VWAP_SOURCES);
  const groups: Candle[][] = [];
  let currentPeriod: number | null = null;
  for (const candle of candles) {
    const period = periodOf(candle.start_ms);
    if (period !== currentPeriod || groups.length === 0) {
      groups.push([]);
      currentPeriod = period;
    }
    groups[groups.length - 1]?.push(candle);
  }
  return groups.flatMap(group =>
    anchoredVwapPoints(group, settings).map(point => ({
      bar_start_ms: point.bar_start_ms,
      values: {...point.values, anchor_in_window: true},
    }))
  );
}

/** How one study is computed. */
interface StudyPlan {
  /**
   * Returns the first bar to compute so the first served point is warm.
   * @param fromMs Start of the first served bar.
   * @return Start of the first computed bar (at or before `fromMs`).
   */
  firstBar(fromMs: number): number;
  /**
   * Computes the values of every candle.
   * @param candles Candles from the warm-up start through the last closed
   *     bar, oldest first.
   * @return Values per candle, in the same order.
   */
  compute(candles: readonly Candle[]): PointValues[];
}

/**
 * Reads the price source setting.
 * @param settings Plugin settings.
 * @return Accessor reading that price from a candle.
 * @throws `Error` for an unknown source.
 */
function sourceOf(
  settings: Readonly<Record<string, unknown>>
): (c: Candle) => number {
  const name = choiceSetting(settings, 'source', 'close', Object.keys(SOURCES));
  const source = SOURCES[name];
  if (!source) throw new Error(`Invalid setting source: ${name}`);
  return source;
}

/**
 * Plans the computation of one plugin's values.
 * @param pluginId Plugin id, one of `DEMO_STUDY_PLUGINS`.
 * @param settings Plugin settings.
 * @param timeframe Series timeframe.
 * @param calendar Session calendar of the instrument.
 * @return First computed bar and the value function.
 * @throws `Error` for invalid settings.
 */
function studyPlan(
  pluginId: string,
  settings: Readonly<Record<string, unknown>>,
  timeframe: Timeframe,
  calendar: BarCalendar
): StudyPlan {
  /**
   * Returns the start of the bar some bars before another one.
   * @param bars Earlier bars needed (capped at `WARMUP_CAP`).
   * @return Function from the first served bar to the first computed one.
   */
  function barsBefore(bars: number): (fromMs: number) => number {
    return fromMs =>
      calendar.shift(timeframe, fromMs, -Math.min(bars, WARMUP_CAP));
  }
  switch (pluginId) {
    case 'sma':
    case 'ema': {
      const length = intSetting(settings, 'length', 20, [1, 5000]);
      const source = sourceOf(settings);
      const average = pluginId === 'sma' ? smaValues : emaValues;
      return {
        // The EMA has no warm-up of its own; earlier bars let it settle.
        firstBar: barsBefore(pluginId === 'sma' ? length - 1 : 4 * length),
        compute: candles =>
          average(candles.map(source), length).map(value => ({
            [pluginId]: decimalText(value),
          })),
      };
    }
    case 'bollinger_bands': {
      const length = intSetting(settings, 'length', 20, [1, 5000]);
      const mult = decimalSetting(settings, 'mult', '2', [0.001, 50]);
      const source = sourceOf(settings);
      return {
        firstBar: barsBefore(length - 1),
        compute: candles =>
          bollingerValues(candles.map(source), length, mult).map(point => ({
            basis: decimalText(point?.basis ?? null),
            upper: decimalText(point?.upper ?? null),
            lower: decimalText(point?.lower ?? null),
            bandwidth: decimalText(point?.bandwidth ?? null),
            percent_b: decimalText(point?.percentB ?? null),
          })),
      };
    }
    case 'rsi': {
      const length = intSetting(settings, 'length', 14, [1, 500]);
      const source = sourceOf(settings);
      return {
        firstBar: barsBefore(4 * length + 1),
        compute: candles =>
          rsiValues(candles.map(source), length).map(value => ({
            rsi: decimalText(value),
          })),
      };
    }
    case 'atr': {
      const length = intSetting(settings, 'length', 14, [1, 500]);
      return {
        firstBar: barsBefore(4 * length),
        compute: candles =>
          atrValues(candles, length).map(value => ({
            atr: decimalText(value),
          })),
      };
    }
    case 'anchored_vwap': {
      const anchor = choiceSetting(
        settings,
        'anchor',
        'session',
        Object.keys(VWAP_PERIODS)
      );
      const period = VWAP_PERIODS[anchor] ?? '1d';
      return {
        // The first point's VWAP sums every bar since its period started.
        firstBar: fromMs =>
          Math.min(
            fromMs,
            calendar.floor(timeframe, calendar.floor(period, fromMs))
          ),
        compute: candles =>
          periodVwapPoints(
            candles,
            start => calendar.floor(period, start),
            settings
          ).map(point => point.values),
      };
    }
    case 'volume_delta':
    default: {
      const estimate =
        choiceSetting(settings, 'delta_source', 'auto', [
          'auto',
          'estimate',
        ]) === 'estimate';
      return {
        firstBar: fromMs => fromMs,
        compute: candles =>
          candles.map(candle => demoDeltaPoint(candle, estimate).values),
      };
    }
  }
}

/**
 * Serves a DEMO series for the built-in studies.
 * @param spec DEMO instrument.
 * @param query Series query; `fromMs` defaults to 199 bars before the last
 *     closed bar and is moved up to at most `DEMO_STUDY_POINT_CAP` points.
 * @param nowMs Current time in epoch ms.
 * @param calendar Session calendar of the instrument (default `24x7`).
 * @return Series with one point per bar from `fromMs` through the last
 *     closed bar (values null while a study warms up), or null when the
 *     plugin is not served by this module.
 * @throws `Error` for invalid settings (the engine answers 422).
 */
export function demoStudySeries(
  spec: DemoInstrumentSpec,
  query: IndicatorSeriesQuery,
  nowMs: number,
  calendar: BarCalendar = CONTINUOUS_CALENDAR
): IndicatorSeries | null {
  if (!DEMO_STUDY_PLUGINS.includes(query.pluginId)) return null;
  const tf = query.timeframe;
  const plan = studyPlan(query.pluginId, query.settings ?? {}, tf, calendar);
  const lastStart = calendar.lastClosedStart(tf, nowMs) ?? 0;
  const requested = calendar.floor(
    tf,
    query.fromMs ?? calendar.shift(tf, lastStart, -(DEFAULT_POINTS - 1))
  );
  const fromMs =
    calendar.barsBetween(tf, requested, lastStart) >= DEMO_STUDY_POINT_CAP
      ? calendar.shift(tf, lastStart, -(DEMO_STUDY_POINT_CAP - 1))
      : requested;
  const candles: Candle[] = [];
  for (
    let t = plan.firstBar(fromMs);
    t <= lastStart;
    t = calendar.shift(tf, t, 1)
  ) {
    candles.push(demoCandle(spec, tf, t, nowMs, calendar));
  }
  const values = plan.compute(candles);
  const points: IndicatorSeriesPoint[] = [];
  candles.forEach((candle, i) => {
    if (candle.start_ms >= fromMs) {
      points.push({bar_start_ms: candle.start_ms, values: values[i] ?? {}});
    }
  });
  return {
    instrument_id: spec.instrumentId,
    timeframe: tf,
    plugin_id: query.pluginId,
    indicator_version: 'demo',
    points,
    as_of_ms: calendar.endOf(tf, lastStart),
    feed_state: 'DEMO',
  };
}
