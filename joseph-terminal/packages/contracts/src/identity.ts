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
  /^(binance|hyperliquid|lighter|bitget|mexc|mock):(perp|spot|future):([A-Za-z0-9._/-]{1,64})$/;

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
  'mock',
];

/** Human-readable venue names. */
export const VENUE_NAMES: Readonly<Record<Venue, string>> = {
  binance: 'Binance USDⓈ-M',
  hyperliquid: 'Hyperliquid',
  lighter: 'Lighter',
  bitget: 'Bitget Futures',
  mexc: 'MEXC Futures',
  mock: 'Demo venue',
};

/** Bar durations in milliseconds. */
export const TIMEFRAME_MS: Readonly<Record<Timeframe, number>> = {
  '1m': 60_000,
  '3m': 180_000,
  '5m': 300_000,
  '15m': 900_000,
  '30m': 1_800_000,
  '1h': 3_600_000,
  '2h': 7_200_000,
  '4h': 14_400_000,
  '6h': 21_600_000,
  '12h': 43_200_000,
  '1d': 86_400_000,
  '1w': 604_800_000,
};

/** Supported timeframes, shortest first. */
export const TIMEFRAMES: readonly Timeframe[] = Object.keys(
  TIMEFRAME_MS
) as Timeframe[];

/** Monday 1970-01-05 00:00 UTC; anchors weekly bars. */
const WEEK_ANCHOR_MS = 4 * 86_400_000;

/**
 * Returns the start of the UTC-aligned bar containing `tsMs`.
 * @param timeframe Bar resolution.
 * @param tsMs UTC epoch milliseconds.
 * @return Bar start in UTC epoch milliseconds.
 */
export function floorToTimeframe(timeframe: Timeframe, tsMs: number): number {
  const duration = TIMEFRAME_MS[timeframe];
  if (timeframe === '1w') {
    return (
      tsMs - ((((tsMs - WEEK_ANCHOR_MS) % duration) + duration) % duration)
    );
  }
  return tsMs - (tsMs % duration);
}
