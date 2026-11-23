/**
 * @fileoverview DEMO instruments, static reference marks and accounts used
 * by the in-browser paper broker. Every value here is synthetic.
 *
 * Besides the crypto paper account the DEMO broker simulates broker
 * accounts for exchange-listed securities: an Interactive Brokers paper
 * account (Nasdaq/NYSE), a SinoPac Shioaji simulation account (TWSE), a
 * SinoPac live account whose credentials are missing on the engine, and a
 * Cathay account awaiting the broker's API SDK. None of them offers LIVE.
 *
 * Forward compatibility: the IBKR and SinoPac capabilities list `DAY` time
 * in force and IBKR's `outside_rth` flag, which the execution schema does
 * not carry yet (the brokers engineer is adding them). They are attached
 * through `withExtensions`, so the DEMO broker behaves like an engine that
 * already accepts them.
 */
import type {
  AccountStatus,
  HealthState,
  Instrument,
  OrderMode,
  TradingCapabilities,
  TradingVenue,
} from '@joseph/contracts';
import {demoInstrument} from '../market/demo-market-client';
import {DEMO_INSTRUMENT_SPECS} from '../market/prices';

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

/** DEMO stocks and ETFs (same metadata as the DEMO market registry). */
const DEMO_SECURITIES: readonly Instrument[] = DEMO_INSTRUMENT_SPECS.filter(
  spec => spec.marketType === 'stock' || spec.marketType === 'etf'
).map(spec => ({...demoInstrument(spec, 0), max_qty: '1000000'}));

/** DEMO instruments supported by the paper broker. */
export const DEMO_BROKER_INSTRUMENTS: readonly Instrument[] = [
  demoPerp('BTC', '0.1', '0.001'),
  demoPerp('ETH', '0.01', '0.01'),
  demoPerp('SOL', '0.001', '0.1'),
  ...DEMO_SECURITIES,
];

/** Static DEMO reference marks. */
export const DEMO_MARKS: Readonly<Record<string, string>> = {
  'mock:perp:BTCUSD': '95000',
  'mock:perp:ETHUSD': '3400',
  'mock:perp:SOLUSD': '180',
  'mock:stock:ACME': '182.40',
  'mock:etf:TLTD': '91.20',
  'mock:stock:9901': '585',
};

/** Account id of the DEMO paper account. */
export const DEMO_ACCOUNT_ID = 'acct-paper-demo';

/** DEMO Interactive Brokers paper account (Nasdaq/NYSE). */
export const DEMO_IBKR_ACCOUNT_ID = 'acct-ibkr-demo';

/** DEMO SinoPac Shioaji simulation account (TWSE). */
export const DEMO_SINOPAC_ACCOUNT_ID = 'acct-sinopac-demo';

/** DEMO SinoPac live account without credentials on the engine. */
export const DEMO_SINOPAC_LIVE_ACCOUNT_ID = 'acct-sinopac-live-demo';

/** DEMO Cathay account awaiting the broker's API SDK. */
export const DEMO_CATHAY_ACCOUNT_ID = 'acct-cathay-demo';

/** Tenant id used by DEMO services. */
export const DEMO_TENANT_ID = 'demo-tenant';

/** User id used by DEMO services. */
export const DEMO_USER_ID = 'demo-user';

/** Static description of one DEMO account. */
export interface DemoAccountSpec {
  /** Account id. */
  accountId: string;
  /** Venue or broker. */
  venue: TradingVenue;
  /** Label. */
  label: string;
  /** Connection status. */
  status: AccountStatus;
  /** Permitted modes (never LIVE in DEMO). */
  modes: readonly OrderMode[];
  /** Account currency. */
  currency: string;
  /** Starting paper equity in the account currency. */
  startingEquity: string;
  /** Largest order value in the account currency. */
  maxNotional: string;
  /** Trading capabilities. */
  capabilities: TradingCapabilities;
  /** Adapter health. */
  health: HealthState;
}

/**
 * Attaches capability fields the execution schema does not carry yet.
 * @param capabilities Contract capabilities.
 * @param extra Forward-compatible fields (`outside_rth`, ...).
 * @return Capabilities with the extra fields.
 */
export function withExtensions(
  capabilities: TradingCapabilities,
  extra: Readonly<Record<string, unknown>>
): TradingCapabilities {
  return {...capabilities, ...extra} as TradingCapabilities;
}

/**
 * Time-in-force list including `DAY` (not in the generated enum yet).
 * @param values Values.
 * @return Contract-typed list.
 */
function timeInForce(
  ...values: readonly string[]
): TradingCapabilities['time_in_force'] {
  return [...values] as TradingCapabilities['time_in_force'];
}

/** DEMO trading capabilities of the crypto paper account. */
export const DEMO_CAPABILITIES: TradingCapabilities = {
  venue: 'mock',
  status: 'AVAILABLE',
  order_types: ['market', 'limit', 'stop_market'],
  time_in_force: ['GTC', 'IOC', 'POST_ONLY'],
  native_reduce_only: true,
  native_stop_loss: true,
  native_take_profit: true,
  native_oco: false,
  amend: true,
  batch: false,
  position_modes: ['ONE_WAY'],
  margin_modes: ['isolated', 'cross'],
  client_order_id: true,
  query_by_client_id: true,
  notes: [
    'DEMO paper simulation in your browser. Nothing is sent to any venue.',
  ],
};

/**
 * Capabilities of a DEMO broker account for cash equities.
 * @param venue Broker.
 * @param status Trading status.
 * @param notes Notes.
 * @return Capabilities.
 */
function brokerCapabilities(
  venue: TradingVenue,
  status: TradingCapabilities['status'],
  notes: readonly string[]
): TradingCapabilities {
  return {
    venue,
    status,
    order_types:
      venue === 'ibkr'
        ? ['market', 'limit', 'stop_market']
        : ['market', 'limit'],
    time_in_force:
      venue === 'ibkr' ? timeInForce('DAY', 'GTC') : timeInForce('DAY'),
    native_reduce_only: false,
    native_stop_loss: venue === 'ibkr',
    native_take_profit: venue === 'ibkr',
    native_oco: false,
    amend: true,
    batch: false,
    position_modes: ['ONE_WAY'],
    margin_modes: [],
    client_order_id: true,
    query_by_client_id: venue === 'ibkr',
    notes: [...notes],
  };
}

/** DEMO accounts, crypto paper account first. */
export const DEMO_ACCOUNTS: readonly DemoAccountSpec[] = [
  {
    accountId: DEMO_ACCOUNT_ID,
    venue: 'mock',
    label: 'Demo paper account',
    status: 'CONNECTED',
    modes: ['PAPER', 'SHADOW'],
    currency: 'USD',
    startingEquity: '100000',
    maxNotional: '1000000',
    capabilities: DEMO_CAPABILITIES,
    health: 'HEALTHY',
  },
  {
    accountId: DEMO_IBKR_ACCOUNT_ID,
    venue: 'ibkr',
    label: 'IBKR paper (demo)',
    status: 'CONNECTED',
    modes: ['PAPER', 'SHADOW'],
    currency: 'USD',
    startingEquity: '100000',
    maxNotional: '1000000',
    capabilities: withExtensions(
      brokerCapabilities('ibkr', 'AVAILABLE', [
        'DEMO simulation of an Interactive Brokers paper account (Nasdaq and NYSE). Nothing is sent to IBKR.',
      ]),
      {outside_rth: true}
    ),
    health: 'HEALTHY',
  },
  {
    accountId: DEMO_SINOPAC_ACCOUNT_ID,
    venue: 'sinopac',
    label: 'SinoPac Shioaji simulation (demo)',
    status: 'CONNECTED',
    modes: ['PAPER', 'SHADOW'],
    currency: 'TWD',
    startingEquity: '5000000',
    maxNotional: '30000000',
    capabilities: brokerCapabilities('sinopac', 'AVAILABLE', [
      'DEMO simulation of a SinoPac Shioaji simulation account (TWSE board and odd lots). Nothing is sent to SinoPac.',
    ]),
    health: 'HEALTHY',
  },
  {
    accountId: DEMO_SINOPAC_LIVE_ACCOUNT_ID,
    venue: 'sinopac',
    label: 'SinoPac Shioaji live (demo)',
    status: 'DISCONNECTED',
    modes: [],
    currency: 'TWD',
    startingEquity: '0',
    maxNotional: '0',
    capabilities: brokerCapabilities('sinopac', 'TRADING_UNAVAILABLE', [
      'API key and certificate are not configured on the execution engine, so this account cannot connect.',
    ]),
    health: 'UNAVAILABLE',
  },
  {
    accountId: DEMO_CATHAY_ACCOUNT_ID,
    venue: 'cathay',
    label: 'Cathay Securities (demo)',
    status: 'TRADING_UNAVAILABLE',
    modes: [],
    currency: 'TWD',
    startingEquity: '0',
    maxNotional: '0',
    capabilities: brokerCapabilities('cathay', 'TRADING_UNAVAILABLE', [
      'Awaiting the broker API SDK: Cathay has not released a trading API, so orders cannot be placed yet.',
    ]),
    health: 'UNAVAILABLE',
  },
];

/**
 * Finds a DEMO account.
 * @param accountId Account id.
 * @return Spec or null.
 */
export function demoAccount(accountId: string): DemoAccountSpec | null {
  return DEMO_ACCOUNTS.find(a => a.accountId === accountId) ?? null;
}

/**
 * Why a DEMO account cannot trade an instrument (the execution engine's
 * venue routing): IBKR trades XNYS listings, TWSE brokers XTAI listings,
 * the crypto paper account the DEMO perpetuals.
 * @param account Account.
 * @param instrument Instrument.
 * @return Reason, or null when routable.
 */
export function demoRoutingProblem(
  account: DemoAccountSpec,
  instrument: Instrument
): string | null {
  const type = instrument.key.market_type;
  const equity = type === 'stock' || type === 'etf';
  const calendar = instrument.calendar ?? '24x7';
  if (account.venue === 'ibkr')
    return equity && calendar === 'XNYS'
      ? null
      : 'Interactive Brokers accounts trade Nasdaq and NYSE listings.';
  if (account.venue === 'sinopac' || account.venue === 'cathay')
    return equity && calendar === 'XTAI'
      ? null
      : 'TWSE broker accounts trade TWSE listings.';
  return equity ? 'Stocks and ETFs trade through a broker account.' : null;
}
