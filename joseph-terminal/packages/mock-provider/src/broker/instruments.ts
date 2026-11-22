/**
 * @fileoverview DEMO instruments and static reference marks used by the
 * in-browser paper broker. Every value here is synthetic.
 */
import type {Instrument} from '@joseph/contracts';

/**
 * Builds a DEMO perpetual instrument.
 * @param base Base asset.
 * @param tick Tick size.
 * @param lot Lot size.
 * @return Contract instrument on the `mock` venue.
 */
function demoPerp(base: string, tick: string, lot: string): Instrument {
  return {
    instrument_id: `mock:perp:${base}USD`,
    key: {
      venue: 'mock',
      market_type: 'perp',
      venue_market_id: `${base}USD`,
      canonical_base: base,
      quote: 'USD',
      settlement: 'USD',
      instrument_version: 1,
    },
    display_symbol: `${base}USD`,
    tick_size: tick,
    lot_size: lot,
    min_qty: lot,
    max_qty: '1000000',
    min_notional: '5',
    contract_multiplier: '1',
    linear: true,
    status: 'TRADING',
    funding_interval_hours: 8,
    max_leverage: '20',
    asset_class: 'CRYPTO',
    source: 'mock',
    as_of_ms: 0,
  };
}

/** DEMO instruments supported by the paper broker. */
export const DEMO_BROKER_INSTRUMENTS: readonly Instrument[] = [
  demoPerp('BTC', '0.1', '0.001'),
  demoPerp('ETH', '0.01', '0.01'),
  demoPerp('SOL', '0.001', '0.1'),
];

/** Static DEMO reference marks. */
export const DEMO_MARKS: Readonly<Record<string, string>> = {
  'mock:perp:BTCUSD': '95000',
  'mock:perp:ETHUSD': '3400',
  'mock:perp:SOLUSD': '180',
};

/** Account id of the DEMO paper account. */
export const DEMO_ACCOUNT_ID = 'acct-paper-demo';

/** Tenant id used by DEMO services. */
export const DEMO_TENANT_ID = 'demo-tenant';

/** User id used by DEMO services. */
export const DEMO_USER_ID = 'demo-user';
