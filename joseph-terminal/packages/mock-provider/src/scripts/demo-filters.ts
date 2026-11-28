/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview DEMO evaluation of the engine's built-in filters on the
 * synthetic DEMO instruments. Each function follows the Python built-in of
 * the same id (`joseph.scripts.builtin.filters`); missing data throws
 * `DemoUnavailable` and is reported as unavailable, never as a match.
 * DEMO never runs Python in the browser and never runs owned scripts.
 */

import type {Instrument, MarketSnapshot, OrderBook} from '@joseph/contracts';

/** A closed DEMO bar as numbers. */
export interface DemoBar {
  /** Opening price. */
  open: number;
  /** Highest price. */
  high: number;
  /** Lowest price. */
  low: number;
  /** Closing price. */
  close: number;
  /** Base volume. */
  volume: number;
}

/** Data a DEMO filter reads. */
export interface DemoFilterContext {
  /** Latest DEMO snapshot. */
  snapshot: MarketSnapshot;
  /** Instrument metadata. */
  instrument: Instrument;
  /** Closed bars, oldest first (empty without requested history). */
  bars: readonly DemoBar[];
  /** Order book, when the filter declares the metric. */
  book: OrderBook | null;
  /** Evaluation time (UTC ms). */
  nowMs: number;
}

/** Missing or stale data that keeps a filter from deciding. */
export class DemoUnavailable extends Error {}

/** Effective filter inputs. */
type Inputs = Readonly<Record<string, unknown>>;

/**
 * Parses a contract decimal string.
 * @param value Decimal string, number or null.
 * @return Finite number or null.
 */
function num(value: unknown): number | null {
  if (value === null || value === undefined || typeof value === 'boolean') {
    return null;
  }
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

/**
 * Applies a screener comparison (above/below strict, between inclusive).
 * @param value Measured value.
 * @param inputs Inputs with operator, value and value2.
 * @return Whether the value satisfies the comparison.
 */
function compare(value: number, inputs: Inputs): boolean {
  const first = Number(inputs.value);
  const second = Number(inputs.value2);
  if (inputs.operator === 'above') return value > first;
  if (inputs.operator === 'below') return value < first;
  return Math.min(first, second) <= value && value <= Math.max(first, second);
}

/**
 * Requires enough closed bars.
 * @param ctx Filter context.
 * @param needed Minimum bars.
 * @return The bars.
 * @throws DemoUnavailable With fewer bars.
 */
function closed(ctx: DemoFilterContext, needed: number): readonly DemoBar[] {
  if (ctx.bars.length < needed) {
    throw new DemoUnavailable(
      `needs ${needed} closed bars, got ${ctx.bars.length}; scan with history_bars of at least ${needed}`
    );
  }
  return ctx.bars;
}

/**
 * Reads a required snapshot number.
 * @param value Field value.
 * @param label Name for the reason.
 * @return The number.
 * @throws DemoUnavailable When absent.
 */
function required(value: unknown, label: string): number {
  const parsed = num(value);
  if (parsed === null) throw new DemoUnavailable(`${label} is unavailable`);
  return parsed;
}

/**
 * Latest trade, else the mark.
 * @param snapshot Snapshot.
 * @return Price.
 * @throws DemoUnavailable Without either.
 */
function latestPrice(snapshot: MarketSnapshot): number {
  return required(snapshot.last ?? snapshot.mark, 'latest price');
}

/**
 * Wilder's RSI at the last close (seeded with the simple mean).
 * @param closes Closes, at least `length + 1`.
 * @param length Smoothing length.
 * @return RSI 0..100 (50 for a flat series).
 */
export function demoRsi(closes: readonly number[], length: number): number {
  const changes = closes.slice(1).map((c, i) => c - (closes[i] ?? c));
  let gain = changes.slice(0, length).reduce((a, c) => a + Math.max(c, 0), 0);
  let loss = changes.slice(0, length).reduce((a, c) => a + Math.max(-c, 0), 0);
  gain /= length;
  loss /= length;
  for (const change of changes.slice(length)) {
    gain = (gain * (length - 1) + Math.max(change, 0)) / length;
    loss = (loss * (length - 1) + Math.max(-change, 0)) / length;
  }
  if (loss === 0) return gain > 0 ? 100 : 50;
  return 100 - 100 / (1 + gain / loss);
}

/** Threshold set of the liquidity qualification. */
interface Limits {
  turnover: number;
  openInterest: number | null;
  ratio: number | null;
  spread: number;
  slippage: number;
  notional: number;
  funding: boolean;
}

/**
 * Selects the liquidity thresholds of an instrument.
 * @param instrument Instrument.
 * @return Thresholds (crypto, equity perp, US or TW listed sets).
 */
function limitsFor(instrument: Instrument): Limits {
  const [venue = '', marketType = ''] = instrument.instrument_id.split(':');
  const listed =
    marketType === 'stock' ||
    marketType === 'etf' ||
    ['twse', 'tpex', 'nasdaq', 'nyse', 'cboe'].includes(venue);
  const bond = instrument.asset_class === 'BOND';
  if (listed && (venue === 'twse' || venue === 'tpex')) {
    return {
      turnover: bond ? 100e6 : 300e6,
      openInterest: null,
      ratio: null,
      spread: 0.6,
      slippage: 1,
      notional: 1.5e6,
      funding: false,
    };
  }
  if (listed) {
    return {
      turnover: bond ? 10e6 : 20e6,
      openInterest: null,
      ratio: null,
      spread: 0.1,
      slippage: 0.25,
      notional: 50e3,
      funding: false,
    };
  }
  return {
    turnover: instrument.asset_class === 'EQUITY' ? 30e6 : 50e6,
    openInterest: 10e6,
    ratio: 0.2,
    spread: 0.1,
    slippage: 0.25,
    notional: 50e3,
    funding: true,
  };
}

/**
 * Walks book levels for a quote notional.
 * @param levels `[price, quantity]` pairs, best first.
 * @param notional Quote notional.
 * @return Average fill price, or null when depth runs out.
 */
function walk(levels: ReadonlyArray<readonly string[]>, notional: number) {
  let remaining = notional;
  let filled = 0;
  let spent = 0;
  for (const [p, q] of levels) {
    const price = Number(p);
    const quantity = Number(q);
    if (!(price > 0 && quantity > 0)) continue;
    const take = Math.min(price * quantity, remaining);
    filled += take / price;
    spent += take;
    remaining -= take;
    if (remaining <= 0) return spent / filled;
  }
  return null;
}

/**
 * DEMO liquidity qualification: the engine's strict gates per asset class.
 * @param ctx Filter context with the order book.
 * @param inputs max_data_age_s and reject_high_funding_risk.
 * @return Whether the instrument qualifies.
 * @throws DemoUnavailable When a gate input is missing or stale.
 */
function liquidity(ctx: DemoFilterContext, inputs: Inputs): boolean {
  const {snapshot, instrument, book} = ctx;
  const [venue, marketType] = instrument.instrument_id.split(':');
  if (marketType === 'rate' || venue === 'treasury') return false;
  if (venue === 'binance' && instrument.key.quote !== 'USDT') return false;
  const limits = limitsFor(instrument);
  const maxAge = Number(inputs.max_data_age_s) * 1000;
  const fresh = (asOf: number | null | undefined) =>
    asOf === null || asOf === undefined || ctx.nowMs - asOf <= maxAge;
  const missing: string[] = [];
  let failed = false;
  const gate = (
    name: string,
    value: number | null,
    asOf: number | null | undefined,
    threshold: number,
    above: boolean
  ) => {
    if (value === null || !fresh(asOf)) {
      missing.push(`${name} is ${value === null ? 'unavailable' : 'stale'}`);
      return;
    }
    if (above ? !(value > threshold) : !(value < threshold)) failed = true;
  };
  const turnover = num(snapshot.turnover_24h_quote);
  gate('TURNOVER_24H', turnover, snapshot.as_of_ms, limits.turnover, true);
  const oi = num(snapshot.open_interest?.notional_usd);
  if (limits.openInterest !== null) {
    gate(
      'OPEN_INTEREST_USD',
      oi,
      snapshot.open_interest?.as_of_ms,
      limits.openInterest,
      true
    );
  }
  if (limits.ratio !== null) {
    gate(
      'TURNOVER_OI_RATIO',
      turnover !== null && oi !== null && oi > 0 ? turnover / oi : null,
      snapshot.as_of_ms,
      limits.ratio,
      true
    );
  }
  const bid = num(book?.bids[0]?.[0] ?? snapshot.best_bid);
  const ask = num(book?.asks[0]?.[0] ?? snapshot.best_ask);
  const mid = bid !== null && ask !== null ? (bid + ask) / 2 : null;
  gate(
    'SPREAD_PCT',
    mid && bid !== null && ask !== null ? ((ask - bid) / mid) * 100 : null,
    book?.received_at_ms ?? snapshot.as_of_ms,
    limits.spread,
    false
  );
  let slippage: number | null = null;
  if (book && !book.stale && mid) {
    const buy = walk(book.asks, limits.notional);
    const sell = walk(book.bids, limits.notional);
    if (buy !== null && sell !== null) {
      slippage = Math.max(((buy - mid) / mid) * 100, ((mid - sell) / mid) * 100);
    }
  }
  gate(
    'SLIPPAGE_50K_PCT',
    slippage,
    book?.received_at_ms,
    limits.slippage,
    false
  );
  if (limits.funding) {
    const rate = num(snapshot.funding?.rate);
    const hours = num(snapshot.funding?.interval_hours);
    const funding =
      rate !== null && hours !== null && hours > 0
        ? (rate * 100 * 8) / hours
        : null;
    if (funding === null || !fresh(snapshot.funding?.as_of_ms)) {
      missing.push('funding is unavailable; risk classification unknown');
    } else if (Math.abs(funding) >= 0.3 && inputs.reject_high_funding_risk) {
      failed = true;
    }
  }
  if (instrument.status !== 'TRADING') failed = true;
  if (failed) return false;
  if (missing.length) throw new DemoUnavailable(missing.join('; '));
  return true;
}

/**
 * Simple or exponential (first-close seeded) moving average.
 * @param closes Closes, at least `length`.
 * @param length Average length.
 * @param kind SMA or EMA.
 * @return The average at the last close.
 */
function average(closes: readonly number[], length: number, kind: unknown) {
  if (kind === 'SMA') {
    return closes.slice(-length).reduce((a, c) => a + c, 0) / length;
  }
  const alpha = 2 / (length + 1);
  return closes
    .slice(1)
    .reduce((value, close) => alpha * close + (1 - alpha) * value, closes[0]!);
}

/** DEMO filters by catalog id. */
const FILTERS: Readonly<
  Record<string, (ctx: DemoFilterContext, inputs: Inputs) => boolean>
> = {
  'builtin:liquidity-qualification': liquidity,
  'builtin:price': (ctx, inputs) =>
    compare(latestPrice(ctx.snapshot), inputs),
  'builtin:change': (ctx, inputs) =>
    compare(required(ctx.snapshot.change_24h_pct, '24h change'), inputs),
  'builtin:volume': (ctx, inputs) =>
    compare(required(ctx.snapshot.volume_24h_base, '24h volume'), inputs),
  'builtin:turnover': (ctx, inputs) =>
    compare(
      required(ctx.snapshot.turnover_24h_quote, '24h turnover'),
      inputs
    ),
  'builtin:relative-volume': (ctx, inputs) => {
    const length = Number(inputs.length);
    const bars = closed(ctx, length + 1);
    const avg =
      bars.slice(-length - 1, -1).reduce((a, b) => a + b.volume, 0) / length;
    if (avg <= 0) throw new DemoUnavailable('average volume is zero');
    return compare(bars[bars.length - 1]!.volume / avg, inputs);
  },
  'builtin:gap': (ctx, inputs) => {
    const bars = closed(ctx, 2);
    const previous = bars[bars.length - 2]!.close;
    if (previous <= 0) {
      throw new DemoUnavailable('previous close is not positive');
    }
    return compare(
      ((bars[bars.length - 1]!.open - previous) / previous) * 100,
      inputs
    );
  },
  'builtin:price-vs-ma': (ctx, inputs) => {
    const length = Number(inputs.length);
    const bars = closed(ctx, length);
    const price = latestPrice(ctx.snapshot);
    const level = average(
      bars.map(b => b.close),
      length,
      inputs.ma_type
    );
    return inputs.position === 'below' ? price < level : price > level;
  },
  'builtin:rsi': (ctx, inputs) => {
    const length = Number(inputs.length);
    const bars = closed(ctx, length + 1);
    return compare(
      demoRsi(
        bars.map(b => b.close),
        length
      ),
      inputs
    );
  },
  'builtin:new-high-low': (ctx, inputs) => {
    const length = Number(inputs.length);
    const window = closed(ctx, length).slice(-length);
    const last = window[window.length - 1]!;
    return inputs.extreme === 'low'
      ? last.low <= Math.min(...window.map(b => b.low))
      : last.high >= Math.max(...window.map(b => b.high));
  },
};

/**
 * Tells whether DEMO can evaluate a filter.
 * @param scriptId Catalog id.
 * @return True for the built-in filters.
 */
export function hasDemoFilter(scriptId: string): boolean {
  return scriptId in FILTERS;
}

/**
 * Evaluates one built-in filter in DEMO.
 * @param scriptId Catalog id of a built-in filter.
 * @param ctx Data of the candidate.
 * @param inputs Effective inputs.
 * @return The decision.
 * @throws DemoUnavailable When data is missing; `Error` for unknown ids.
 */
export function demoFilter(
  scriptId: string,
  ctx: DemoFilterContext,
  inputs: Inputs
): boolean {
  const run = FILTERS[scriptId];
  if (!run) throw new Error(`DEMO cannot run ${scriptId}`);
  return run(ctx, inputs);
}
