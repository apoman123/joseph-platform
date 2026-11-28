/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview DEMO evaluation of the engine's built-in alert conditions
 * on synthetic DEMO bars. Each condition follows the Python built-in of the
 * same id (`joseph.scripts.builtin.alerts`), with the same event names.
 */

import {demoRsi, type DemoBar} from './demo-filters';

/** One matched condition on a closed bar. */
export interface DemoAlertEvent {
  /** Index of the bar in the evaluated window. */
  index: number;
  /** Stable condition name, e.g. `crossing-up`. */
  name: string;
  /** Plain-text message. */
  message: string;
}

/** Effective alert inputs. */
type Inputs = Readonly<Record<string, unknown>>;

/**
 * Formats a number like the engine (`.10g`).
 * @param value Number.
 * @return Text.
 */
function show(value: number): string {
  return String(Number(value.toPrecision(10)));
}

/**
 * Detects a cross (series above the other now, at or below before).
 * @param previous Series and other value on the previous bar.
 * @param current Series and other value on this bar.
 * @return Direction or null.
 */
function crossed(
  previous: readonly [number, number],
  current: readonly [number, number]
): 'up' | 'down' | null {
  if (current[0] > current[1] && previous[0] <= previous[1]) return 'up';
  if (current[0] < current[1] && previous[0] >= previous[1]) return 'down';
  return null;
}

/** A condition evaluated over a bar window. */
type Condition = (
  bars: readonly DemoBar[],
  inputs: Inputs,
  symbol: string
) => DemoAlertEvent[];

/**
 * Builds events for crossings of a per-bar pair series.
 * @param pairs Series and other value per bar, or null before warm-up.
 * @param inputs Condition input (crossing, crossing_up, crossing_down).
 * @param names Names of the up and down events.
 * @param message Message builder.
 * @return Events.
 */
function crossings(
  pairs: ReadonlyArray<readonly [number, number] | null>,
  inputs: Inputs,
  names: readonly [string, string],
  message: (direction: 'up' | 'down', index: number) => string
): DemoAlertEvent[] {
  const events: DemoAlertEvent[] = [];
  pairs.forEach((pair, index) => {
    const previous = index > 0 ? pairs[index - 1] : null;
    if (!pair || !previous) return;
    const direction = crossed(previous, pair);
    if (direction === 'up' && inputs.condition !== 'crossing_down') {
      events.push({index, name: names[0], message: message('up', index)});
    }
    if (direction === 'down' && inputs.condition !== 'crossing_up') {
      events.push({index, name: names[1], message: message('down', index)});
    }
  });
  return events;
}

/** DEMO alert conditions by catalog id. */
const CONDITIONS: Readonly<Record<string, Condition>> = {
  'builtin:price-cross': (bars, inputs, symbol) => {
    const level = Number(inputs.level);
    return crossings(
      bars.map(b => [b.close, level] as const),
      inputs,
      ['crossing-up', 'crossing-down'],
      (direction, i) =>
        `${symbol} crossed ${direction} ${show(level)}: close ${show(bars[i]?.close ?? NaN)}`
    );
  },
  'builtin:channel': (bars, inputs, symbol) => {
    const upper = Number(inputs.upper);
    const lower = Number(inputs.lower);
    if (lower >= upper) {
      throw new Error('the lower level must be below the upper level');
    }
    const condition = String(inputs.condition);
    const verbs: Record<string, string> = {
      entering: 'entered',
      exiting: 'exited',
      inside: 'closed inside',
      outside: 'closed outside',
    };
    const events: DemoAlertEvent[] = [];
    bars.forEach((bar, index) => {
      const inside = lower <= bar.close && bar.close <= upper;
      const before = bars[index - 1] ?? null;
      const wasInside =
        before === null ? null : lower <= before.close && before.close <= upper;
      const changed = wasInside !== null && wasInside !== inside;
      const matched = {
        entering: changed && inside,
        exiting: changed && !inside,
        inside,
        outside: !inside,
      }[condition];
      if (matched) {
        events.push({
          index,
          name: `${condition}-channel`,
          message: `${symbol} ${verbs[condition]} the channel ${show(lower)}-${show(upper)}: close ${show(bar.close)}`,
        });
      }
    });
    return events;
  },
  'builtin:ma-cross': (bars, inputs, symbol) => {
    const fast = Number(inputs.fast_length);
    const slow = Number(inputs.slow_length);
    const kind = String(inputs.ma_type);
    const ema = new Map<number, number>();
    const pairs = bars.map((bar, index) => {
      for (const length of new Set([fast, slow])) {
        const last = ema.get(length);
        const alpha = 2 / (length + 1);
        ema.set(
          length,
          last === undefined
            ? bar.close
            : alpha * bar.close + (1 - alpha) * last
        );
      }
      const value = (length: number) => {
        if (kind !== 'SMA') return ema.get(length) ?? bar.close;
        if (index + 1 < length) return null;
        const window = bars.slice(index + 1 - length, index + 1);
        return window.reduce((a, b) => a + b.close, 0) / length;
      };
      const f = value(fast);
      const s = value(slow);
      return f === null || s === null ? null : ([f, s] as const);
    });
    return crossings(
      pairs,
      inputs,
      ['ma-cross-up', 'ma-cross-down'],
      (direction, i) =>
        `${symbol} ${kind} ${fast}/${slow} crossed ${direction} at close ${show(bars[i]?.close ?? NaN)}`
    );
  },
  'builtin:rsi-level': (bars, inputs, symbol) => {
    const length = Number(inputs.length);
    const high = Number(inputs.overbought);
    const low = Number(inputs.oversold);
    const closes = bars.map(b => b.close);
    const values = closes.map((_, i) =>
      i < length ? null : demoRsi(closes.slice(0, i + 1), length)
    );
    const events: DemoAlertEvent[] = [];
    values.forEach((value, index) => {
      const previous = index > 0 ? values[index - 1] : null;
      if (value === null || previous === null || previous === undefined) return;
      if (inputs.condition !== 'oversold' && previous <= high && high < value) {
        events.push({
          index,
          name: 'rsi-overbought',
          message: `${symbol} RSI rose above ${show(high)}: ${value.toFixed(2)}`,
        });
      }
      if (inputs.condition !== 'overbought' && previous >= low && low > value) {
        events.push({
          index,
          name: 'rsi-oversold',
          message: `${symbol} RSI fell below ${show(low)}: ${value.toFixed(2)}`,
        });
      }
    });
    return events;
  },
  'builtin:volume-spike': (bars, inputs, symbol) => {
    const length = Number(inputs.length);
    const multiple = Number(inputs.multiplier);
    const events: DemoAlertEvent[] = [];
    bars.forEach((bar, index) => {
      if (index < length) return;
      const avg =
        bars.slice(index - length, index).reduce((a, b) => a + b.volume, 0) /
        length;
      if (avg > 0 && bar.volume > multiple * avg) {
        events.push({
          index,
          name: 'volume-spike',
          message: `${symbol} volume ${show(bar.volume)} is ${(bar.volume / avg).toFixed(1)}x the ${length}-bar average`,
        });
      }
    });
    return events;
  },
  'builtin:percent-change': (bars, inputs, symbol) => {
    const n = Number(inputs.bars);
    const threshold = Number(inputs.threshold_pct);
    const events: DemoAlertEvent[] = [];
    bars.forEach((bar, index) => {
      const base = index >= n ? (bars[index - n]?.close ?? 0) : 0;
      if (base <= 0) return;
      const change = ((bar.close - base) / base) * 100;
      if (inputs.direction !== 'down' && change >= threshold) {
        events.push({
          index,
          name: 'move-up',
          message: `${symbol} rose ${change.toFixed(2)}% over ${n} bars`,
        });
      }
      if (inputs.direction !== 'up' && change <= -threshold) {
        events.push({
          index,
          name: 'move-down',
          message: `${symbol} fell ${(-change).toFixed(2)}% over ${n} bars`,
        });
      }
    });
    return events;
  },
  'builtin:breakout': (bars, inputs, symbol) => {
    const length = Number(inputs.length);
    const events: DemoAlertEvent[] = [];
    bars.forEach((bar, index) => {
      if (index < length) return;
      const window = bars.slice(index - length, index);
      if (
        inputs.extreme !== 'low' &&
        bar.high > Math.max(...window.map(b => b.high))
      ) {
        events.push({
          index,
          name: 'new-high',
          message: `${symbol} made a ${length}-bar high at ${show(bar.high)}`,
        });
      }
      if (
        inputs.extreme !== 'high' &&
        bar.low < Math.min(...window.map(b => b.low))
      ) {
        events.push({
          index,
          name: 'new-low',
          message: `${symbol} made a ${length}-bar low at ${show(bar.low)}`,
        });
      }
    });
    return events;
  },
};

/**
 * Tells whether DEMO can evaluate an alert condition.
 * @param scriptId Catalog id.
 * @return True for the built-in alerts.
 */
export function hasDemoAlert(scriptId: string): boolean {
  return scriptId in CONDITIONS;
}

/**
 * Evaluates one built-in alert condition over closed DEMO bars.
 * @param scriptId Catalog id of a built-in alert.
 * @param bars Closed bars, oldest first.
 * @param inputs Effective inputs.
 * @param symbol Instrument id used in messages.
 * @return Matched events in bar order.
 * @throws `Error` for unknown ids or invalid inputs.
 */
export function demoAlertEvents(
  scriptId: string,
  bars: readonly DemoBar[],
  inputs: Inputs,
  symbol: string
): DemoAlertEvent[] {
  const run = CONDITIONS[scriptId];
  if (!run) throw new Error(`DEMO cannot run ${scriptId}`);
  return run(bars, inputs, symbol);
}
