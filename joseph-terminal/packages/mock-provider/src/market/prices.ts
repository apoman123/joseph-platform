/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Deterministic synthetic price path for DEMO mode.
 *
 * Price is a pure function of absolute time, so every request, timeframe
 * and reload sees the same path without stored state:
 *
 *   p(t) = base * (1 + Σ_k A_k sin(2πt/P_k + φ_k) + 0.0006 n(t) + 0.00008 s(t))
 *
 * with t in epoch milliseconds, ten periods P_k from 20 days down to 14
 * minutes, amplitudes A_k = 0.045 * sqrt(P_k / 20 days) (a Brownian-like
 * spectrum), phases φ_k = k * 1.7 + (base mod 7), n(t) a hashed noise term
 * per UTC minute and s(t) a small, well-mixed noise term per second
 * (texture for seconds bars). No random seed or clock state is involved,
 * so the Python engine can reproduce it exactly for fixtures. Prices are
 * rounded to the instrument's tick (the ladder rung at the price) only when
 * written into a candle.
 *
 * Candles also carry the DEMO venue's quote turnover and taker-buy volume
 * (the aggressor-bought share follows the bar's direction plus noise), so
 * true volume delta and turnover ranking work offline. Sub-minute bars
 * scale wicks and volume to their length; monthly bars end on the 1st.
 *
 * Session instruments (DEMO stocks, the bond ETFs and the Treasury yield)
 * only have bars inside their calendar's sessions, built with the same
 * calendar arithmetic as the engine; stock and ETF bars carry a buy volume
 * classified against quotes (`taker_buy_method: classified`), as on stock
 * exchanges, and the yield carries no volume.
 */

import {
  CONTINUOUS_CALENDAR,
  Decimal,
  TIMEFRAME_MS,
  type AssetClass,
  type BarCalendar,
  type CalendarId,
  type Candle,
  type MarketType,
  type TickStep,
  type Timeframe,
} from '@joseph/contracts';

/** Synthetic instrument parameters. */
export interface DemoInstrumentSpec {
  /** Canonical instrument id, e.g. `mock:perp:BTCUSD`. */
  instrumentId: string;
  /** Display symbol. */
  displaySymbol: string;
  /** Base asset. */
  base: string;
  /** Centre of the synthetic price path, in quote units. */
  basePrice: number;
  /**
   * Tick size as a decimal string; the tick wherever no ladder rung
   * applies.
   */
  tickSize: string;
  /** Quantity step in base units, as a decimal string. */
  lotSize: string;
  /** Typical base volume per minute; 0 publishes no volume (a yield). */
  volumePerMinute: number;
  /** Product type (default `perp`). */
  marketType?: MarketType;
  /** Session calendar (default `24x7`). */
  calendar?: CalendarId;
  /** Asset class (default `CRYPTO`). */
  assetClass?: AssetClass;
  /** Quote currency or unit (default `USD`; `%` for yields). */
  quote?: string;
  /** English name. */
  name?: string;
  /** Name in the listing market's language. */
  localName?: string;
  /** Listing exchange of a security. */
  listingExchange?: string;
  /** Price-dependent tick ladder, rungs in ascending `min_price` order. */
  tickLadder?: readonly TickStep[];
  /** Daily price limit in percent, as a decimal string. */
  priceLimitPct?: string;
  /** Shares per board lot, as a decimal string. */
  boardLot?: string;
}

/** Taiwan stock price ladder (TWSE and TPEx stock tick sizes). */
export const TWSE_TICK_LADDER: readonly TickStep[] = [
  {min_price: '0', tick_size: '0.01'},
  {min_price: '10', tick_size: '0.05'},
  {min_price: '50', tick_size: '0.1'},
  {min_price: '100', tick_size: '0.5'},
  {min_price: '500', tick_size: '1'},
  {min_price: '1000', tick_size: '5'},
];

/** Taiwan ETF price ladder (TWSE and TPEx: 0.01 below 50, 0.05 from 50). */
export const TAIWAN_ETF_TICK_LADDER: readonly TickStep[] = [
  {min_price: '0', tick_size: '0.01'},
  {min_price: '50', tick_size: '0.05'},
];

/** DEMO instruments on the `mock` venue. */
export const DEMO_INSTRUMENT_SPECS: readonly DemoInstrumentSpec[] = [
  {
    instrumentId: 'mock:perp:BTCUSD',
    displaySymbol: 'BTCUSD',
    base: 'BTC',
    basePrice: 95_000,
    tickSize: '0.1',
    lotSize: '0.001',
    volumePerMinute: 42,
  },
  {
    instrumentId: 'mock:perp:ETHUSD',
    displaySymbol: 'ETHUSD',
    base: 'ETH',
    basePrice: 3_400,
    tickSize: '0.01',
    lotSize: '0.01',
    volumePerMinute: 610,
  },
  {
    instrumentId: 'mock:perp:SOLUSD',
    displaySymbol: 'SOLUSD',
    base: 'SOL',
    basePrice: 180,
    tickSize: '0.001',
    lotSize: '0.1',
    volumePerMinute: 9_800,
  },
  {
    instrumentId: 'mock:stock:ACME',
    displaySymbol: 'ACME',
    base: 'ACME',
    basePrice: 182.4,
    tickSize: '0.01',
    lotSize: '1',
    volumePerMinute: 1_500,
    marketType: 'stock',
    calendar: 'XNYS',
    assetClass: 'EQUITY',
    name: 'Acme Corporation (demo)',
    listingExchange: 'NYSE',
  },
  {
    instrumentId: 'mock:etf:TLTD',
    displaySymbol: 'TLTD',
    base: 'TLTD',
    basePrice: 91.2,
    tickSize: '0.01',
    lotSize: '1',
    volumePerMinute: 900,
    marketType: 'etf',
    calendar: 'XNYS',
    assetClass: 'BOND',
    name: 'Demo 20+ Year Treasury Bond ETF',
    listingExchange: 'Nasdaq',
  },
  {
    instrumentId: 'mock:stock:9901',
    displaySymbol: '9901',
    base: '9901',
    basePrice: 585,
    tickSize: '0.01',
    lotSize: '1',
    volumePerMinute: 24_000,
    marketType: 'stock',
    calendar: 'XTAI',
    assetClass: 'EQUITY',
    quote: 'TWD',
    name: 'Demo Semiconductor (demo)',
    localName: '示範半導體',
    listingExchange: 'TWSE',
    tickLadder: TWSE_TICK_LADDER,
    priceLimitPct: '10',
    boardLot: '1000',
  },
  {
    // A TPEx bond ETF holding foreign bonds: ETF ladder, no daily limit.
    instrumentId: 'mock:etf:00999B',
    displaySymbol: '00999B',
    base: '00999B',
    basePrice: 26.2,
    tickSize: '0.01',
    lotSize: '1',
    volumePerMinute: 120_000,
    marketType: 'etf',
    calendar: 'XTAI',
    assetClass: 'BOND',
    quote: 'TWD',
    name: 'Demo US Treasury 20+ Year Bond ETF (demo)',
    localName: '示範美債20年',
    listingExchange: 'TPEx',
    tickLadder: TAIWAN_ETF_TICK_LADDER,
    boardLot: '1000',
  },
  {
    instrumentId: 'mock:rate:UST10Y',
    displaySymbol: 'UST10Y',
    base: 'UST10Y',
    basePrice: 4.25,
    tickSize: '0.001',
    lotSize: '1',
    volumePerMinute: 0,
    marketType: 'rate',
    calendar: 'USBOND',
    assetClass: 'BOND',
    quote: '%',
    name: 'US 10-Year Treasury yield (demo)',
  },
];

/**
 * Returns the tick size at a price: that of the highest ladder rung at or
 * below it, else `tickSize`.
 * @param spec Instrument.
 * @param price Price in quote units.
 * @return Tick size as a decimal string.
 */
export function demoTickAt(spec: DemoInstrumentSpec, price: number): string {
  let tickSize = spec.tickSize;
  for (const step of spec.tickLadder ?? []) {
    if (price >= Number(step.min_price)) tickSize = step.tick_size;
  }
  return tickSize;
}

const DAY_MS = 86_400_000;
const HOUR_MS = 3_600_000;
/** Periods P_k of the sine components of the synthetic path. */
const PERIODS_MS: readonly number[] = [
  20 * DAY_MS,
  8.7 * DAY_MS,
  3.9 * DAY_MS,
  1.6 * DAY_MS,
  17.3 * HOUR_MS,
  7.1 * HOUR_MS,
  3.1 * HOUR_MS,
  77 * 60_000,
  33 * 60_000,
  14 * 60_000,
];
/** Amplitude of the longest component, as a fraction of the base price. */
const BASE_AMPLITUDE = 0.045;

/**
 * Hashes an integer to [-1, 1] deterministically (one xorshift32 round).
 * @param seed Integer seed; the bitwise operations truncate fractions.
 * @return Pseudo-random value in [-1, 1].
 */
export function hashNoise(seed: number): number {
  let x = (seed ^ 0x9e3779b9) >>> 0;
  x ^= x << 13;
  x >>>= 0;
  x ^= x >>> 17;
  x ^= x << 5;
  x >>>= 0;
  return (x / 0xffffffff) * 2 - 1;
}

/**
 * Spreads consecutive integers over the 32-bit range (multiplicative
 * hashing), so neighbouring seconds get unrelated noise.
 * @param seed Integer seed.
 * @return Mixed seed.
 */
export function mixSeed(seed: number): number {
  return Math.imul(Math.trunc(seed) | 0, 0x9e3779b1) ^ 0x5bd1e995;
}

/**
 * Returns the synthetic price at a time (the formula in the file overview).
 * @param spec Instrument.
 * @param timeMs Time in epoch milliseconds (UTC).
 * @return Price in quote units, not rounded to the tick.
 */
export function priceAt(spec: DemoInstrumentSpec, timeMs: number): number {
  const tau = 2 * Math.PI;
  const minute = Math.floor(timeMs / 60_000);
  const phase = spec.basePrice % 7;
  const longest = PERIODS_MS[0] ?? DAY_MS;
  let shape = 0;
  PERIODS_MS.forEach((period, k) => {
    const amplitude = BASE_AMPLITUDE * Math.sqrt(period / longest);
    shape += amplitude * Math.sin((tau * timeMs) / period + k * 1.7 + phase);
  });
  shape += 0.0006 * hashNoise(minute * 31 + Math.round(spec.basePrice));
  const second = Math.floor(timeMs / 1000);
  shape += 0.00008 * hashNoise(mixSeed(second + Math.round(spec.basePrice)));
  return spec.basePrice * (1 + shape);
}

/**
 * Rounds a price to the instrument's tick at that price.
 * @param spec Instrument.
 * @param value Price in quote units.
 * @return Rounded price as a decimal string.
 */
function tick(spec: DemoInstrumentSpec, value: number): string {
  return Decimal.fromNumber(value, 10)
    .roundToStep(Decimal.parse(demoTickAt(spec, value)))
    .toString();
}

/**
 * Builds one synthetic candle.
 * @param spec Instrument.
 * @param timeframe Timeframe.
 * @param startMs Bar start in epoch ms, on a bar boundary of `calendar`.
 * @param nowMs Current time in epoch ms. A bar ending after it is still
 *     forming: it closes at the price at `nowMs` and is not final.
 * @param calendar Session calendar that sets the bar's end (default
 *     continuous `24x7`).
 * @return Candle with DEMO provenance.
 */
export function demoCandle(
  spec: DemoInstrumentSpec,
  timeframe: Timeframe,
  startMs: number,
  nowMs: number,
  calendar: BarCalendar = CONTINUOUS_CALENDAR
): Candle {
  const duration = TIMEFRAME_MS[timeframe];
  const endMs = calendar.endOf(timeframe, startMs);
  const final = endMs <= nowMs;
  const closeAt = final ? endMs - 1 : Math.max(startMs, nowMs);
  // Open, close and the body extremes come from seven evenly spaced samples
  // of the path; the wicks add hashed noise beyond them.
  const samples = 6;
  const prices: number[] = [];
  for (let i = 0; i <= samples; i += 1) {
    prices.push(priceAt(spec, startMs + ((closeAt - startMs) * i) / samples));
  }
  const open = prices[0] ?? spec.basePrice;
  const close = prices[prices.length - 1] ?? open;
  // Sub-minute bars get proportionally shorter wicks (random-walk scaling).
  const wickScale = duration < 60_000 ? Math.sqrt(duration / 60_000) : 1;
  const wick =
    spec.basePrice *
    0.0009 *
    wickScale *
    (1 + Math.abs(hashNoise(startMs / 60_000 + 7)));
  const high =
    Math.max(...prices) + wick * Math.abs(hashNoise(startMs / 1000 + 3));
  const low =
    Math.min(...prices) - wick * Math.abs(hashNoise(startMs / 1000 + 5));
  // Volume scales with the elapsed part of the bar (at least one second for
  // sub-minute bars, one minute otherwise).
  const minutes =
    duration < 60_000
      ? Math.max(1 / 60, (closeAt - startMs + 1) / 60_000)
      : Math.max(1, (closeAt - startMs) / 60_000);
  const volume =
    spec.volumePerMinute *
    minutes *
    (0.6 +
      Math.abs(
        hashNoise(
          duration < 60_000
            ? mixSeed(Math.floor(startMs / 1000) + 11)
            : startMs / 60_000 + 11
        )
      ));
  const lot = Decimal.parse(spec.lotSize);
  const baseVolume = Decimal.fromNumber(volume, 4).floorToStep(lot);
  const range = Math.max(
    Math.max(high, open, close) - Math.min(low, open, close),
    1e-9
  );
  // The aggressor-bought share leans with the bar's body (close - open over
  // the range) plus noise, clamped to 4%..96%.
  const buyShare = Math.min(
    0.96,
    Math.max(
      0.04,
      0.5 +
        0.38 * ((close - open) / range) +
        0.12 * hashNoise(mixSeed(Math.floor(startMs / 1000) + 13))
    )
  );
  const takerBuy = Decimal.fromNumber(
    baseVolume.toNumber() * buyShare,
    4
  ).floorToStep(lot);
  const typical = prices.reduce((sum, p) => sum + p, 0) / prices.length;
  // A published yield has no volume; stock exchanges publish no aggressor
  // side, so their buy volume is classified against quotes (Lee-Ready).
  const noVolume = spec.volumePerMinute <= 0;
  const security = spec.marketType === 'stock' || spec.marketType === 'etf';
  return {
    instrument_id: spec.instrumentId,
    timeframe,
    start_ms: startMs,
    end_ms: endMs,
    open: tick(spec, open),
    high: tick(spec, Math.max(high, open, close)),
    low: tick(spec, Math.min(low, open, close)),
    close: tick(spec, close),
    base_volume: baseVolume.toString(),
    quote_turnover: noVolume
      ? null
      : Decimal.fromNumber(baseVolume.toNumber() * typical, 2).toString(),
    trade_count: null,
    taker_buy_base_volume: noVolume ? null : takerBuy.toString(),
    ...(security && !noVolume ? {taker_buy_method: 'classified' as const} : {}),
    is_final: final,
    revision: 0,
    source: 'mock',
    feed_state: 'DEMO',
    received_at_ms: nowMs,
    event_ts_ms: null,
  };
}

/**
 * Builds a candle series ending with the last bar that starts before `toMs`
 * (between sessions: the last bar before it).
 * @param spec Instrument.
 * @param timeframe Timeframe.
 * @param limit Number of bars; 0 or less gives an empty series.
 * @param nowMs Current time in epoch ms; the series never goes past it.
 * @param toMs Exclusive end of the requested range in epoch ms: a bar that
 *     starts at `toMs` is left out. When omitted, the series ends with the
 *     bar containing `nowMs`.
 * @param calendar Session calendar of the instrument (default `24x7`).
 * @return `limit` candles, oldest first; the newest is still forming when
 *     it contains `nowMs`.
 */
export function demoSeries(
  spec: DemoInstrumentSpec,
  timeframe: Timeframe,
  limit: number,
  nowMs: number,
  toMs?: number,
  calendar: BarCalendar = CONTINUOUS_CALENDAR
): Candle[] {
  // Times are whole milliseconds, so `toMs - 1` is the latest time before
  // the exclusive end.
  const end = toMs === undefined ? nowMs : Math.min(toMs - 1, nowMs);
  const last = calendar.floor(timeframe, end);
  const candles: Candle[] = [];
  let start = calendar.shift(timeframe, last, -(limit - 1));
  for (let i = 0; i < limit; i += 1) {
    candles.push(demoCandle(spec, timeframe, start, nowMs, calendar));
    start = calendar.shift(timeframe, start, 1);
  }
  return candles;
}
