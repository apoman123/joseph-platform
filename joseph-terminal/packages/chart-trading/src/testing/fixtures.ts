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
