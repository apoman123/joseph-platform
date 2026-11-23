/**
 * @fileoverview DEMO instruments, static reference marks and accounts used
 * by the in-browser paper broker. Every value here is synthetic.
 *
 * The accounts mirror the execution engine: a USD paper account (crypto,
 * Nasdaq and NYSE) and a TWD paper account (TWSE), an Interactive Brokers
 * paper account (Nasdaq/NYSE, outside RTH, queued market orders), a
 * SinoPac Shioaji simulation account (TWSE board and intraday odd lots, no
 * stop orders), a SinoPac live account whose credentials are missing on
 * the server and Cathay awaiting its API SDK. Capabilities carry the
 * contract's `tradable_venues`, `cash_account`, `short_selling`,
 * `outside_rth`, `odd_lot`, `queue_market_when_closed`, `simulation` and
 * `currencies`. None of the default accounts offers LIVE; the opt-in live
 * rehearsal account (`DEMO_LIVE_REHEARSAL_ACCOUNT`) does, so arming can be
 * rehearsed without a backend.
 */
import type {
  AccountStatus,
  HealthState,
  Instrument,
  OrderMode,
  TradingCapabilities,
  TradingVenue,
  Venue,
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

/** Account id of the DEMO USD paper account (crypto, Nasdaq, NYSE). */
export const DEMO_ACCOUNT_ID = 'acct-paper-demo';

/** DEMO TWD paper account (TWSE), like the engine's `acct-twd-paper-*`. */
export const DEMO_TWD_ACCOUNT_ID = 'acct-twd-paper-demo';

/** DEMO Interactive Brokers paper account (Nasdaq/NYSE). */
export const DEMO_IBKR_ACCOUNT_ID = 'acct-ibkr-demo';

/** DEMO SinoPac Shioaji simulation account (TWSE). */
export const DEMO_SINOPAC_ACCOUNT_ID = 'acct-sinopac-demo';

/** DEMO SinoPac live account whose credentials are missing on the server. */
export const DEMO_SINOPAC_LIVE_ACCOUNT_ID = 'acct-sinopac-live-demo';

/** DEMO Cathay account awaiting the broker's API SDK. */
export const DEMO_CATHAY_ACCOUNT_ID = 'acct-cathay-demo';

/** Opt-in DEMO account that rehearses LIVE arming (IBKR, simulated). */
export const DEMO_LIVE_REHEARSAL_ACCOUNT_ID = 'acct-ibkr-live-rehearsal-demo';

/**
 * Step-up code the DEMO broker accepts when arming LIVE (the execution
 * engine verifies a TOTP code instead). Any other code is refused with
 * `STEP_UP_REQUIRED`.
 */
export const DEMO_STEP_UP_CODE = '123456';

/** Longest DEMO arming (the engine's default `JOSEPH_LIVE_ARM_MAX_MS`). */
export const DEMO_LIVE_ARM_MAX_MS = 8 * 60 * 60_000;

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
  /** Permitted modes (LIVE only on the opt-in rehearsal account). */
  modes: readonly OrderMode[];
  /** Account currency. */
  currency: string;
  /** Starting paper cash in the account currency. */
  startingEquity: string;
  /** Largest order value in the account currency. */
  maxNotional: string;
  /** Trading capabilities. */
  capabilities: TradingCapabilities;
  /** Adapter health. */
  health: HealthState;
}

/**
 * Capabilities of a DEMO paper account (engine paper adapter): every order
 * type, crypto on the USD account and long-only cash securities of the
 * account currency's exchanges.
 * @param currency Account currency.
 * @param tradable Exchanges of that currency.
 * @return Capabilities.
 */
function paperCapabilities(
  currency: string,
  tradable: readonly Venue[]
): TradingCapabilities {
  return {
    venue: 'mock',
    status: 'AVAILABLE',
    order_types: ['market', 'limit', 'stop_market', 'stop_limit'],
    time_in_force: ['GTC', 'IOC', 'FOK', 'POST_ONLY', 'DAY'],
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
    tradable_venues: [...tradable],
    cash_account: true,
    short_selling: false,
    outside_rth: false,
    odd_lot: true,
    queue_market_when_closed: true,
    simulation: true,
    currencies: [currency],
  };
}

/** DEMO trading capabilities of the USD paper account. */
export const DEMO_CAPABILITIES: TradingCapabilities = paperCapabilities('USD', [
  'nasdaq',
  'nyse',
]);

/** Interactive Brokers (engine `capabilities.ibkr`, paper, cash account). */
const IBKR_CAPABILITIES: TradingCapabilities = {
  venue: 'ibkr',
  status: 'AVAILABLE',
  order_types: ['market', 'limit', 'stop_market', 'stop_limit'],
  time_in_force: ['DAY', 'GTC', 'IOC'],
  native_reduce_only: false,
  native_stop_loss: true,
  native_take_profit: true,
  native_oco: false,
  amend: true,
  batch: false,
  position_modes: ['ONE_WAY'],
  margin_modes: [],
  client_order_id: true,
  query_by_client_id: true,
  notes: [
    'DEMO simulation of an Interactive Brokers paper account (Nasdaq and NYSE). Nothing is sent to IBKR.',
    'Outside-RTH applies to limit and stop-limit orders; IBKR queues DAY market orders placed while closed for the open.',
  ],
  tradable_venues: ['nasdaq', 'nyse'],
  cash_account: true,
  short_selling: false,
  outside_rth: true,
  odd_lot: false,
  queue_market_when_closed: true,
  simulation: true,
  currencies: ['USD'],
};

/**
 * SinoPac Shioaji (engine `capabilities.sinopac`).
 * @param status Trading status.
 * @param notes Notes.
 * @return Capabilities.
 */
function sinopacCapabilities(
  status: TradingCapabilities['status'],
  notes: readonly string[]
): TradingCapabilities {
  return {
    venue: 'sinopac',
    status,
    order_types: ['market', 'limit'],
    time_in_force: ['DAY', 'IOC', 'FOK'],
    native_reduce_only: false,
    native_stop_loss: false,
    native_take_profit: false,
    native_oco: false,
    amend: true,
    batch: false,
    position_modes: ['ONE_WAY'],
    margin_modes: [],
    client_order_id: false,
    query_by_client_id: true,
    notes: [...notes],
    tradable_venues: ['twse'],
    cash_account: true,
    short_selling: false,
    outside_rth: false,
    odd_lot: true,
    queue_market_when_closed: false,
    simulation: status === 'AVAILABLE',
    currencies: ['TWD'],
  };
}

/** Cathay Securities (engine `capabilities.cathay`, fail-closed). */
const CATHAY_CAPABILITIES: TradingCapabilities = {
  venue: 'cathay',
  status: 'TRADING_UNAVAILABLE',
  order_types: [],
  time_in_force: [],
  native_reduce_only: false,
  native_stop_loss: false,
  native_take_profit: false,
  native_oco: false,
  amend: false,
  batch: false,
  position_modes: ['ONE_WAY'],
  margin_modes: [],
  client_order_id: false,
  query_by_client_id: false,
  notes: [
    'Awaiting the Cathay API SDK: Cathay has not released a trading API, so orders cannot be placed yet.',
  ],
  tradable_venues: ['twse'],
  cash_account: true,
  short_selling: false,
  outside_rth: false,
  odd_lot: false,
  queue_market_when_closed: false,
  simulation: false,
  currencies: ['TWD'],
};

/** DEMO accounts, the USD paper account first. */
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
    accountId: DEMO_TWD_ACCOUNT_ID,
    venue: 'mock',
    label: 'Demo paper account (TWD)',
    status: 'CONNECTED',
    modes: ['PAPER', 'SHADOW'],
    currency: 'TWD',
    startingEquity: '3000000',
    maxNotional: '30000000',
    capabilities: paperCapabilities('TWD', ['twse']),
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
    capabilities: IBKR_CAPABILITIES,
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
    capabilities: sinopacCapabilities('AVAILABLE', [
      'DEMO simulation of a SinoPac Shioaji simulation account: board lots (any order type) and intraday odd lots (limit DAY). No stop orders. Nothing is sent to SinoPac.',
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
    capabilities: sinopacCapabilities('TRADING_UNAVAILABLE', [
      'The Shioaji API key and CA certificate are not configured on the execution server, so this account cannot connect. The operator adds them as server secrets.',
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
    capabilities: CATHAY_CAPABILITIES,
    health: 'UNAVAILABLE',
  },
];

/**
 * Opt-in DEMO account that rehearses a verified Interactive Brokers live
 * account (`DemoExecutionClient` option `liveRehearsal`). It offers LIVE so
 * the server-side arming flow (step-up code, armed-until, disarm,
 * `LIVE_NOT_ARMED`) can be tried; armed LIVE orders are simulated in the
 * browser like paper orders and nothing is sent to IBKR.
 */
export const DEMO_LIVE_REHEARSAL_ACCOUNT: DemoAccountSpec = {
  accountId: DEMO_LIVE_REHEARSAL_ACCOUNT_ID,
  venue: 'ibkr',
  label: 'IBKR live rehearsal (demo)',
  status: 'CONNECTED',
  modes: ['LIVE'],
  currency: 'USD',
  startingEquity: '100000',
  maxNotional: '1000000',
  capabilities: {
    ...IBKR_CAPABILITIES,
    notes: [
      'DEMO rehearsal of a verified Interactive Brokers live account: arming and LIVE orders are simulated in your browser. Nothing is sent to IBKR and no real money moves.',
      ...IBKR_CAPABILITIES.notes.slice(1),
    ],
    simulation: false,
  },
  health: 'HEALTHY',
};

/**
 * Finds a DEMO account.
 * @param accountId Account id.
 * @return Spec or null.
 */
export function demoAccount(accountId: string): DemoAccountSpec | null {
  return DEMO_ACCOUNTS.find(a => a.accountId === accountId) ?? null;
}

/** Exchange venues of listed securities. */
const EXCHANGES: readonly Venue[] = ['twse', 'nasdaq', 'nyse'];

/**
 * Exchanges a security trades on: its venue, or for DEMO securities the
 * exchanges of its session calendar.
 * @param instrument Instrument.
 * @return Exchange venues.
 */
function listingExchanges(instrument: Instrument): readonly Venue[] {
  const venue = instrument.key.venue;
  if (EXCHANGES.includes(venue)) return [venue];
  if (instrument.calendar === 'XTAI') return ['twse'];
  if (instrument.calendar === 'XNYS') return ['nasdaq', 'nyse'];
  return [];
}

/**
 * Why a DEMO account cannot trade an instrument (engine `equities.route`
 * and the `tradable_venues` check): securities go to accounts listing their
 * exchange; brokers trade securities only; the TWD paper account trades
 * TWSE only.
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
  const served = account.capabilities.tradable_venues ?? [];
  if (equity) {
    return listingExchanges(instrument).some(v => served.includes(v))
      ? null
      : `${account.label} does not route ${listingExchanges(instrument).join('/') || instrument.instrument_id} listings.`;
  }
  if (account.venue !== 'mock')
    return `${account.label} trades exchange-listed securities only.`;
  return account.currency === 'USD'
    ? null
    : `The ${account.currency} paper account trades ${account.currency} securities only.`;
}
