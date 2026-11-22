/**
 * @fileoverview Deterministic synthetic price path for DEMO mode.
 *
 * Price is a pure function of absolute time, so every request, timeframe
 * and reload sees the same path without stored state:
 *
 *   p(t) = base * (1 + Σ_k A_k sin(2πt/P_k + φ_k) + 0.0006 n(t))
 *
 * over ten periods P_k from 20 days down to 14 minutes with amplitudes
 * A_k = 0.045 * sqrt(P_k / 20 days) (a brownian-like spectrum), phases
 * φ_k = k * 1.7 + (base mod 7), and n(t) a hashed noise term per minute.
 * The Python engine can reproduce it exactly for fixtures.
 */
import {
  Decimal,
  TIMEFRAME_MS,
  floorToTimeframe,
  type Candle,
  type Timeframe,
} from '@joseph/contracts';

/** Synthetic instrument parameters. */
export interface DemoInstrumentSpec {
  /** Canonical id. */
  instrumentId: string;
  /** Display symbol. */
  displaySymbol: string;
  /** Base asset. */
  base: string;
  /** Base price. */
  basePrice: number;
  /** Tick size. */
  tickSize: string;
  /** Lot size. */
  lotSize: string;
  /** Typical volume per minute. */
  volumePerMinute: number;
}

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
];

const DAY_MS = 86_400_000;
const HOUR_MS = 3_600_000;
/** Component periods of the synthetic path. */
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
/** Amplitude of the longest component. */
const BASE_AMPLITUDE = 0.045;

/**
 * Hashes an integer to [-1, 1) deterministically (xorshift-style).
 * @param seed Integer seed.
 * @return Pseudo-random value.
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
 * Synthetic price at a time.
 * @param spec Instrument.
 * @param timeMs UTC ms.
 * @return Price.
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
  return spec.basePrice * (1 + shape);
}

/**
 * Rounds to the instrument tick as a decimal string.
 * @param spec Instrument.
 * @param value Price.
 * @return Decimal string.
 */
function tick(spec: DemoInstrumentSpec, value: number): string {
  return Decimal.fromNumber(value, 10)
    .roundToStep(Decimal.parse(spec.tickSize))
    .toString();
}

/**
 * Builds one synthetic candle.
 * @param spec Instrument.
 * @param timeframe Timeframe.
 * @param startMs Bar start.
 * @param nowMs Current time (bars ending after it are not final).
 * @return Candle with DEMO provenance.
 */
export function demoCandle(
  spec: DemoInstrumentSpec,
  timeframe: Timeframe,
  startMs: number,
  nowMs: number
): Candle {
  const duration = TIMEFRAME_MS[timeframe];
  const endMs = startMs + duration;
  const final = endMs <= nowMs;
  const closeAt = final ? endMs - 1 : Math.max(startMs, nowMs);
  const samples = 6;
  const prices: number[] = [];
  for (let i = 0; i <= samples; i += 1) {
    prices.push(priceAt(spec, startMs + ((closeAt - startMs) * i) / samples));
  }
  const open = prices[0] ?? spec.basePrice;
  const close = prices[prices.length - 1] ?? open;
  const wick =
    spec.basePrice * 0.0009 * (1 + Math.abs(hashNoise(startMs / 60_000 + 7)));
  const high =
    Math.max(...prices) + wick * Math.abs(hashNoise(startMs / 1000 + 3));
  const low =
    Math.min(...prices) - wick * Math.abs(hashNoise(startMs / 1000 + 5));
  const minutes = Math.max(1, (closeAt - startMs) / 60_000);
  const volume =
    spec.volumePerMinute *
    minutes *
    (0.6 + Math.abs(hashNoise(startMs / 60_000 + 11)));
  return {
    instrument_id: spec.instrumentId,
    timeframe,
    start_ms: startMs,
    end_ms: endMs,
    open: tick(spec, open),
    high: tick(spec, Math.max(high, open, close)),
    low: tick(spec, Math.min(low, open, close)),
    close: tick(spec, close),
    base_volume: Decimal.fromNumber(volume, 4)
      .floorToStep(Decimal.parse(spec.lotSize))
      .toString(),
    quote_turnover: null,
    trade_count: null,
    is_final: final,
    revision: 0,
    source: 'mock',
    feed_state: 'DEMO',
    received_at_ms: nowMs,
    event_ts_ms: null,
  };
}

/**
 * Builds a candle series ending with the bar containing `toMs`.
 * @param spec Instrument.
 * @param timeframe Timeframe.
 * @param limit Number of bars.
 * @param nowMs Current time.
 * @param toMs Exclusive end of the requested range (defaults to now).
 * @return Candles, oldest first.
 */
export function demoSeries(
  spec: DemoInstrumentSpec,
  timeframe: Timeframe,
  limit: number,
  nowMs: number,
  toMs = nowMs
): Candle[] {
  const duration = TIMEFRAME_MS[timeframe];
  const last = floorToTimeframe(timeframe, Math.min(toMs, nowMs));
  const candles: Candle[] = [];
  for (let i = limit - 1; i >= 0; i -= 1) {
    candles.push(demoCandle(spec, timeframe, last - i * duration, nowMs));
  }
  return candles;
}
