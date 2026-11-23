/**
 * @fileoverview Shared test fixtures for chart-trading unit tests.
 */
import type {
  AccountSummary,
  Order,
  OrderPreview,
  Position,
} from '@joseph/contracts';
import type {InstrumentPrecision} from '../intent-builder';

/** BTC demo instrument precision. */
export const BTC: InstrumentPrecision = {
  instrument_id: 'mock:perp:BTCUSD',
  display_symbol: 'BTCUSD',
  tick_size: '0.1',
  lot_size: '0.001',
  min_qty: '0.001',
  max_qty: '100',
  min_notional: '5',
  max_leverage: '20',
  status: 'TRADING',
  key: {canonical_base: 'BTC', quote: 'USD'},
};

/**
 * Builds an order fixture.
 * @param patch Field overrides.
 * @return Order.
 */
export function order(patch: Partial<Order> = {}): Order {
  return {
    order_id: 'ord-1',
    intent_id: 'int-1',
    tenant_id: 'demo-tenant',
    account_id: 'acct-paper-demo',
    instrument_id: 'mock:perp:BTCUSD',
    client_order_id: 'cl-1',
    mode: 'PAPER',
    role: 'PRIMARY',
    side: 'buy',
    order_type: 'limit',
    time_in_force: 'GTC',
    quantity_base: '0.01',
    filled_base: '0',
    limit_price: '94000',
    reduce_only: false,
    state: 'ACKNOWLEDGED',
    created_at_ms: 1,
    updated_at_ms: 1,
    version: 1,
    ...patch,
  };
}

/**
 * Builds a position fixture.
 * @param patch Field overrides.
 * @return Position.
 */
export function position(patch: Partial<Position> = {}): Position {
  return {
    position_id: 'pos-1',
    account_id: 'acct-paper-demo',
    instrument_id: 'mock:perp:BTCUSD',
    side: 'long',
    quantity_base: '0.02',
    entry_price: '95000',
    mark_price: '95100',
    realized_pnl: '0',
    liquidation_price: '80000',
    mode: 'PAPER',
    stale: false,
    as_of_ms: 1,
    ...patch,
  };
}

/**
 * Builds an account summary fixture.
 * @param patch Field overrides.
 * @return Account summary.
 */
export function account(patch: Partial<AccountSummary> = {}): AccountSummary {
  return {
    account_id: 'acct-paper-demo',
    tenant_id: 'demo-tenant',
    venue: 'mock',
    label: 'Demo paper',
    status: 'CONNECTED',
    modes: ['PAPER', 'SHADOW'],
    equity: '100000',
    available_margin: '100000',
    currency: 'USD',
    positions: [],
    open_orders: [],
    capabilities: {
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
      notes: [],
    },
    health: 'HEALTHY',
    stale: false,
    last_sync_ms: 1,
    ...patch,
  };
}

/**
 * Builds a preview fixture for a request.
 * @param normalized Normalized request.
 * @param patch Field overrides.
 * @return Preview.
 */
export function preview(
  normalized: OrderPreview['normalized'],
  patch: Partial<OrderPreview> = {}
): OrderPreview {
  return {
    allowed: true,
    normalized,
    notional: '950',
    estimated_fee: '0.19',
    margin_required: '950',
    risk: {
      decision: 'ALLOW',
      checks: [],
      policy_version: 'demo-1',
      evaluated_at_ms: 1,
    },
    warnings: [],
    preview_token: 'tok-1',
    expires_at_ms: 10_000,
    protection: {native_stop_loss: true, native_take_profit: true, note: ''},
    ...patch,
  };
}

/** TWSE ladder used by the equity fixtures. */
const TWSE_LADDER = [
  {min_price: '0', tick_size: '0.01'},
  {min_price: '10', tick_size: '0.05'},
  {min_price: '50', tick_size: '0.1'},
  {min_price: '100', tick_size: '0.5'},
  {min_price: '500', tick_size: '1'},
  {min_price: '1000', tick_size: '5'},
];

/** TWSE stock (board lot 1000, ±10% limit, tick ladder). */
export const TWSE_STOCK: InstrumentPrecision = {
  instrument_id: 'mock:stock:9901',
  display_symbol: '9901',
  tick_size: '0.01',
  lot_size: '1',
  min_qty: '1',
  max_qty: '1000000',
  min_notional: null,
  max_leverage: null,
  status: 'TRADING',
  key: {canonical_base: '9901', quote: 'TWD'},
  calendar: 'XTAI',
  tick_ladder: TWSE_LADDER,
  price_limit_pct: '10',
  board_lot: '1000',
  listing_exchange: 'TWSE',
  name: 'Demo Semiconductor (demo)',
  local_name: '示範半導體',
};

/** US stock on the NYSE calendar. */
export const US_STOCK: InstrumentPrecision = {
  instrument_id: 'mock:stock:ACME',
  display_symbol: 'ACME',
  tick_size: '0.01',
  lot_size: '1',
  min_qty: '1',
  max_qty: '1000000',
  min_notional: null,
  max_leverage: null,
  status: 'TRADING',
  key: {canonical_base: 'ACME', quote: 'USD'},
  calendar: 'XNYS',
  tick_ladder: [],
  price_limit_pct: null,
  board_lot: null,
  listing_exchange: 'NYSE',
  name: 'Acme Corporation (demo)',
};

/**
 * Builds a broker account summary with the engine's broker capabilities.
 * @param venue Broker.
 * @param patch Field overrides.
 * @param caps Capability overrides.
 * @return Account summary.
 */
export function brokerAccount(
  venue: 'ibkr' | 'sinopac' | 'cathay',
  patch: Partial<AccountSummary> = {},
  caps: Partial<AccountSummary['capabilities']> = {}
): AccountSummary {
  const base = account({
    account_id: `acct-${venue}`,
    venue,
    label: `${venue} account`,
    currency: venue === 'ibkr' ? 'USD' : 'TWD',
  });
  const ibkr = venue === 'ibkr';
  return {
    ...base,
    capabilities: {
      ...base.capabilities,
      venue,
      order_types: ibkr
        ? ['market', 'limit', 'stop_market', 'stop_limit']
        : ['market', 'limit'],
      time_in_force: ibkr ? ['DAY', 'GTC', 'IOC'] : ['DAY', 'IOC', 'FOK'],
      margin_modes: [],
      tradable_venues: ibkr ? ['nasdaq', 'nyse'] : ['twse'],
      cash_account: true,
      short_selling: false,
      outside_rth: ibkr,
      odd_lot: venue === 'sinopac',
      queue_market_when_closed: ibkr,
      simulation: true,
      currencies: [ibkr ? 'USD' : 'TWD'],
      ...caps,
    },
    ...patch,
  };
}

/**
 * Builds an engine paper account: `acct-paper-*` (USD: crypto, Nasdaq,
 * NYSE) or `acct-twd-paper-*` (TWD: TWSE).
 * @param currency Account currency.
 * @return Account summary.
 */
export function paperAccount(currency: 'USD' | 'TWD'): AccountSummary {
  const base = account({
    account_id: currency === 'USD' ? 'acct-paper-demo' : 'acct-twd-paper-demo',
    label: `Paper ${currency}`,
    currency,
  });
  return {
    ...base,
    capabilities: {
      ...base.capabilities,
      order_types: ['market', 'limit', 'stop_market', 'stop_limit'],
      time_in_force: ['GTC', 'IOC', 'FOK', 'POST_ONLY', 'DAY'],
      tradable_venues: currency === 'USD' ? ['nasdaq', 'nyse'] : ['twse'],
      cash_account: true,
      short_selling: false,
      outside_rth: false,
      odd_lot: true,
      queue_market_when_closed: true,
      simulation: true,
      currencies: [currency],
    },
  };
}
