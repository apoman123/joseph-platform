/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Market groups, search ranking and data-access flags for the
 * instrument picker and chart headers.
 *
 * Groups follow what an instrument is, not where it is listed: Treasury
 * yields and bond ETFs (asset class `BOND`) are "Bonds & rates" even when
 * an ETF trades on Nasdaq; other securities go by their session calendar
 * (XTAI → Taiwan stocks: TWSE and TPEx; XNYS → US stocks); everything else
 * is crypto.
 */

import {
  instrumentCalendarId,
  parseInstrumentId,
  type Instrument,
  type InstrumentId,
  type MarketType,
  type VenueCapabilities,
} from '@joseph/contracts';

/** Asset-class group of the picker. */
export type MarketGroup = 'crypto' | 'tw' | 'us' | 'bonds';

/** Picker tabs in display order (`all` first). */
export const MARKET_GROUPS: ReadonlyArray<{
  id: MarketGroup | 'all';
  label: string;
}> = [
  {id: 'all', label: 'All'},
  {id: 'crypto', label: 'Crypto'},
  {id: 'tw', label: 'Taiwan stocks'},
  {id: 'us', label: 'US stocks'},
  {id: 'bonds', label: 'Bonds & rates'},
];

/** Group labels by id. */
export const MARKET_GROUP_LABELS: Readonly<Record<MarketGroup, string>> = {
  crypto: 'Crypto',
  tw: 'Taiwan stocks',
  us: 'US stocks',
  bonds: 'Bonds & rates',
};

/** Venues listed per group (the engine may serve only some of them). */
export const GROUP_VENUES: Readonly<Record<MarketGroup, readonly string[]>> = {
  crypto: ['binance', 'hyperliquid', 'lighter', 'bitget', 'mexc'],
  tw: ['twse', 'tpex'],
  us: ['nasdaq', 'nyse', 'cboe'],
  bonds: ['treasury', 'nasdaq', 'nyse', 'cboe', 'twse', 'tpex'],
};

/**
 * Group of an instrument.
 * @param instrument Registry entry.
 * @return Market group.
 */
export function marketGroupOf(
  instrument: Pick<Instrument, 'instrument_id' | 'asset_class' | 'calendar'>
): MarketGroup {
  if (instrument.asset_class === 'BOND') return 'bonds';
  const calendar = instrumentCalendarId(instrument.instrument_id, instrument);
  if (calendar === 'XTAI') return 'tw';
  if (calendar === 'XNYS') return 'us';
  if (calendar === 'USBOND') return 'bonds';
  return 'crypto';
}

/**
 * Product type of an instrument id.
 * @param instrumentId Canonical id.
 * @return Market type, or null for invalid ids.
 */
export function marketTypeOf(instrumentId: InstrumentId): MarketType | null {
  try {
    return parseInstrumentId(instrumentId).marketType;
  } catch {
    return null;
  }
}

/**
 * Whether an instrument is a published rate (a yield, not tradable).
 * @param instrumentId Canonical id.
 * @return True for market type `rate`.
 */
export function isRateInstrument(instrumentId: InstrumentId): boolean {
  return marketTypeOf(instrumentId) === 'rate';
}

/**
 * Whether an instrument is a cash security (stock or ETF).
 * @param instrumentId Canonical id.
 * @return True for stocks and ETFs.
 */
export function isSecurity(instrumentId: InstrumentId): boolean {
  const type = marketTypeOf(instrumentId);
  return type === 'stock' || type === 'etf';
}

/** Short market-type labels for result rows. */
const TYPE_LABELS: Readonly<Record<MarketType, string>> = {
  perp: 'Perpetual',
  spot: 'Spot',
  future: 'Future',
  stock: 'Stock',
  etf: 'ETF',
  rate: 'Yield',
};

/**
 * Label of a market type.
 * @param type Market type.
 * @return Label such as `ETF`.
 */
export function marketTypeLabel(type: MarketType): string {
  return TYPE_LABELS[type];
}

/**
 * Ranks an instrument against search text: exact symbol, symbol prefix,
 * symbol or base contains, then the English or local (e.g. Chinese) name.
 * @param instrument Registry entry.
 * @param query Search text.
 * @return Rank (lower is better), or null when it does not match.
 */
export function searchRank(
  instrument: Pick<
    Instrument,
    'display_symbol' | 'key' | 'name' | 'local_name' | 'instrument_id'
  >,
  query: string
): number | null {
  const q = query.trim().toUpperCase();
  if (!q) return 3;
  const symbol = instrument.display_symbol.toUpperCase();
  const native = instrument.key.venue_market_id.toUpperCase();
  const base = instrument.key.canonical_base.toUpperCase();
  if (symbol === q || native === q || base === q) return 0;
  if (symbol.startsWith(q) || native.startsWith(q) || base.startsWith(q)) {
    return 1;
  }
  if (symbol.includes(q) || native.includes(q) || base.includes(q)) return 2;
  const names = [instrument.name ?? '', instrument.local_name ?? ''].map(n =>
    n.toUpperCase()
  );
  if (names.some(name => name.startsWith(q))) return 4;
  if (names.some(name => name.includes(q))) return 5;
  return null;
}

/** Data-access flags of a venue shown on rows and headers. */
export interface VenueAccess {
  /** Delay label such as `Delayed 15 min`, or null when real time. */
  delay: string | null;
  /** Message when the data source needs a key that is not configured. */
  missingCredentials: string | null;
}

/**
 * Formats a data delay.
 * @param ms Delay in milliseconds.
 * @return Text such as `Delayed 15 min`.
 */
export function delayLabel(ms: number): string {
  if (ms >= 3_600_000) return `Delayed ${Math.round(ms / 3_600_000)} h`;
  if (ms >= 60_000) return `Delayed ${Math.round(ms / 60_000)} min`;
  return `Delayed ${Math.max(1, Math.round(ms / 1000))} s`;
}

/**
 * Data-access flags from a venue's capabilities.
 * @param capabilities Venue capabilities (null while unknown).
 * @return Flags (none while unknown).
 */
export function venueAccess(
  capabilities: VenueCapabilities | null | undefined
): VenueAccess {
  if (!capabilities) return {delay: null, missingCredentials: null};
  const delayMs = capabilities.data_delay_ms ?? null;
  const missing =
    capabilities.requires_credentials === true &&
    capabilities.credentials_configured === false;
  const provider =
    capabilities.venue === 'nasdaq' ||
    capabilities.venue === 'nyse' ||
    capabilities.venue === 'cboe'
      ? 'Alpaca'
      : capabilities.display_name;
  return {
    delay: delayMs !== null && delayMs > 0 ? delayLabel(delayMs) : null,
    missingCredentials: missing
      ? `${provider} API key missing: ${capabilities.display_name} data is unavailable until the operator configures it.`
      : null,
  };
}
