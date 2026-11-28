/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview DEMO study evaluator: produces `ScriptResult`-shaped
 * outputs of the engine's built-in indicator scripts over DEMO candles.
 *
 * DEMO mode has no Python engine, so this module generates the data the
 * engine would return for the nine built-in indicator ids. It is DEMO data
 * generation, not an alternative indicator implementation: each study
 * follows its Python built-in (`joseph.scripts.builtin.indicators`) step
 * by step through a small context with the same plot and drawing calls,
 * and a parity test checks the outputs against values exported from the
 * Python built-ins on fixed bars
 * (`contracts/fixtures/builtin-indicator-outputs.json`). Owned and plugin
 * scripts cannot run in DEMO.
 */

import type {
  PlotSeries,
  ScriptAlertEvent,
  ScriptVisual,
} from '@joseph/contracts';

/** One source bar in the engine's script shape. */
export interface StudyBar {
  /** Bar start, UTC epoch ms. */
  time_ms: number;
  /** Bar end (exclusive), UTC epoch ms. */
  end_ms: number;
  /** Whether the bar has closed. */
  is_final: boolean;
  /** Open price. */
  open: number;
  /** High price. */
  high: number;
  /** Low price. */
  low: number;
  /** Close price. */
  close: number;
  /** Base volume. */
  volume: number;
  /** Taker (or classified) buy volume; null when unpublished. */
  taker_buy_volume?: number | null;
  /** `taker` or `classified`; null with no buy volume. */
  taker_buy_method?: string | null;
}

/** Everything one evaluation reads. */
export interface StudyRun {
  /** Source bars, oldest first. */
  bars: readonly StudyBar[];
  /** Effective inputs (defaults merged with overrides). */
  inputs: Readonly<Record<string, unknown>>;
  /** Chart timeframe, e.g. `15m`. */
  timeframe: string;
  /** Bars of the declared requests by alias. */
  extra: Readonly<Record<string, readonly StudyBar[]>>;
  /** Calendar period [start, next start] per bar, by period (`1d`...). */
  periods: Readonly<Record<string, ReadonlyArray<readonly [number, number]>>>;
}

/** Plots, drawings and alerts of one evaluation. */
export interface StudyOutput {
  /** Scalar series. */
  plots: PlotSeries[];
  /** Style of each series. */
  plot_styles: Record<string, Record<string, unknown>>;
  /** Persistent drawings. */
  visuals: ScriptVisual[];
  /** Matched conditions on confirmed bars. */
  alerts: ScriptAlertEvent[];
}

/** Plot styling options. */
interface PlotOptions {
  /** Hex color. */
  color?: string;
  /** line, step, area, histogram, columns, circles or crosses. */
  style?: string;
  /** Pixel width. */
  width?: number;
  /** Offset in bars. */
  offset?: number;
  /** `all` or `data_window`. */
  display?: string;
}

/** The subset of the Python script context the built-ins use. */
class StudyContext {
  readonly plots = new Map<string, PlotSeries['points']>();
  readonly styles: Record<string, Record<string, unknown>> = {};
  readonly visuals = new Map<string, ScriptVisual>();
  readonly alerts: ScriptAlertEvent[] = [];
  index = -1;
  current: StudyBar | null = null;
  private readonly cursors = new Map<string, number>();
  private readonly closed = new Map<string, number>();

  /**
   * Starts a run.
   * @param run Bars, inputs and extra series.
   */
  constructor(readonly run: StudyRun) {}

  /**
   * The current bar.
   * @return The current bar.
   * @throws `Error` before the first bar.
   */
  get bar(): StudyBar {
    if (!this.current) throw new Error('no current bar');
    return this.current;
  }

  /**
   * The chart timeframe.
   * @return The chart timeframe.
   */
  get timeframe(): string {
    return this.run.timeframe;
  }

  /**
   * Whether this is the first bar.
   * @return Whether this is the first bar.
   */
  get isFirst(): boolean {
    return this.index === 0;
  }

  /**
   * Whether this is the last bar.
   * @return Whether this is the last bar.
   */
  get isLast(): boolean {
    return this.index === this.run.bars.length - 1;
  }

  /**
   * Whether the current bar has closed.
   * @return Whether the current bar has closed.
   */
  get isConfirmed(): boolean {
    return this.bar.is_final;
  }

  /**
   * Reads an input.
   * @param name Input name.
   * @return Its value.
   */
  input(name: string): unknown {
    return this.run.inputs[name];
  }

  /**
   * Reads a numeric input.
   * @param name Input name.
   * @return Its number.
   */
  num(name: string): number {
    return Number(this.run.inputs[name]);
  }

  /**
   * Reads a string input.
   * @param name Input name.
   * @return Its text.
   */
  str(name: string): string {
    return String(this.run.inputs[name]);
  }

  /**
   * Reads the intrabars inside the current bar (`ctx.intrabars`).
   * @param alias Request alias.
   * @return Closed lower-timeframe bars inside the bar.
   */
  intrabars(alias: string): StudyBar[] {
    const series = this.run.extra[alias] ?? [];
    const start = this.bar.time_ms;
    const end = this.bar.end_ms;
    let index = this.cursors.get(alias) ?? 0;
    while ((series[index]?.time_ms ?? Infinity) < start) {
      index += 1;
    }
    this.cursors.set(alias, index);
    const found: StudyBar[] = [];
    for (let bar = series[index]; bar && bar.end_ms <= end;) {
      found.push(bar);
      index += 1;
      bar = series[index];
    }
    return found;
  }

  /**
   * Reads the latest request bar closed by the current close.
   * @param alias Request alias.
   * @return That bar, or null.
   */
  closedBar(alias: string): StudyBar | null {
    const series = this.run.extra[alias] ?? [];
    let index = this.closed.get(alias) ?? -1;
    while ((series[index + 1]?.end_ms ?? Infinity) <= this.bar.end_ms) {
      index += 1;
    }
    this.closed.set(alias, index);
    return series[index] ?? null;
  }

  /**
   * Reads the calendar period around the current bar (`ctx.period`).
   * @param tf Period.
   * @return Start and next start, UTC epoch ms.
   * @throws `Error` for a period the study does not declare.
   */
  period(tf: string): readonly [number, number] {
    const bounds = this.run.periods[tf]?.[this.index];
    if (!bounds) throw new Error('declare the period in PERIODS');
    return bounds;
  }

  /**
   * Publishes a plot value on the current bar.
   * @param name Series name.
   * @param value Value or null.
   * @param options Styling.
   */
  plot(name: string, value: number | null, options: PlotOptions = {}): void {
    const color = options.color ?? '#5cb8e6';
    const style: Record<string, unknown> = {
      color,
      style: options.style ?? 'line',
      width: options.width ?? 2,
      offset: options.offset ?? 0,
    };
    if (options.display && options.display !== 'all') {
      style.display = options.display;
    }
    this.styles[name] = style;
    const points = this.plots.get(name) ?? [];
    this.plots.set(name, points);
    const point = {time_ms: this.bar.time_ms, value, color};
    if (points.at(-1)?.time_ms === point.time_ms) {
      points[points.length - 1] = point;
    } else points.push(point);
  }

  /**
   * Creates or replaces a drawing.
   * @param kind Drawing category.
   * @param id Stable id.
   * @param data Drawing fields.
   */
  visual(kind: ScriptVisual['kind'], id: string, data: ScriptVisual['data']) {
    this.visuals.set(id, {id, kind, data});
  }

  /**
   * Deletes a drawing.
   * @param id Drawing id.
   */
  delete(id: string): void {
    this.visuals.delete(id);
  }

  /**
   * Draws a line (`ctx.line`).
   * @param from First anchor [ms, price].
   * @param to Second anchor [ms, price].
   * @param id Stable id.
   * @param color Hex color.
   * @param width Width.
   * @param style solid, dashed or dotted.
   */
  line(
    from: readonly [number, number],
    to: readonly [number, number],
    id: string,
    color: string,
    width = 1,
    style = 'solid'
  ): void {
    this.visual('line', id, {
      from_ms: from[0],
      from_price: from[1],
      to_ms: to[0],
      to_price: to[1],
      color,
      width,
      style,
      extend: 'none',
    });
  }

  /**
   * Places a label (`ctx.label`).
   * @param text Text.
   * @param timeMs Anchor time.
   * @param price Anchor price.
   * @param id Stable id.
   * @param color Hex color.
   * @param position at, above or below.
   */
  label(
    text: string,
    timeMs: number,
    price: number,
    id: string,
    color: string,
    position: string
  ): void {
    this.visual('label', id, {
      text: text.slice(0, 300),
      time_ms: timeMs,
      price,
      color,
      position,
    });
  }

  /**
   * Draws a horizontal level (`ctx.hline`).
   * @param name Level id and text.
   * @param price Level.
   * @param style solid, dashed or dotted.
   */
  hline(name: string, price: number, style = 'dashed'): void {
    this.visual('hline', name, {
      price,
      color: '#8fa5b3',
      style,
      text: name,
    });
  }

  /**
   * Shades between two plots or levels (`ctx.fill`).
   * @param first First name.
   * @param second Second name.
   * @param color Hex color.
   * @param opacity Alpha.
   */
  fill(first: string, second: string, color: string, opacity: number): void {
    this.visual('fill', `fill:${first}:${second}`, {
      first,
      second,
      color,
      opacity,
    });
  }

  /**
   * Fills between two lines (`ctx.linefill`).
   * @param first First line id.
   * @param second Second line id.
   * @param color Hex color.
   * @param opacity Alpha.
   */
  linefill(
    first: string,
    second: string,
    color: string,
    opacity: number
  ): void {
    this.visual('linefill', `linefill:${first}:${second}`, {
      first,
      second,
      color,
      opacity,
    });
  }

  /**
   * Shows a table (`ctx.table`).
   * @param name Table id.
   * @param rows Text rows.
   * @param position Screen anchor.
   */
  table(name: string, rows: string[][], position = 'top_right'): void {
    this.visual('table', name, {
      rows,
      position,
      color: '#e4edf1',
      background: '#122431',
    });
  }

  /**
   * Records an alert on a confirmed bar (`ctx.alert`).
   * @param message Text.
   * @param name Condition name.
   */
  alert(message: string, name: string): void {
    if (!this.bar.is_final) return;
    const event: ScriptAlertEvent = {
      name,
      message,
      time_ms: this.bar.time_ms,
      severity: 'INFO',
    };
    const last = this.alerts.at(-1);
    if (last && last.name === name && last.time_ms === event.time_ms) {
      this.alerts[this.alerts.length - 1] = event;
    } else this.alerts.push(event);
  }
}

/** A study instance: called once per bar. */
type Study = (ctx: StudyContext) => void;

/** One built-in: its calendar periods and instance factory. */
interface Builtin {
  /** Calendar periods the study reads. */
  periods: readonly string[];
  /** Creates a fresh instance. */
  create: () => Study;
}

/**
 * Reads a TradingView price source.
 * @param bar Source bar.
 * @param source Source name.
 * @return Price.
 */
function sourceValue(bar: StudyBar, source: string): number {
  if (source === 'hl2') return (bar.high + bar.low) / 2;
  if (source === 'hlc3') return (bar.high + bar.low + bar.close) / 3;
  if (source === 'ohlc4') {
    return (bar.open + bar.high + bar.low + bar.close) / 4;
  }
  return bar[source as 'open' | 'high' | 'low' | 'close'];
}

/**
 * Sums numbers.
 * @param values Numbers.
 * @return Their sum.
 */
function sum(values: Iterable<number>): number {
  let total = 0;
  for (const value of values) {
    total += value;
  }
  return total;
}

/**
 * Formats like Python's `{:.6g}`.
 * @param value Number.
 * @return Text.
 */
export function formatG6(value: number): string {
  if (value === 0) return '0';
  const exponent = Math.floor(Math.log10(Math.abs(value)));
  if (exponent < -4 || exponent >= 6) {
    const [mantissa = '0', power = '0'] = value.toExponential(5).split('e');
    const trimmed = mantissa.replace(/\.?0+$/, '');
    const sign = power.startsWith('-') ? '-' : '+';
    return `${trimmed}e${sign}${power.replace(/^[+-]/, '').padStart(2, '0')}`;
  }
  const text = value.toFixed(Math.max(0, 5 - exponent));
  return text.includes('.') ? text.replace(/\.?0+$/, '') : text;
}

/**
 * Creates a moving average study.
 * @param name Plot name.
 * @param exponential EMA instead of SMA.
 * @return Factory.
 */
function movingAverage(name: string, exponential: boolean): () => Study {
  return () => {
    const window: number[] = [];
    let value: number | null = null;
    return ctx => {
      const length = ctx.num('length');
      const price = sourceValue(ctx.bar, ctx.str('source'));
      let out: number | null;
      if (exponential) {
        const alpha = 2 / (length + 1);
        value = value === null ? price : alpha * price + (1 - alpha) * value;
        out = value;
      } else {
        window.push(price);
        while (window.length > length) {
          window.shift();
        }
        out = window.length === length ? sum(window) / length : null;
      }
      ctx.plot(name, out, {color: ctx.str('color'), offset: ctx.num('offset')});
    };
  };
}

/**
 * Creates a Bollinger Bands instance.
 * @return A Bollinger Bands instance.
 */
function bollinger(): Study {
  const window: number[] = [];
  return ctx => {
    const length = ctx.num('length');
    const price = sourceValue(ctx.bar, ctx.str('source'));
    window.push(price);
    while (window.length > length) {
      window.shift();
    }
    let basis: number | null = null;
    let upper: number | null = null;
    let lower: number | null = null;
    let bandwidth: number | null = null;
    let percentB: number | null = null;
    if (window.length === length) {
      const mean = sum(window) / length;
      const variance = sum(window.map(x => (x - mean) ** 2)) / length;
      const dev = ctx.num('mult') * Math.sqrt(variance);
      basis = mean;
      upper = mean + dev;
      lower = mean - dev;
      if (mean !== 0) bandwidth = ((upper - lower) / mean) * 100;
      if (upper !== lower) percentB = (price - lower) / (upper - lower);
    }
    const offset = ctx.num('offset');
    ctx.plot('Basis', basis, {color: ctx.str('basis_color'), width: 1, offset});
    ctx.plot('Upper', upper, {color: ctx.str('upper_color'), width: 1, offset});
    ctx.plot('Lower', lower, {color: ctx.str('lower_color'), width: 1, offset});
    ctx.plot('Bandwidth', bandwidth, {display: 'data_window'});
    ctx.plot('%B', percentB, {display: 'data_window'});
    if (ctx.isFirst) ctx.fill('Upper', 'Lower', ctx.str('fill_color'), 0.08);
  };
}

/** Wilder moving average seeded with the simple average. */
class Rma {
  private seed: number[] = [];
  value: number | null = null;

  /**
   * Folds a value.
   * @param x Value.
   * @param length Smoothing length.
   * @return The average, null while seeding.
   */
  add(x: number, length: number): number | null {
    if (this.value !== null) {
      const alpha = 1 / length;
      this.value = alpha * x + (1 - alpha) * this.value;
    } else {
      this.seed.push(x);
      if (this.seed.length === length) {
        this.value = sum(this.seed) / length;
        this.seed = [];
      }
    }
    return this.value;
  }
}

/**
 * Creates an RSI instance.
 * @return An RSI instance.
 */
function rsi(): Study {
  let previous: number | null = null;
  const up = new Rma();
  const down = new Rma();
  return ctx => {
    const length = ctx.num('length');
    const price = sourceValue(ctx.bar, ctx.str('source'));
    let value: number | null = null;
    if (previous !== null) {
      const gain = up.add(Math.max(price - previous, 0), length);
      const loss = down.add(Math.max(previous - price, 0), length);
      if (gain !== null && loss !== null) {
        value =
          loss === 0 ? 100 : gain === 0 ? 0 : 100 - 100 / (1 + gain / loss);
      }
    }
    previous = price;
    ctx.plot('RSI', value, {color: ctx.str('color'), width: 1});
    if (ctx.isFirst) {
      ctx.hline('Upper band', ctx.num('upper'));
      ctx.hline('Middle band', ctx.num('middle'), 'dotted');
      ctx.hline('Lower band', ctx.num('lower'));
      ctx.fill('Upper band', 'Lower band', ctx.str('color'), 0.08);
    }
  };
}

/**
 * Creates an ATR instance.
 * @return An ATR instance.
 */
function atr(): Study {
  let previousClose: number | null = null;
  const average = new Rma();
  return ctx => {
    const {high, low} = ctx.bar;
    let range = high - low;
    if (previousClose !== null) {
      range = Math.max(
        range,
        Math.abs(high - previousClose),
        Math.abs(low - previousClose)
      );
    }
    previousClose = ctx.bar.close;
    ctx.plot('ATR', average.add(range, ctx.num('length')), {
      color: ctx.str('color'),
      width: 1,
    });
  };
}

/** Volume delta method labels, as the Python table shows them. */
const METHOD_LABELS: Readonly<Record<string, string>> = {
  taker_volume: 'Taker buy volume',
  trade_classification: 'Classified trades',
  candle_direction: 'Intrabar estimate',
};

/**
 * Creates a volume delta instance.
 * @return A volume delta instance.
 */
function volumeDelta(): Study {
  let lastClose: number | null = null;
  let lastDirection = 0;
  return ctx => {
    const intrabars = ctx.intrabars('intrabars');
    let buy = 0;
    let sell = 0;
    let covered = 0;
    for (const bar of intrabars) {
      let current = Math.sign(bar.close - bar.open);
      if (current === 0 && lastClose !== null) {
        current = Math.sign(bar.close - lastClose);
      }
      if (current !== 0) lastDirection = current;
      if (lastDirection > 0) buy += bar.volume;
      else if (lastDirection < 0) sell += bar.volume;
      covered += bar.volume;
      lastClose = bar.close;
    }
    const volume = ctx.bar.volume;
    const taker = ctx.bar.taker_buy_volume ?? null;
    const estimated = !(ctx.input('use_buy_volume') && taker !== null);
    const count = estimated ? intrabars.length : 0;
    let coverage: number | null;
    let method: string;
    let sufficient: boolean;
    if (taker !== null && !estimated) {
      buy = taker;
      sell = volume - taker;
      covered = volume;
      coverage = 100;
      method =
        ctx.bar.taker_buy_method === 'classified'
          ? 'trade_classification'
          : 'taker_volume';
      sufficient = true;
    } else {
      coverage = volume > 0 ? (covered / volume) * 100 : null;
      method = 'candle_direction';
      sufficient =
        coverage !== null &&
        count >= ctx.num('min_intrabars') &&
        coverage >= ctx.num('min_coverage') * 100;
    }
    const deltaPct =
      sufficient && covered > 0 ? ((buy - sell) / covered) * 100 : null;
    const color = ctx.str((deltaPct ?? 0) >= 0 ? 'up_color' : 'down_color');
    ctx.plot('Delta %', deltaPct, {
      color: estimated && color.length === 7 ? `${color}99` : color,
      style: 'histogram',
      width: 1,
    });
    const shown = sufficient && covered > 0;
    const data = {display: 'data_window'};
    ctx.plot('Delta', shown ? buy - sell : null, data);
    ctx.plot('Buy volume', shown ? buy : null, data);
    ctx.plot('Sell volume', shown ? sell : null, data);
    ctx.plot('Coverage %', coverage, data);
    ctx.plot('Intrabars', count, data);
    if (ctx.isFirst) ctx.hline('Zero', 0, 'solid');
    if (ctx.isLast) {
      const detail =
        estimated && !sufficient
          ? 'Insufficient intrabar coverage'
          : (METHOD_LABELS[method] ?? method);
      ctx.table(
        'Volume delta',
        [
          ['Method', detail],
          ['Coverage', coverage === null ? 'n/a' : `${coverage.toFixed(0)}%`],
        ],
        'top_left'
      );
    }
  };
}

/** Unit lengths for ordering resolutions (a month counts thirty days). */
const UNIT_MS: Readonly<Record<string, number>> = {
  s: 1000,
  m: 60_000,
  h: 3_600_000,
  d: 86_400_000,
  w: 604_800_000,
  M: 2_592_000_000,
};

/**
 * Approximates a resolution's length.
 * @param timeframe Count and unit, e.g. `15m`.
 * @return Milliseconds.
 */
function durationMs(timeframe: string): number {
  return (
    Number(timeframe.slice(0, -1) || 1) * (UNIT_MS[timeframe.slice(-1)] ?? 0)
  );
}

/**
 * Picks the `auto` period for a chart resolution.
 * @param timeframe Chart resolution.
 * @return `1d`, `1w`, `1M` or null.
 */
export function autoPeriod(timeframe: string): string | null {
  for (const period of ['1d', '1w', '1M']) {
    if (durationMs(timeframe) < durationMs(period)) return period;
  }
  return null;
}

/** Pivot level keys in publication order. */
const LEVELS = ['p', 'r1', 'r2', 'r3', 's1', 's2', 's3'] as const;

/**
 * Creates a pivot points instance.
 * @return A pivot points instance.
 */
function pivotPoints(): Study {
  let sourceStart: number | null = null;
  let applies: number | null = null;
  let levels: Record<string, number> = {};
  let tested = new Set<string>();
  const shown: number[] = [];
  const color = (ctx: StudyContext, key: string) =>
    ctx.str(
      `${key.startsWith('p') ? 'pivot' : key.startsWith('r') ? 'resistance' : 'support'}_color`
    );
  const draw = (ctx: StudyContext, until: number) => {
    if (applies === null) return;
    for (const [key, price] of Object.entries(levels)) {
      const name = `pivot:${applies}:${key}`;
      ctx.line(
        [applies, price],
        [until, price],
        name,
        color(ctx, key),
        1,
        'dashed'
      );
      if (ctx.input('show_labels')) {
        ctx.label(
          `${key.toUpperCase()} (${formatG6(price)})`,
          applies,
          price,
          `${name}:label`,
          color(ctx, key),
          'above'
        );
      }
    }
  };
  return ctx => {
    const chosen = ctx.str('period');
    let period: string | null = chosen;
    if (chosen === 'auto') period = autoPeriod(ctx.timeframe);
    else if (durationMs(ctx.timeframe) >= durationMs(chosen)) {
      throw new Error(
        `the ${ctx.timeframe} chart must be shorter than the ${chosen} period`
      );
    }
    if (period === null) {
      if (ctx.isLast) ctx.table('Pivots', [['Pivots need a chart below 1M']]);
      return;
    }
    const [start, following] = ctx.period(period);
    const completed = ctx.closedBar('period');
    if (
      completed &&
      (sourceStart === null || completed.time_ms > sourceStart)
    ) {
      const due = start <= completed.time_ms ? following : start;
      if (ctx.bar.end_ms >= due) {
        draw(ctx, due);
        sourceStart = completed.time_ms;
        applies = due;
        const {high, low, close} = completed;
        const pivot = (high + low + close) / 3;
        levels = {
          p: pivot,
          r1: 2 * pivot - low,
          r2: pivot + (high - low),
          r3: high + 2 * (pivot - low),
          s1: 2 * pivot - high,
          s2: pivot - (high - low),
          s3: low - 2 * (high - pivot),
        };
        tested = new Set();
        shown.push(due);
        while (shown.length > ctx.num('pivots_back')) {
          const old = shown.shift();
          for (const key of LEVELS) {
            ctx.delete(`pivot:${old}:${key}`);
            ctx.delete(`pivot:${old}:${key}:label`);
          }
        }
      }
    }
    if (applies !== null && ctx.isConfirmed) {
      for (const [key, price] of Object.entries(levels)) {
        if (
          !tested.has(key) &&
          ctx.bar.time_ms >= applies &&
          ctx.bar.low <= price &&
          price <= ctx.bar.high
        ) {
          tested.add(key);
          ctx.alert(
            `Price touched pivot ${key.toUpperCase()} at ${formatG6(price)}`,
            `pivot_${key}`
          );
        }
      }
    }
    if (ctx.isLast && applies !== null) {
      draw(ctx, start >= applies ? following : applies + (applies - start));
    }
  };
}

/** Periods of the VWAP anchors. */
const VWAP_PERIODS: Readonly<Record<string, string>> = {
  session: '1d',
  week: '1w',
  month: '1M',
};

/**
 * Creates an anchored VWAP instance.
 * @return An anchored VWAP instance.
 */
function anchoredVwap(): Study {
  let periodStart: number | null = null;
  let started = false;
  let outside = false;
  let bars = 0;
  let sums: [number, number, number] = [0, 0, 0];
  const reset = () => {
    started = true;
    bars = 0;
    sums = [0, 0, 0];
  };
  return ctx => {
    const anchor = ctx.str('anchor');
    if (anchor === 'time') {
      const anchorMs = ctx.num('anchor_ms');
      if (!(anchorMs > 0)) {
        throw new Error('anchor_ms is required for the time anchor');
      }
      if (ctx.isFirst && ctx.bar.time_ms > anchorMs) outside = true;
      if (!started && !outside && ctx.bar.end_ms > anchorMs) reset();
    } else {
      const [start] = ctx.period(VWAP_PERIODS[anchor] ?? '1d');
      if (start !== periodStart) {
        periodStart = start;
        reset();
      }
    }
    let vwap: number | null = null;
    let stdev: number | null = null;
    if (started) {
      bars += 1;
      const volume = ctx.bar.volume;
      if (volume > 0) {
        const price = sourceValue(ctx.bar, ctx.str('source'));
        sums = [
          sums[0] + price * volume,
          sums[1] + volume,
          sums[2] + price * price * volume,
        ];
      }
      if (sums[1] > 0) {
        vwap = sums[0] / sums[1];
        stdev = Math.sqrt(Math.max(0, sums[2] / sums[1] - vwap * vwap));
      }
    }
    ctx.plot('VWAP', vwap, {color: ctx.str('color')});
    const bandColor = ctx.str('band_color');
    const multipliers = [1, 2, 3].map(k => ctx.num(`band_${k}`));
    multipliers.forEach((multiplier, offset) => {
      if (multiplier <= 0) return;
      for (const [name, sign] of [
        ['Upper', 1],
        ['Lower', -1],
      ] as const) {
        ctx.plot(
          `${name} ${offset + 1}`,
          vwap === null || stdev === null
            ? null
            : vwap + sign * multiplier * stdev,
          {color: bandColor, width: 1}
        );
      }
    });
    ctx.plot('Stdev', stdev, {display: 'data_window'});
    ctx.plot('Bars', started ? bars : null, {display: 'data_window'});
    if (ctx.isFirst && (multipliers[0] ?? 0) > 0) {
      ctx.fill('Upper 1', 'Lower 1', bandColor, 0.08);
    }
    if (ctx.isLast && outside) {
      ctx.table('VWAP', [['Anchor is outside the loaded bars']], 'top_left');
    }
  };
}

/**
 * Creates a regression channel instance.
 * @return A regression channel instance.
 */
function regressionChannel(): Study {
  const rows: Array<[number, number, number, number]> = [];
  let started = false;
  let outside = false;
  let done = false;
  return ctx => {
    if (done) return;
    const anchorMs = ctx.num('anchor_ms');
    const endMs = ctx.num('end_ms');
    const bar = ctx.bar;
    if (anchorMs > 0) {
      if (ctx.isFirst && bar.time_ms > anchorMs) outside = true;
      if (!outside && bar.end_ms > anchorMs) started = true;
    } else started = true;
    if (started) {
      rows.push([
        bar.time_ms,
        sourceValue(bar, ctx.str('source')),
        bar.high,
        bar.low,
      ]);
      if (anchorMs <= 0) {
        while (rows.length > ctx.num('length')) {
          rows.shift();
        }
      }
    }
    if (!(ctx.isLast || (endMs > 0 && bar.end_ms > endMs))) return;
    done = true;
    if (outside) {
      ctx.table(
        'Regression',
        [['Anchor is outside the loaded bars']],
        'top_left'
      );
      return;
    }
    if (rows.length < 2 || (anchorMs <= 0 && rows.length < ctx.num('length'))) {
      return;
    }
    const count = rows.length;
    const [head] = rows;
    const tail = rows.at(-1);
    if (!head || !tail) return;
    const origin = head[1];
    const ys = rows.map(row => row[1] - origin);
    const meanY = sum(ys) / count;
    const centred = ys.map(y => y - meanY);
    const meanX = (count - 1) / 2;
    const sxx = (count * (count * count - 1)) / 12;
    const sxy = sum(centred.map((c, x) => x * c)) - meanX * sum(centred);
    const syy = sum(centred.map(c => c * c));
    const slope = sxy / sxx;
    const centre = origin + meanY;
    const intercept = centre - slope * meanX;
    const pearson =
      syy > 0 ? Math.min(1, Math.max(-1, sxy / Math.sqrt(sxx * syy))) : 0;
    const start = centre - slope * meanX;
    const end = centre + slope * meanX;
    const stdev = Math.sqrt(Math.max(0, syy - slope * sxy) / (count - 1));
    const values = {
      slope,
      stdev,
      pearson_r: pearson,
      max_up: Math.max(...rows.map((row, x) => row[2] - slope * x)) - intercept,
      max_down:
        intercept - Math.min(...rows.map((row, x) => row[3] - slope * x)),
    };
    const width = ctx.num('deviations') * stdev;
    const first = head[0];
    const last = tail[0];
    const line = ctx.str('color');
    const band = ctx.str('band_color');
    for (const [name, shift, stroke] of [
      ['centre', 0, line],
      ['upper', width, band],
      ['lower', -width, band],
    ] as const) {
      ctx.line(
        [first, start + shift],
        [last, end + shift],
        name,
        stroke,
        name === 'centre' ? 2 : 1
      );
    }
    ctx.linefill('upper', 'lower', band, 0.06);
    if (ctx.input('show_pearson')) {
      ctx.label(
        pearson.toFixed(4),
        first,
        start - width,
        'pearson',
        band,
        'below'
      );
    }
    for (const [name, key] of [
      ["Pearson's R", 'pearson_r'],
      ['Slope', 'slope'],
      ['Stdev', 'stdev'],
      ['Max up', 'max_up'],
      ['Max down', 'max_down'],
    ] as const) {
      ctx.plot(name, values[key], {display: 'data_window'});
    }
    ctx.plot('Bars', count, {display: 'data_window'});
  };
}

/** The DEMO evaluators of the built-in indicator scripts, by catalog id. */
const BUILTINS: Readonly<Record<string, Builtin>> = {
  'builtin:sma': {periods: [], create: movingAverage('SMA', false)},
  'builtin:ema': {periods: [], create: movingAverage('EMA', true)},
  'builtin:anchored_vwap': {periods: ['1d', '1w', '1M'], create: anchoredVwap},
  'builtin:regression_channel': {periods: [], create: regressionChannel},
  'builtin:bollinger_bands': {periods: [], create: bollinger},
  'builtin:rsi': {periods: [], create: rsi},
  'builtin:atr': {periods: [], create: atr},
  'builtin:volume_delta': {periods: [], create: volumeDelta},
  'builtin:pivot_points': {periods: ['1d', '1w', '1M'], create: pivotPoints},
};

/** Built-in indicator ids the DEMO evaluator serves. */
export const DEMO_STUDY_IDS: readonly string[] = Object.keys(BUILTINS);

/**
 * Lists the calendar periods a built-in reads (its `PERIODS`).
 * @param scriptId Catalog id.
 * @return Periods; empty for unknown ids.
 */
export function studyPeriods(scriptId: string): readonly string[] {
  return BUILTINS[scriptId]?.periods ?? [];
}

/**
 * Evaluates one built-in indicator over DEMO bars.
 * @param scriptId Catalog id such as `builtin:ema`.
 * @param run Bars, effective inputs, extra series and periods.
 * @return Plots, styles, drawings and alerts like the engine's result.
 * @throws `Error` for ids DEMO cannot run and for input errors the
 *     Python built-in raises too.
 */
export function evaluateBuiltin(scriptId: string, run: StudyRun): StudyOutput {
  const builtin = BUILTINS[scriptId];
  if (!builtin) {
    throw new Error('Connect a backend to run plugin and owned Python scripts');
  }
  const ctx = new StudyContext(run);
  const study = builtin.create();
  run.bars.forEach((bar, index) => {
    ctx.index = index;
    ctx.current = bar;
    study(ctx);
  });
  return {
    plots: [...ctx.plots].map(([name, points]) => ({name, points})),
    plot_styles: ctx.styles,
    visuals: [...ctx.visuals.values()],
    alerts: ctx.alerts,
  };
}
