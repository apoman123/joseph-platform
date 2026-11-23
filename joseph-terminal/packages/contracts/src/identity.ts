/**
 * @fileoverview Instrument identity and timeframe helpers mirroring the
 * Python domain kernel (`joseph.domain.instruments` / `timeframes`).
 */
import type {
  InstrumentId,
  MarketType,
  Timeframe,
  Venue,
} from './generated/contracts';

/** Canonical instrument id pattern `{venue}:{market_type}:{venue_market_id}`. */
const INSTRUMENT_ID_PATTERN =
  /^(binance|hyperliquid|lighter|bitget|mexc|twse|nasdaq|nyse|treasury|mock):(perp|spot|future|stock|etf|rate):([A-Za-z0-9._/-]{1,64})$/;

/** Parsed parts of a canonical instrument id. */
export interface InstrumentIdParts {
  /** Venue that lists the instrument. */
  venue: Venue;
  /** Product type. */
  marketType: MarketType;
  /** Venue-native market id, e.g. `BTCUSDT`. */
  venueMarketId: string;
}

/**
 * Parses a canonical instrument id.
 * @param id Text such as `binance:perp:BTCUSDT`.
 * @return The id parts.
 * @throws {RangeError} If the id is not canonical.
 */
export function parseInstrumentId(id: string): InstrumentIdParts {
  const match = INSTRUMENT_ID_PATTERN.exec(id);
  if (!match) throw new RangeError(`Invalid instrument id: ${id}`);
  return {
    venue: match[1] as Venue,
    marketType: match[2] as MarketType,
    venueMarketId: match[3] as string,
  };
}

/**
 * Formats instrument id parts into the canonical string.
 * @param parts Venue, market type and venue market id.
 * @return Canonical id.
 */
export function formatInstrumentId(parts: InstrumentIdParts): InstrumentId {
  return `${parts.venue}:${parts.marketType}:${parts.venueMarketId}`;
}

/**
 * Checks whether a string is a canonical instrument id.
 * @param id Candidate text.
 * @return True when valid.
 */
export function isInstrumentId(id: string): boolean {
  return INSTRUMENT_ID_PATTERN.test(id);
}

/** All supported venues in display order. */
export const VENUES: readonly Venue[] = [
  'binance',
  'hyperliquid',
  'lighter',
  'bitget',
  'mexc',
  'twse',
  'nasdaq',
  'nyse',
  'treasury',
  'mock',
];

/** Human-readable venue names. */
export const VENUE_NAMES: Readonly<Record<Venue, string>> = {
  binance: 'Binance USDⓈ-M',
  hyperliquid: 'Hyperliquid',
  lighter: 'Lighter',
  bitget: 'Bitget Futures',
  mexc: 'MEXC Futures',
  twse: 'Taiwan Stock Exchange',
  nasdaq: 'Nasdaq',
  nyse: 'NYSE',
  treasury: 'US Treasury yields',
  mock: 'Demo venue',
};

const SECOND_MS = 1_000;
const MINUTE_MS = 60 * SECOND_MS;
const HOUR_MS = 60 * MINUTE_MS;
const DAY_MS = 24 * HOUR_MS;

/**
 * Bar durations in milliseconds, mirroring the engine's `duration_ms`.
 *
 * Exact for fixed-length timeframes. `1M` (calendar month) is the longest
 * possible month (31 days): use it to order timeframes or size look-back
 * windows, never to step between bar boundaries; use `floorToTimeframe`,
 * `timeframeEnd`, `shiftTimeframe` and `barsBetween` for that.
 */
export const TIMEFRAME_MS: Readonly<Record<Timeframe, number>> = {
  '1s': SECOND_MS,
  '5s': 5 * SECOND_MS,
  '15s': 15 * SECOND_MS,
  '30s': 30 * SECOND_MS,
  '1m': MINUTE_MS,
  '3m': 3 * MINUTE_MS,
  '5m': 5 * MINUTE_MS,
  '15m': 15 * MINUTE_MS,
  '30m': 30 * MINUTE_MS,
  '1h': HOUR_MS,
  '2h': 2 * HOUR_MS,
  '4h': 4 * HOUR_MS,
  '6h': 6 * HOUR_MS,
  '12h': 12 * HOUR_MS,
  '1d': DAY_MS,
  '1w': 7 * DAY_MS,
  '1M': 31 * DAY_MS,
};

/** Supported timeframes, shortest first. */
export const TIMEFRAMES: readonly Timeframe[] = Object.keys(
  TIMEFRAME_MS
) as Timeframe[];

/** Monday 1970-01-05 00:00 UTC; anchors weekly bars. */
const WEEK_ANCHOR_MS = 4 * DAY_MS;

/**
 * Whether bar length varies with the calendar (`1M`). Case matters: `1m`
 * is one minute.
 * @param timeframe Bar resolution.
 * @return True for calendar months.
 */
export function isCalendarTimeframe(timeframe: Timeframe): boolean {
  return timeframe === '1M';
}

/**
 * Whether the timeframe is sub-minute (the engine builds these bars from
 * the venue's public trades).
 * @param timeframe Bar resolution.
 * @return True for `1s` … `30s`.
 */
export function isSecondsTimeframe(timeframe: Timeframe): boolean {
  return TIMEFRAME_MS[timeframe] < MINUTE_MS;
}

/**
 * Floor modulo (the sign follows the divisor, like Python's `%`).
 * @param value Dividend.
 * @param divisor Positive divisor.
 * @return Remainder in `[0, divisor)`.
 */
function floorMod(value: number, divisor: number): number {
  return ((value % divisor) + divisor) % divisor;
}

/**
 * Months since January 1970 of the UTC month containing a time.
 * @param tsMs UTC epoch milliseconds.
 * @return Zero-based month index (negative before 1970).
 */
function monthIndex(tsMs: number): number {
  const date = new Date(tsMs);
  return (date.getUTCFullYear() - 1970) * 12 + date.getUTCMonth();
}

/**
 * First instant of a month index (the 1st at 00:00 UTC).
 * @param index Months since January 1970.
 * @return UTC epoch milliseconds.
 */
function monthStart(index: number): number {
  // Date.UTC normalizes month overflow, including negative months.
  return Date.UTC(1970, index, 1);
}

/**
 * Returns the start of the UTC-aligned bar containing `tsMs` (engine
 * `Timeframe.floor`). Weeks start on Monday; months on the 1st.
 * @param timeframe Bar resolution.
 * @param tsMs UTC epoch milliseconds.
 * @return Bar start in UTC epoch milliseconds.
 */
export function floorToTimeframe(timeframe: Timeframe, tsMs: number): number {
  if (timeframe === '1M') return monthStart(monthIndex(tsMs));
  const duration = TIMEFRAME_MS[timeframe];
  if (timeframe === '1w') {
    return tsMs - floorMod(tsMs - WEEK_ANCHOR_MS, duration);
  }
  return tsMs - floorMod(tsMs, duration);
}

/**
 * Moves an aligned bar start by whole bars (engine `Timeframe.shift`).
 * Calendar months step to the 1st of the target month.
 * @param timeframe Bar resolution.
 * @param startMs Aligned bar start (a bar end is also a start).
 * @param bars Bars to move; negative moves back in time.
 * @return Start of the bar `bars` bars away.
 */
export function shiftTimeframe(
  timeframe: Timeframe,
  startMs: number,
  bars: number
): number {
  if (timeframe === '1M') return monthStart(monthIndex(startMs) + bars);
  return startMs + bars * TIMEFRAME_MS[timeframe];
}

/**
 * Exclusive end of the bar starting at `startMs` (engine `end_of`): 28 to
 * 31 days later for `1M`.
 * @param timeframe Bar resolution.
 * @param startMs Aligned bar start.
 * @return Exclusive bar end in UTC epoch milliseconds.
 */
export function timeframeEnd(timeframe: Timeframe, startMs: number): number {
  return shiftTimeframe(timeframe, startMs, 1);
}

/**
 * Counts bar starts in `[startMs, endMs)` (engine `bars_between`).
 * @param timeframe Bar resolution.
 * @param startMs Aligned bar start.
 * @param endMs Exclusive end (any time).
 * @return Number of bars starting in the interval (0 when empty).
 */
export function barsBetween(
  timeframe: Timeframe,
  startMs: number,
  endMs: number
): number {
  if (endMs <= startMs) return 0;
  if (timeframe === '1M') {
    return monthIndex(endMs - 1) - monthIndex(startMs) + 1;
  }
  const duration = TIMEFRAME_MS[timeframe];
  return Math.ceil((endMs - startMs) / duration);
}
