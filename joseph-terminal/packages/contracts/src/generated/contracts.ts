/**
 * @fileoverview GENERATED FILE - DO NOT EDIT.
 * Source: packages/contracts/schemas/*.schema.json
 * Regenerate with `pnpm contracts:gen`.
 */
/* eslint-disable */

/**
 * Exact decimal number encoded as a string. Never an IEEE-754 float.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "DecimalString".
 */
export type DecimalString = string;
/**
 * Exact non-negative decimal number encoded as a string.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "NonNegativeDecimalString".
 */
export type NonNegativeDecimalString = string;
/**
 * UTC epoch milliseconds.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "TimestampMs".
 */
export type TimestampMs = number;
/**
 * Execution venue identifier. `mock` is the synthetic DEMO venue and is never tradable live.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "Venue".
 */
export type Venue =
  'binance' | 'hyperliquid' | 'lighter' | 'bitget' | 'mexc' | 'mock';
/**
 * Product type at the venue.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "MarketType".
 */
export type MarketType = 'perp' | 'spot' | 'future';
/**
 * Canonical venue-scoped instrument id `{venue}:{market_type}:{venue_market_id}`, e.g. `binance:perp:BTCUSDT` or `hyperliquid:perp:BTC`. The same base asset on two venues is two instruments.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "InstrumentId".
 */
export type InstrumentId = string;
/**
 * Candle resolution. Boundaries are UTC-aligned half-open intervals [start_ms, end_ms). Case matters: 1m is one minute, 1M one calendar month (starts on the 1st 00:00 UTC, 28-31 days). Seconds bars are built by the engine from the venue's public trades.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "Timeframe".
 */
export type Timeframe =
  | '1s'
  | '5s'
  | '15s'
  | '30s'
  | '1m'
  | '3m'
  | '5m'
  | '15m'
  | '30m'
  | '1h'
  | '2h'
  | '4h'
  | '6h'
  | '12h'
  | '1d'
  | '1w'
  | '1M';
/**
 * Requested market-data transport policy.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "FeedMode".
 */
export type FeedMode =
  'AUTO' | 'NATIVE_WS' | 'REST_POLL' | 'EXTERNAL_VENDOR' | 'REPLAY';
/**
 * Observed provenance of data shown to users. DEMO is synthetic and must never be presented as live.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "FeedState".
 */
export type FeedState =
  'LIVE' | 'POLL' | 'REPLAY' | 'STALE' | 'DEMO' | 'UNAVAILABLE' | 'CONNECTING';
/**
 * Health of a feed, adapter or account connection.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "HealthState".
 */
export type HealthState = 'HEALTHY' | 'DEGRADED' | 'STALE' | 'UNAVAILABLE';
/**
 * Strategy trade direction.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "Direction".
 */
export type Direction = 'LONG' | 'SHORT';
/**
 * Directional bias of a structure or area of interest.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "ZoneSide".
 */
export type ZoneSide = 'bullish' | 'bearish' | 'neutral';
/**
 * Strategy activation mode. Saving a rule never implies AUTO_LIVE.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "ExecutionMode".
 */
export type ExecutionMode = 'ALERT_ONLY' | 'PAPER' | 'SHADOW' | 'AUTO_LIVE';
/**
 * Mode of an individual order intent. LIVE is disabled by default and fails closed.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "OrderMode".
 */
export type OrderMode = 'PAPER' | 'SHADOW' | 'LIVE';
/**
 * Actionable machine-readable error code shown to users.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "ErrorCode".
 */
export type ErrorCode =
  | 'MARKET_STALE'
  | 'MISSING_DATA'
  | 'ORDER_UNKNOWN'
  | 'INSUFFICIENT_MARGIN'
  | 'RISK_LIMIT'
  | 'VENUE_UNAVAILABLE'
  | 'AUTH_SCOPE_MISSING'
  | 'UNAUTHENTICATED'
  | 'FORBIDDEN'
  | 'STEP_UP_REQUIRED'
  | 'INVALID_TICK_SIZE'
  | 'INVALID_LOT_SIZE'
  | 'MIN_NOTIONAL'
  | 'TRADE_MODE_DISABLED'
  | 'LIVE_TRADING_DISABLED'
  | 'KILL_SWITCH_ENGAGED'
  | 'DUPLICATE_INTENT'
  | 'IDEMPOTENCY_CONFLICT'
  | 'PREVIEW_REQUIRED'
  | 'PREVIEW_EXPIRED'
  | 'POSITION_UNKNOWN'
  | 'UNSUPPORTED'
  | 'UNSUPPORTED_ORDER_TYPE'
  | 'VALIDATION_FAILED'
  | 'NOT_FOUND'
  | 'CONFLICT'
  | 'RATE_LIMITED'
  | 'INTERNAL';
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "OrderSide".
 */
export type OrderSide = 'buy' | 'sell';
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "PositionEffect".
 */
export type PositionEffect = 'open' | 'close' | 'reduce';
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "OrderType".
 */
export type OrderType = 'market' | 'limit' | 'stop_market' | 'stop_limit';
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "TimeInForce".
 */
export type TimeInForce = 'GTC' | 'IOC' | 'FOK' | 'POST_ONLY';
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "MarginMode".
 */
export type MarginMode = 'isolated' | 'cross';
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "PriceBasis".
 */
export type PriceBasis = 'mark' | 'last';
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "IntentSource".
 */
export type IntentSource =
  'MANUAL_TICKET' | 'CHART' | 'QUICK_TRADE' | 'STRATEGY' | 'EMERGENCY';
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "RiskDomain".
 */
export type RiskDomain =
  | 'MODE'
  | 'IDENTITY'
  | 'ACCOUNT'
  | 'MARKET_STATUS'
  | 'DATA_QUALITY'
  | 'SIZE'
  | 'EXPOSURE'
  | 'LIQUIDITY'
  | 'DECISION_QUALITY'
  | 'DUPLICATE'
  | 'CONNECTIVITY'
  | 'KILL_SWITCH'
  | 'PROTECTION';
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "OrderState".
 */
export type OrderState =
  | 'DRAFT'
  | 'VALIDATED'
  | 'RESERVED'
  | 'SUBMITTING'
  | 'ACKNOWLEDGED'
  | 'PARTIALLY_FILLED'
  | 'FILLED'
  | 'REJECTED'
  | 'CANCEL_PENDING'
  | 'CANCELED'
  | 'EXPIRED'
  | 'FAILED'
  | 'UNKNOWN_RECONCILING';
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "OrderRole".
 */
export type OrderRole = 'PRIMARY' | 'STOP_LOSS' | 'TAKE_PROFIT' | 'CLOSE';
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "TradingStatus".
 */
export type TradingStatus = 'AVAILABLE' | 'READ_ONLY' | 'TRADING_UNAVAILABLE';
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "AccountStatus".
 */
export type AccountStatus =
  | 'CONNECTED'
  | 'PENDING_VERIFICATION'
  | 'READ_ONLY'
  | 'TRADING_UNAVAILABLE'
  | 'DISCONNECTED';
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "KillSwitchScope".
 */
export type KillSwitchScope = 'GLOBAL' | 'ACCOUNT' | 'STRATEGY';
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "AoiKind".
 */
export type AoiKind =
  | 'FVG'
  | 'IFVG'
  | 'OB'
  | 'BREAKER'
  | 'BSL'
  | 'SSL'
  | 'EQH'
  | 'EQL'
  | 'PREMIUM'
  | 'DISCOUNT'
  | 'EQUILIBRIUM'
  | 'OTE'
  | 'VAH'
  | 'POC'
  | 'VAL'
  | 'PDH'
  | 'PDL'
  | 'SESSION_HIGH'
  | 'SESSION_LOW'
  | 'SWING_HIGH'
  | 'SWING_LOW';
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "AoiState".
 */
export type AoiState =
  'active' | 'tested' | 'inverted' | 'swept' | 'invalidated' | 'expired';
/**
 * `pine_parity` reproduces the reference Pine semantics; `trade` uses venue trades and is never presented as parity.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "IndicatorMethod".
 */
export type IndicatorMethod = 'pine_parity' | 'trade';
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "StructureEventName".
 */
export type StructureEventName =
  | 'BOS_BULL'
  | 'BOS_BEAR'
  | 'CHOCH_BULL'
  | 'CHOCH_BEAR'
  | 'INITIAL_BREAK_BULL'
  | 'INITIAL_BREAK_BEAR'
  | 'FVG_FORMED'
  | 'IFVG_FORMED'
  | 'OB_FORMED'
  | 'BREAKER_FORMED'
  | 'AOI_INVALIDATED'
  | 'AOI_EXPIRED'
  | 'SSL_SWEEP'
  | 'BSL_SWEEP'
  | 'SSL_BREAK'
  | 'BSL_BREAK'
  | 'BUYER_FAILURE'
  | 'SELLER_FAILURE'
  | 'BUYER_BREAKOUT'
  | 'SELLER_BREAKOUT'
  | 'RANGE_RESET'
  | 'VALUE_AREA_UPDATED'
  | 'SWING_HH'
  | 'SWING_HL'
  | 'SWING_LH'
  | 'SWING_LL'
  | 'SWING_EQH'
  | 'SWING_EQL';
/**
 * Scalar indicator output. Decimals are strings.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "IndicatorValue".
 */
export type IndicatorValue = string | number | boolean | null;
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "LicensingStatus".
 */
export type LicensingStatus =
  'CLEAN_ROOM_FROM_SPEC' | 'PINE_PORT_REVIEWED' | 'PINE_PORT_PENDING_REVIEW';
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "ParityStatus".
 */
export type ParityStatus = 'SPEC_ONLY' | 'GOLDEN_VERIFIED';
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "InstrumentStatus".
 */
export type InstrumentStatus =
  'TRADING' | 'HALTED' | 'PRE_LAUNCH' | 'DELISTED' | 'UNKNOWN';
/**
 * Asset class used to select qualification thresholds.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "AssetClass".
 */
export type AssetClass = 'CRYPTO' | 'RWA' | 'EQUITY' | 'UNKNOWN';
/**
 * [price, base_quantity] pair.
 *
 * @minItems 2
 * @maxItems 2
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "BookLevel".
 */
export type BookLevel = [string, string];
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "FundingKind".
 */
export type FundingKind = 'PREDICTED' | 'SETTLED';
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "Transport".
 */
export type Transport = 'NATIVE_WS' | 'REST_POLL' | 'REPLAY' | 'MOCK' | 'NONE';
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "QualificationStatus".
 */
export type QualificationStatus =
  'QUALIFIED' | 'REJECTED' | 'INSUFFICIENT_DATA';
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "RiskTag".
 */
export type RiskTag = 'WATCH' | 'HIGH_RISK';
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "FundingRisk".
 */
export type FundingRisk = 'NORMAL' | 'WATCH' | 'HIGH_RISK' | 'UNKNOWN';
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "GateName".
 */
export type GateName =
  | 'TURNOVER_24H'
  | 'OPEN_INTEREST_USD'
  | 'TURNOVER_OI_RATIO'
  | 'SPREAD_PCT'
  | 'SLIPPAGE_50K_PCT';
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "TriState".
 */
export type TriState = 'TRUE' | 'FALSE' | 'UNKNOWN';
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "SetupState".
 */
export type SetupState =
  | 'WAITING'
  | 'AOI_TOUCHED'
  | 'CONFIRMING'
  | 'READY'
  | 'FIRED'
  | 'EXPIRED'
  | 'INVALIDATED'
  | 'CANCELLED'
  | 'BLOCKED';
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "Severity".
 */
export type Severity = 'INFO' | 'WARNING' | 'ENTRY' | 'CRITICAL';
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "SignalKind".
 */
export type SignalKind =
  | 'WATCHING_AOI'
  | 'PRE_SIGNAL'
  | 'ENTRY_CONFIRMED'
  | 'ENTRY_BLOCKED'
  | 'AOI_INVALIDATED'
  | 'SETUP_EXPIRED'
  | 'QUALIFICATION_CHANGED'
  | 'POSITION_EXITED';
/**
 * Discriminated union on `op`.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "RuleNode".
 */
export type RuleNode =
  | AllNode
  | AnyNode
  | NotNode
  | BiasNode
  | PriceInZoneNode
  | AoiTouchNode
  | AoiOverlapNode
  | EventNode
  | SequenceNode
  | CompareNode
  | InRangeNode
  | CrossesNode
  | PersistForNode
  | WithinBarsNode
  | SessionNode
  | RiskRewardNode;
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "Operand".
 */
export type Operand =
  ConstOperand | CandleOperand | IndicatorOperand | MarketOperand;
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "StrategyState".
 */
export type StrategyState =
  'DRAFT' | 'VALIDATED' | 'ACTIVE' | 'PAUSED' | 'ARCHIVED' | 'UNSUPPORTED';
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "StreamEventType".
 */
export type StreamEventType =
  | 'candle.updated'
  | 'aoi.updated'
  | 'aoi.removed'
  | 'structure.event'
  | 'feed.health'
  | 'qualification.changed'
  | 'signal.created'
  | 'signal.updated'
  | 'order.updated'
  | 'fill.created'
  | 'position.updated'
  | 'account.updated'
  | 'risk.kill_switch'
  | 'subscribed'
  | 'snapshot_required'
  | 'heartbeat'
  | 'error';
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "ChannelName".
 */
export type ChannelName =
  | 'candles'
  | 'aois'
  | 'feed_health'
  | 'qualification'
  | 'signals'
  | 'orders'
  | 'fills'
  | 'positions'
  | 'account'
  | 'risk';
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "ChartType".
 */
export type ChartType = 'candles' | 'bars' | 'line' | 'area' | 'heikin_ashi';
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "PriceScaleMode".
 */
export type PriceScaleMode = 'linear' | 'log' | 'percent';
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "LayoutId".
 */
export type LayoutId = '1' | '2h' | '2v' | '4' | '8';

/**
 * Validation failure for one request field.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "FieldError".
 */
export interface FieldError {
  field: string;
  message: string;
}
/**
 * Uniform error body for every REST endpoint.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "ErrorResponse".
 */
export interface ErrorResponse {
  code: ErrorCode;
  message: string;
  trace_id: string;
  retryable: boolean;
  field_errors: FieldError[];
  source_status?: string | null;
}
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "StopLossSpec".
 */
export interface StopLossSpec {
  trigger: DecimalString;
  basis: PriceBasis;
}
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "TakeProfitSpec".
 */
export interface TakeProfitSpec {
  trigger: DecimalString;
  fraction: NonNegativeDecimalString;
}
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "Bracket".
 */
export interface Bracket {
  stop_loss?: StopLossSpec | null;
  /**
   * @maxItems 4
   */
  take_profit?:
    | []
    | [TakeProfitSpec]
    | [TakeProfitSpec, TakeProfitSpec]
    | [TakeProfitSpec, TakeProfitSpec, TakeProfitSpec]
    | [TakeProfitSpec, TakeProfitSpec, TakeProfitSpec, TakeProfitSpec];
}
/**
 * Client-submitted draft. The server assigns intent_id, tenant, user and client_order_id and re-runs every risk check.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "OrderIntentRequest".
 */
export interface OrderIntentRequest {
  account_id: string;
  instrument_id: InstrumentId;
  side: OrderSide;
  position_effect: PositionEffect;
  order_type: OrderType;
  quantity_base: NonNegativeDecimalString;
  limit_price?: DecimalString | null;
  trigger_price?: DecimalString | null;
  trigger_basis?: PriceBasis | null;
  time_in_force: TimeInForce;
  reduce_only: boolean;
  margin_mode?: MarginMode;
  leverage?: NonNegativeDecimalString | null;
  bracket?: Bracket | null;
  mode: OrderMode;
  source: IntentSource;
  strategy_version_id?: string | null;
  signal_id?: string | null;
  expires_at_ms?: TimestampMs | null;
  /**
   * Token from /v1/orders/preview binding the confirmed parameters. Required for manual LIVE submissions.
   */
  preview_token?: string | null;
}
/**
 * Immutable server-side intent.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "OrderIntent".
 */
export interface OrderIntent {
  /**
   * UUIDv7 generated server-side.
   */
  intent_id: string;
  tenant_id: string;
  user_id: string;
  idempotency_key: string;
  client_order_id: string;
  created_at_ms: TimestampMs;
  request: OrderIntentRequest;
}
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "RiskCheckResult".
 */
export interface RiskCheckResult {
  check: string;
  domain: RiskDomain;
  outcome: 'PASS' | 'FAIL' | 'WARN' | 'SKIP' | 'UNKNOWN';
  code?: ErrorCode | null;
  message: string;
  observed?: string | null;
  limit?: string | null;
}
/**
 * Deny-by-default policy pipeline verdict. Any FAIL or UNKNOWN on a mandatory check blocks new exposure.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "RiskDecision".
 */
export interface RiskDecision {
  decision: 'ALLOW' | 'REJECT' | 'BLOCK' | 'PAUSE';
  checks: RiskCheckResult[];
  policy_version: string;
  evaluated_at_ms: TimestampMs;
  reservation_id?: string | null;
}
/**
 * Whether protective orders are venue-native. Server-side software stops are never presented as guaranteed.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "ProtectionInfo".
 */
export interface ProtectionInfo {
  native_stop_loss: boolean;
  native_take_profit: boolean;
  note: string;
}
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "OrderPreview".
 */
export interface OrderPreview {
  allowed: boolean;
  normalized: OrderIntentRequest;
  notional: DecimalString;
  estimated_fee: DecimalString;
  estimated_slippage_pct?: DecimalString | null;
  margin_required: DecimalString;
  mark_price?: DecimalString | null;
  best_bid?: DecimalString | null;
  best_ask?: DecimalString | null;
  risk: RiskDecision;
  warnings: string[];
  preview_token: string;
  expires_at_ms: TimestampMs;
  protection: ProtectionInfo;
}
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "Order".
 */
export interface Order {
  order_id: string;
  intent_id: string;
  tenant_id: string;
  account_id: string;
  instrument_id: InstrumentId;
  client_order_id: string;
  exchange_order_id?: string | null;
  parent_order_id?: string | null;
  mode: OrderMode;
  role: OrderRole;
  side: OrderSide;
  order_type: OrderType;
  time_in_force: TimeInForce;
  quantity_base: NonNegativeDecimalString;
  filled_base: NonNegativeDecimalString;
  avg_fill_price?: DecimalString | null;
  limit_price?: DecimalString | null;
  trigger_price?: DecimalString | null;
  reduce_only: boolean;
  state: OrderState;
  state_reason?: string | null;
  strategy_version_id?: string | null;
  signal_id?: string | null;
  created_at_ms: TimestampMs;
  updated_at_ms: TimestampMs;
  version: number;
}
/**
 * Result of POST /v1/orders/intents. A repeated Idempotency-Key returns the original response with replayed=true.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "SubmitIntentResponse".
 */
export interface SubmitIntentResponse {
  intent: OrderIntent;
  risk: RiskDecision;
  orders: Order[];
  replayed: boolean;
}
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "AmendRequest".
 */
export interface AmendRequest {
  limit_price?: DecimalString | null;
  trigger_price?: DecimalString | null;
  quantity_base?: NonNegativeDecimalString | null;
  expected_version?: number;
}
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "Fill".
 */
export interface Fill {
  fill_id: string;
  order_id: string;
  account_id: string;
  instrument_id: InstrumentId;
  exchange_fill_id: string;
  side: OrderSide;
  price: DecimalString;
  quantity_base: NonNegativeDecimalString;
  fee: DecimalString;
  fee_asset: string;
  liquidity: 'maker' | 'taker';
  ts_ms: TimestampMs;
  mode: OrderMode;
}
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "Position".
 */
export interface Position {
  position_id: string;
  account_id: string;
  instrument_id: InstrumentId;
  side: 'long' | 'short' | 'flat';
  quantity_base: NonNegativeDecimalString;
  entry_price?: DecimalString | null;
  mark_price?: DecimalString | null;
  unrealized_pnl?: DecimalString | null;
  realized_pnl: DecimalString;
  liquidation_price?: DecimalString | null;
  margin?: DecimalString | null;
  leverage?: DecimalString | null;
  funding_accrued?: DecimalString | null;
  mode: OrderMode;
  stale: boolean;
  as_of_ms: TimestampMs;
}
/**
 * What the venue adapter can do for this account, negotiated at runtime. Unsupported features are rejected, never emulated silently.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "TradingCapabilities".
 */
export interface TradingCapabilities {
  venue: Venue;
  status: TradingStatus;
  order_types: OrderType[];
  time_in_force: TimeInForce[];
  native_reduce_only: boolean;
  native_stop_loss: boolean;
  native_take_profit: boolean;
  native_oco: boolean;
  amend: boolean;
  batch: boolean;
  position_modes: ('ONE_WAY' | 'HEDGE')[];
  margin_modes: MarginMode[];
  client_order_id: boolean;
  query_by_client_id: boolean;
  notes: string[];
}
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "AccountSummary".
 */
export interface AccountSummary {
  account_id: string;
  tenant_id: string;
  venue: Venue;
  label: string;
  status: AccountStatus;
  /**
   * Order modes currently permitted for this account.
   */
  modes: OrderMode[];
  equity?: DecimalString | null;
  available_margin?: DecimalString | null;
  currency: string;
  positions: Position[];
  open_orders: Order[];
  capabilities: TradingCapabilities;
  health: HealthState;
  stale: boolean;
  last_sync_ms: TimestampMs | null;
}
/**
 * Engaging blocks new opening orders. Cancelling and flattening are separate explicit options.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "KillSwitchRequest".
 */
export interface KillSwitchRequest {
  scope: KillSwitchScope;
  target_id?: string | null;
  engage: boolean;
  cancel_open_orders: boolean;
  flatten_positions: boolean;
  reason: string;
}
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "KillSwitchState".
 */
export interface KillSwitchState {
  scope: KillSwitchScope;
  target_id?: string | null;
  engaged: boolean;
  cancel_open_orders: boolean;
  flatten_positions: boolean;
  reason: string;
  actor: string;
  changed_at_ms: TimestampMs;
}
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "KillSwitchList".
 */
export interface KillSwitchList {
  switches: KillSwitchState[];
}
/**
 * Append-only, hash-chained audit record.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "AuditEvent".
 */
export interface AuditEvent {
  event_id: string;
  tenant_id: string;
  account_id?: string | null;
  actor: string;
  action: string;
  entity_type: string;
  entity_id: string;
  trace_id: string;
  ts_ms: TimestampMs;
  payload: {};
  prev_hash: string;
  hash: string;
}
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "AuditEventList".
 */
export interface AuditEventList {
  events: AuditEvent[];
  chain_valid: boolean;
}
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "DeploymentLimits".
 */
export interface DeploymentLimits {
  max_notional: NonNegativeDecimalString;
  max_open_positions: number;
  max_daily_loss: NonNegativeDecimalString;
  allowed_instruments: InstrumentId[];
  max_leverage: NonNegativeDecimalString;
}
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "SizingModel".
 */
export interface SizingModel {
  model: 'RISK_PCT' | 'FIXED_QTY';
  risk_pct?: NonNegativeDecimalString;
  fixed_qty?: NonNegativeDecimalString;
}
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "LiveConsent".
 */
export interface LiveConsent {
  consented_at_ms: TimestampMs;
  expires_at_ms: TimestampMs;
  scope_hash: string;
  actor: string;
}
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "DeploymentRequest".
 */
export interface DeploymentRequest {
  strategy_version_id: string;
  account_id: string;
  mode: 'PAPER' | 'SHADOW' | 'AUTO_LIVE';
  limits: DeploymentLimits;
  sizing: SizingModel;
}
/**
 * Binding of one immutable strategy version to one account. AUTO_LIVE stays PENDING_CONSENT until scoped consent, step-up and the global live gate all pass.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "Deployment".
 */
export interface Deployment {
  deployment_id: string;
  tenant_id: string;
  strategy_version_id: string;
  account_id: string;
  mode: 'PAPER' | 'SHADOW' | 'AUTO_LIVE';
  state: 'ENABLED' | 'DISABLED' | 'PENDING_CONSENT' | 'REVOKED';
  limits: DeploymentLimits;
  sizing: SizingModel;
  live_consent?: LiveConsent | null;
  created_at_ms: TimestampMs;
  updated_at_ms: TimestampMs;
}
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "OrderList".
 */
export interface OrderList {
  orders: Order[];
}
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "FillList".
 */
export interface FillList {
  fills: Fill[];
}
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "PositionList".
 */
export interface PositionList {
  positions: Position[];
}
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "AccountList".
 */
export interface AccountList {
  accounts: AccountSummary[];
}
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "DeploymentList".
 */
export interface DeploymentList {
  deployments: Deployment[];
}
/**
 * Close or reduce a position with a reduce-only order. Never reverses the position.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "ClosePositionRequest".
 */
export interface ClosePositionRequest {
  fraction: NonNegativeDecimalString;
  order_type: 'market' | 'limit';
  limit_price?: DecimalString | null;
  preview_token?: string | null;
}
/**
 * Versioned presentation record of an area of interest. Lines use lower == upper.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "AoiRecord".
 */
export interface AoiRecord {
  schema_version: 1;
  type: 'aoi.updated';
  instrument_id: InstrumentId;
  timeframe: Timeframe;
  aoi_id: string;
  /**
   * Indicator plugin id.
   */
  source: string;
  kind: AoiKind;
  side: ZoneSide;
  state: AoiState;
  lower: DecimalString;
  upper: DecimalString;
  created_at_ms: TimestampMs;
  confirmed_at_ms: TimestampMs;
  invalidated_at_ms?: TimestampMs | null;
  as_of_ms: TimestampMs;
  quality_score?: number | null;
  range_id?: string | null;
  indicator_version: string;
  method: IndicatorMethod;
  meta?: {};
}
/**
 * Market-structure event confirmed at a closed bar.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "StructureEvent".
 */
export interface StructureEvent {
  event_id: string;
  instrument_id: InstrumentId;
  timeframe: Timeframe;
  source: string;
  name: StructureEventName;
  bar_start_ms: TimestampMs;
  confirmed_at_ms: TimestampMs;
  price?: DecimalString | null;
  aoi_id?: string | null;
  score?: number | null;
  meta?: {};
}
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "DataQuality".
 */
export interface DataQuality {
  feed_state: FeedState;
  missing_bars: number;
  coverage_pct?: DecimalString | null;
}
/**
 * State of one indicator plugin on one instrument/timeframe as of a closed bar.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "IndicatorSnapshot".
 */
export interface IndicatorSnapshot {
  schema_version: 1;
  instrument_id: InstrumentId;
  timeframe: Timeframe;
  plugin_id: string;
  indicator_version: string;
  method: IndicatorMethod;
  as_of_ms: TimestampMs;
  bar_start_ms: TimestampMs;
  aois: AoiRecord[];
  events: StructureEvent[];
  values: {
    [k: string]: IndicatorValue;
  };
  warmup_complete: boolean;
  data_quality: DataQuality;
}
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "IndicatorSnapshotList".
 */
export interface IndicatorSnapshotList {
  snapshots: IndicatorSnapshot[];
  as_of_ms: TimestampMs;
}
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "AoiList".
 */
export interface AoiList {
  instrument_id: InstrumentId;
  timeframe: Timeframe;
  aois: AoiRecord[];
  events?: StructureEvent[];
  as_of_ms: TimestampMs;
  feed_state: FeedState;
}
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "IndicatorPluginInfo".
 */
export interface IndicatorPluginInfo {
  plugin_id: string;
  indicator_version: string;
  schema_version: number;
  display_name: string;
  description: string;
  timeframes: Timeframe[];
  warmup_bars: number;
  required_streams: string[];
  input_schema: {};
  output_kinds: AoiKind[];
  output_events: StructureEventName[];
  licensing_status: LicensingStatus;
  parity_status: ParityStatus;
}
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "IndicatorPluginList".
 */
export interface IndicatorPluginList {
  plugins: IndicatorPluginInfo[];
}
/**
 * Published values of one plugin on one closed bar.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "IndicatorSeriesPoint".
 */
export interface IndicatorSeriesPoint {
  bar_start_ms: TimestampMs;
  values: {
    [k: string]: IndicatorValue;
  };
}
/**
 * Per-bar values of one plugin (e.g. order-flow Delta % for a sub-pane). Values are point-in-time: each point is what the plugin published when that bar closed.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "IndicatorSeries".
 */
export interface IndicatorSeries {
  instrument_id: InstrumentId;
  timeframe: Timeframe;
  plugin_id: string;
  indicator_version: string;
  points: IndicatorSeriesPoint[];
  as_of_ms: TimestampMs;
  feed_state: FeedState;
}
/**
 * Structured identity of a tradable contract. Never key solely on a symbol such as BTCUSDT.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "InstrumentKey".
 */
export interface InstrumentKey {
  venue: Venue;
  market_type: MarketType;
  venue_market_id: string;
  canonical_base: string;
  quote: string;
  settlement: string;
  instrument_version: number;
}
/**
 * Venue instrument with precision and contract metadata.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "Instrument".
 */
export interface Instrument {
  instrument_id: InstrumentId;
  key: InstrumentKey;
  display_symbol: string;
  tick_size: NonNegativeDecimalString;
  lot_size: NonNegativeDecimalString;
  min_qty: NonNegativeDecimalString;
  max_qty?: NonNegativeDecimalString | null;
  min_notional?: NonNegativeDecimalString | null;
  contract_multiplier: NonNegativeDecimalString;
  linear: boolean;
  status: InstrumentStatus;
  funding_interval_hours?: number | null;
  max_leverage?: NonNegativeDecimalString | null;
  asset_class: AssetClass;
  source: string;
  as_of_ms: TimestampMs;
}
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "InstrumentList".
 */
export interface InstrumentList {
  instruments: Instrument[];
  as_of_ms: TimestampMs;
}
/**
 * OHLCV bar over the half-open interval [start_ms, end_ms). Only is_final bars may confirm signals. Corrections increment revision.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "Candle".
 */
export interface Candle {
  instrument_id: InstrumentId;
  timeframe: Timeframe;
  start_ms: TimestampMs;
  end_ms: TimestampMs;
  open: DecimalString;
  high: DecimalString;
  low: DecimalString;
  close: DecimalString;
  base_volume: NonNegativeDecimalString;
  quote_turnover?: NonNegativeDecimalString | null;
  trade_count?: number | null;
  /**
   * Base volume of trades whose aggressor bought (venue-published or summed from the venue's public trades). Enables true volume delta; null when the venue publishes neither.
   */
  taker_buy_base_volume?: NonNegativeDecimalString | null;
  is_final: boolean;
  revision: number;
  /**
   * Origin such as `binance.rest`, `binance.ws`, `replay`, `mock`.
   */
  source: string;
  feed_state: FeedState;
  received_at_ms: TimestampMs;
  event_ts_ms?: TimestampMs | null;
}
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "TimeGap".
 */
export interface TimeGap {
  from_ms: TimestampMs;
  to_ms: TimestampMs;
}
/**
 * Historical candles response with provenance and detected gaps.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "CandleBatch".
 */
export interface CandleBatch {
  schema_version: 1;
  instrument_id: InstrumentId;
  timeframe: Timeframe;
  candles: Candle[];
  feed_state: FeedState;
  source: string;
  as_of_ms: TimestampMs;
  complete: boolean;
  gaps: TimeGap[];
}
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "Trade".
 */
export interface Trade {
  instrument_id: InstrumentId;
  trade_id: string;
  venue_event_ts_ms: TimestampMs;
  received_at_ms: TimestampMs;
  price: DecimalString;
  base_quantity: NonNegativeDecimalString;
  quote_quantity?: NonNegativeDecimalString | null;
  aggressor_side: 'buy' | 'sell' | 'unknown';
  is_aggregated: boolean;
}
/**
 * Order book built from a snapshot plus validated increments. A stale book must never price real slippage.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "OrderBook".
 */
export interface OrderBook {
  instrument_id: InstrumentId;
  venue_sequence?: number | null;
  snapshot_ts_ms: TimestampMs;
  received_at_ms: TimestampMs;
  bids: BookLevel[];
  asks: BookLevel[];
  depth: number;
  stale: boolean;
  source: string;
  checksum_verified?: boolean | null;
}
/**
 * Funding rate. normalized_8h_pct = rate * 100 * 8 / interval_hours, for comparison only.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "Funding".
 */
export interface Funding {
  instrument_id: InstrumentId;
  rate: DecimalString;
  interval_hours: number;
  normalized_8h_pct: DecimalString;
  kind: FundingKind;
  effective_time_ms: TimestampMs;
  next_funding_ms?: TimestampMs | null;
  source: string;
  as_of_ms: TimestampMs;
}
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "OpenInterest".
 */
export interface OpenInterest {
  instrument_id: InstrumentId;
  base_quantity: NonNegativeDecimalString;
  notional_usd: NonNegativeDecimalString;
  mark_price?: DecimalString | null;
  source: string;
  as_of_ms: TimestampMs;
}
/**
 * Latest market state for one instrument. Mark, last and index are separate fields; missing values are null, never zero.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "MarketSnapshot".
 */
export interface MarketSnapshot {
  instrument_id: InstrumentId;
  last?: DecimalString | null;
  mark?: DecimalString | null;
  index?: DecimalString | null;
  best_bid?: DecimalString | null;
  best_ask?: DecimalString | null;
  spread_pct?: DecimalString | null;
  turnover_24h_quote?: NonNegativeDecimalString | null;
  volume_24h_base?: NonNegativeDecimalString | null;
  change_24h_pct?: DecimalString | null;
  funding?: Funding | null;
  open_interest?: OpenInterest | null;
  feed_state: FeedState;
  source: string;
  as_of_ms: TimestampMs;
  data_age_ms: number;
  stale: boolean;
}
/**
 * Observable health of one venue feed. Fallbacks are always visible here.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "FeedHealth".
 */
export interface FeedHealth {
  venue: Venue;
  state: HealthState;
  feed_mode: FeedMode;
  feed_state: FeedState;
  transport: Transport;
  last_message_ms?: TimestampMs | null;
  lag_ms?: number | null;
  reconnects: number;
  gaps_detected: number;
  last_error?: string | null;
  as_of_ms: TimestampMs;
}
/**
 * Market-data features a venue adapter actually implements. Unsupported is false, never faked.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "MarketDataCapabilities".
 */
export interface MarketDataCapabilities {
  rest_history: boolean;
  native_ws_candles: boolean;
  native_ws_trades: boolean;
  native_ws_book: boolean;
  book_snapshot: boolean;
  open_interest: boolean;
  funding: boolean;
  trades_history: boolean;
  /**
   * The venue's klines (1m and above) carry taker_buy_base_volume, so true delta is available on every timeframe. Seconds bars built from trades (trade_built_timeframes) carry it on every venue regardless of this flag.
   */
  taker_volume?: boolean;
}
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "VenueCapabilities".
 */
export interface VenueCapabilities {
  venue: Venue;
  display_name: string;
  market_types: MarketType[];
  market_data: MarketDataCapabilities;
  timeframes: Timeframe[];
  /**
   * Subset of timeframes the engine builds from the venue's public trade stream (seconds bars), because the venue publishes no such klines. History is limited to what the venue's trade history endpoint returns.
   */
  trade_built_timeframes?: Timeframe[];
  notes: string[];
  docs_url: string;
  verified_at_ms?: TimestampMs | null;
}
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "VenueStatus".
 */
export interface VenueStatus {
  capabilities: VenueCapabilities;
  health: FeedHealth;
}
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "VenueStatusList".
 */
export interface VenueStatusList {
  venues: VenueStatus[];
  as_of_ms: TimestampMs;
}
/**
 * One hard gate with the observed value, threshold and outcome. passed=null means the value was missing or stale.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "GateResult".
 */
export interface GateResult {
  gate: GateName;
  observed: DecimalString | null;
  threshold: DecimalString;
  comparator: 'gt' | 'lt';
  passed: boolean | null;
  unit: 'USD' | 'ratio' | 'pct';
  as_of_ms?: TimestampMs | null;
  reason: string;
}
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "FundingAssessment".
 */
export interface FundingAssessment {
  normalized_8h_pct: DecimalString | null;
  interval_hours?: number | null;
  risk: FundingRisk;
}
/**
 * Qualification of one exact venue instrument.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "QualificationRecord".
 */
export interface QualificationRecord {
  instrument_id: InstrumentId;
  venue: Venue;
  display_symbol?: string;
  status: QualificationStatus;
  risk_tags: RiskTag[];
  gates: GateResult[];
  funding: FundingAssessment;
  asset_class: AssetClass;
  heat_score?: number | null;
  rank?: number | null;
  policy_version: string;
  as_of_ms: TimestampMs;
  reasons: string[];
}
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "QualificationReport".
 */
export interface QualificationReport {
  policy_version: string;
  as_of_ms: TimestampMs;
  venue?: Venue | null;
  records: QualificationRecord[];
}
/**
 * Explanation trace for one evaluated rule node, for both true and false predicates.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "ReasonNode".
 */
export interface ReasonNode {
  /**
   * JSON pointer of the node inside `when`.
   */
  node_path: string;
  node_id?: string | null;
  op: string;
  result: TriState;
  detail: string;
  evidence?: {};
  children: ReasonNode[];
}
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "RiskEvaluation".
 */
export interface RiskEvaluation {
  status: 'PASS' | 'FAIL' | 'UNKNOWN' | 'NOT_APPLICABLE';
  reasons: string[];
}
/**
 * One lifecycle notification of a strategy setup on one exact venue instrument.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "Signal".
 */
export interface Signal {
  signal_id: string;
  /**
   * tenant + strategy_version + account scope + instrument + direction + setup id + kind.
   */
  dedupe_key: string;
  tenant_id: string;
  strategy_id: string;
  strategy_version_id: string;
  setup_id?: string | null;
  instrument_id: InstrumentId;
  direction: Direction;
  timeframe: Timeframe;
  kind: SignalKind;
  setup_state: SetupState;
  severity: Severity;
  bar_start_ms: TimestampMs;
  bar_close_ms: TimestampMs;
  evaluated_at_ms: TimestampMs;
  inputs_snapshot_hash: string;
  /**
   * definition_hash of the evaluated StrategyVersion.
   */
  rule_version: string;
  evaluator_version: string;
  indicator_versions: {
    [k: string]: string | undefined;
  };
  reason_tree: ReasonNode;
  aoi_ids: string[];
  range_ids?: string[];
  /**
   * Base volume of the evaluated (signal) bar. Not comparable across instruments; rank by bar_quote_volume.
   */
  bar_base_volume?: NonNegativeDecimalString | null;
  /**
   * Quote-currency turnover of the evaluated bar as published by the venue; null when the venue publishes none (never estimated).
   */
  bar_quote_volume?: NonNegativeDecimalString | null;
  entry_price?: DecimalString | null;
  stop_price?: DecimalString | null;
  target_price?: DecimalString | null;
  rr?: DecimalString | null;
  risk_evaluation: RiskEvaluation;
  mode: ExecutionMode;
  data_source: FeedState;
  revision: number;
  message?: string;
}
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "SignalList".
 */
export interface SignalList {
  signals: Signal[];
  next_cursor?: string | null;
}
/**
 * Local-time window in which deliveries are held back, except bypass severities.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "QuietHours".
 */
export interface QuietHours {
  start: string;
  end: string;
  /**
   * IANA time zone, e.g. Asia/Taipei.
   */
  tz: string;
  bypass_severities?: Severity[];
}
/**
 * Where and when a tenant receives signal notifications. Secrets (bot tokens) are server configuration, never part of a subscription.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "AlertSubscription".
 */
export interface AlertSubscription {
  subscription_id: string;
  channel: 'TELEGRAM' | 'LINE' | 'EMAIL' | 'WEBHOOK';
  /**
   * Telegram chat id, LINE user id, e-mail address or https webhook URL.
   */
  target: string;
  /**
   * Empty means every kind.
   */
  kinds: SignalKind[];
  min_severity: Severity;
  /**
   * Empty means every strategy.
   */
  strategy_ids: string[];
  enabled: boolean;
  quiet_hours?: QuietHours | null;
}
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "AlertSubscriptionList".
 */
export interface AlertSubscriptionList {
  subscriptions: AlertSubscription[];
  /**
   * Channels the server has credentials for; others dead-letter with a clear reason.
   */
  configured_channels: (
    'UI' | 'TELEGRAM' | 'LINE' | 'EMAIL' | 'WEBHOOK' | 'EXECUTION'
  )[];
}
/**
 * TRUE when every child is TRUE; FALSE when any child is FALSE; otherwise UNKNOWN.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "AllNode".
 */
export interface AllNode {
  op: 'ALL';
  id?: string;
  label?: string;
  /**
   * @minItems 1
   * @maxItems 32
   */
  children: [RuleNode, ...RuleNode[]];
}
/**
 * TRUE when any child is TRUE; FALSE when every child is FALSE; otherwise UNKNOWN.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "AnyNode".
 */
export interface AnyNode {
  op: 'ANY';
  id?: string;
  label?: string;
  /**
   * @minItems 1
   * @maxItems 32
   */
  children: [RuleNode, ...RuleNode[]];
}
/**
 * Negation; NOT UNKNOWN is UNKNOWN.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "NotNode".
 */
export interface NotNode {
  op: 'NOT';
  id?: string;
  label?: string;
  child: RuleNode;
}
/**
 * Higher-timeframe structural bias read only from bars closed at or before the evaluated bar close. match=ALL requires every timeframe; K_OF_N requires k of them. require_bos additionally requires the latest event on each counted timeframe to be a same-direction BOS.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "BiasNode".
 */
export interface BiasNode {
  op: 'BIAS';
  id?: string;
  label?: string;
  /**
   * @minItems 1
   * @maxItems 6
   */
  timeframes:
    | [Timeframe]
    | [Timeframe, Timeframe]
    | [Timeframe, Timeframe, Timeframe]
    | [Timeframe, Timeframe, Timeframe, Timeframe]
    | [Timeframe, Timeframe, Timeframe, Timeframe, Timeframe]
    | [Timeframe, Timeframe, Timeframe, Timeframe, Timeframe, Timeframe];
  value: 'BULLISH' | 'BEARISH';
  match?: 'ALL' | 'K_OF_N';
  k?: number;
  require_bos?: boolean;
}
/**
 * Price inside a Premium/Discount/Equilibrium/OTE zone of the current dealing range on `tf`. Nodes sharing a range_group must bind the same range_id, otherwise the group is FALSE.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "PriceInZoneNode".
 */
export interface PriceInZoneNode {
  op: 'PRICE_IN_ZONE';
  id?: string;
  label?: string;
  zone: 'PREMIUM' | 'DISCOUNT' | 'EQUILIBRIUM' | 'OTE';
  tf: Timeframe;
  price_source?: 'close' | 'high' | 'low' | 'hl2';
  range_group?: string;
}
/**
 * A closed signal-timeframe bar within the last `within_bars` bars (0 = current bar only) touched an AOI that was already confirmed before that bar. touch=WICK uses high/low intersection, BODY uses open/close, CLOSE uses close inside the zone.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "AoiTouchNode".
 */
export interface AoiTouchNode {
  op: 'AOI_TOUCH';
  id?: string;
  label?: string;
  /**
   * @minItems 1
   * @maxItems 8
   */
  kinds:
    | [AoiKind]
    | [AoiKind, AoiKind]
    | [AoiKind, AoiKind, AoiKind]
    | [AoiKind, AoiKind, AoiKind, AoiKind]
    | [AoiKind, AoiKind, AoiKind, AoiKind, AoiKind]
    | [AoiKind, AoiKind, AoiKind, AoiKind, AoiKind, AoiKind]
    | [AoiKind, AoiKind, AoiKind, AoiKind, AoiKind, AoiKind, AoiKind]
    | [AoiKind, AoiKind, AoiKind, AoiKind, AoiKind, AoiKind, AoiKind, AoiKind];
  side: 'bullish' | 'bearish';
  tf?: Timeframe;
  within_bars: number;
  touch?: 'WICK' | 'BODY' | 'CLOSE';
  require_active?: boolean;
  min_quality?: number;
}
/**
 * An active AOI of kinds_a overlaps an active AOI of kinds_b by at least min_overlap_pct of the smaller zone.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "AoiOverlapNode".
 */
export interface AoiOverlapNode {
  op: 'AOI_OVERLAP';
  id?: string;
  label?: string;
  /**
   * @minItems 1
   */
  kinds_a: [AoiKind, ...AoiKind[]];
  /**
   * @minItems 1
   */
  kinds_b: [AoiKind, ...AoiKind[]];
  side: 'bullish' | 'bearish';
  tf?: Timeframe;
  min_overlap_pct?: NonNegativeDecimalString;
}
/**
 * A structure event confirmed within the last `within_bars` closed bars (0 = current bar).
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "EventNode".
 */
export interface EventNode {
  op: 'EVENT';
  id?: string;
  label?: string;
  name: StructureEventName;
  tf?: Timeframe;
  within_bars: number;
}
/**
 * Ordered events on distinct closed bars inside a window of `within_bars` bars. The final step must occur on the evaluated bar, so a completed sequence fires once.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "SequenceNode".
 */
export interface SequenceNode {
  op: 'SEQUENCE';
  id?: string;
  label?: string;
  within_bars: number;
  /**
   * @minItems 2
   * @maxItems 8
   */
  steps:
    | [EventRef, EventRef]
    | [EventRef, EventRef, EventRef]
    | [EventRef, EventRef, EventRef, EventRef]
    | [EventRef, EventRef, EventRef, EventRef, EventRef]
    | [EventRef, EventRef, EventRef, EventRef, EventRef, EventRef]
    | [EventRef, EventRef, EventRef, EventRef, EventRef, EventRef, EventRef]
    | [
        EventRef,
        EventRef,
        EventRef,
        EventRef,
        EventRef,
        EventRef,
        EventRef,
        EventRef,
      ];
}
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "EventRef".
 */
export interface EventRef {
  name: StructureEventName;
  tf?: Timeframe;
}
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "CompareNode".
 */
export interface CompareNode {
  op: 'COMPARE';
  id?: string;
  label?: string;
  left: Operand;
  cmp: 'gt' | 'gte' | 'lt' | 'lte' | 'eq' | 'neq';
  right: Operand;
}
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "ConstOperand".
 */
export interface ConstOperand {
  kind: 'const';
  value: DecimalString;
}
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "CandleOperand".
 */
export interface CandleOperand {
  kind: 'candle';
  tf?: Timeframe;
  field: 'open' | 'high' | 'low' | 'close' | 'base_volume';
  bars_ago?: number;
}
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "IndicatorOperand".
 */
export interface IndicatorOperand {
  kind: 'indicator';
  plugin_id: string;
  tf?: Timeframe;
  field: string;
  bars_ago?: number;
}
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "MarketOperand".
 */
export interface MarketOperand {
  kind: 'market';
  field:
    | 'funding_8h_pct'
    | 'open_interest_usd'
    | 'oi_change_pct'
    | 'spread_pct'
    | 'turnover_24h_quote';
}
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "InRangeNode".
 */
export interface InRangeNode {
  op: 'IN_RANGE';
  id?: string;
  label?: string;
  value: Operand;
  low: Operand;
  high: Operand;
  inclusive?: boolean;
}
/**
 * left crossed right between the previous and the evaluated closed bar.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "CrossesNode".
 */
export interface CrossesNode {
  op: 'CROSSES';
  id?: string;
  label?: string;
  left: Operand;
  right: Operand;
  direction: 'ABOVE' | 'BELOW';
}
/**
 * child is TRUE on each of the last `bars` closed bars including the evaluated bar.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "PersistForNode".
 */
export interface PersistForNode {
  op: 'PERSIST_FOR';
  id?: string;
  label?: string;
  bars: number;
  child: RuleNode;
}
/**
 * child is TRUE on at least one of the last `bars` + 1 closed bars.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "WithinBarsNode".
 */
export interface WithinBarsNode {
  op: 'WITHIN_BARS';
  id?: string;
  label?: string;
  bars: number;
  child: RuleNode;
}
/**
 * Evaluated bar starts inside one of the named New York time sessions (DST aware): ASIA 20:00-00:00, LONDON 02:00-05:00, NEW_YORK 08:30-11:00.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "SessionNode".
 */
export interface SessionNode {
  op: 'SESSION';
  id?: string;
  label?: string;
  /**
   * @minItems 1
   */
  sessions: [
    'ASIA' | 'LONDON' | 'NEW_YORK',
    ...('ASIA' | 'LONDON' | 'NEW_YORK')[],
  ];
}
/**
 * Projected reward/risk net of estimated costs from the strategy risk model is at least min_rr.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "RiskRewardNode".
 */
export interface RiskRewardNode {
  op: 'RISK_REWARD';
  id?: string;
  label?: string;
  min_rr: NonNegativeDecimalString;
}
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "Universe".
 */
export interface Universe {
  qualification: 'QUALIFIED_ONLY' | 'ANY';
  /**
   * @minItems 1
   */
  venues: [Venue, ...Venue[]];
  whitelist?: InstrumentId[];
  blacklist?: InstrumentId[];
  min_qualified_minutes?: number;
}
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "RiskModel".
 */
export interface RiskModel {
  min_rr: NonNegativeDecimalString;
  stop_model: 'AOI_INVALIDATION' | 'SWING' | 'ATR';
  stop_buffer_atr?: NonNegativeDecimalString;
  target_model: 'NEAREST_OPPOSING_LIQUIDITY' | 'FIXED_R';
  fixed_r?: NonNegativeDecimalString;
  fee_bps_estimate?: NonNegativeDecimalString;
  max_risk_pct_equity?: NonNegativeDecimalString;
}
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "NotificationPolicy".
 */
export interface NotificationPolicy {
  cooldown_bars: number;
  once_per_aoi: boolean;
  emit?: SignalKind[];
  channels?: ('UI' | 'TELEGRAM' | 'LINE' | 'EMAIL' | 'WEBHOOK')[];
}
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "SetupPolicy".
 */
export interface SetupPolicy {
  expire_after_bars?: number;
}
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "DataPolicy".
 */
export interface DataPolicy {
  max_data_age_ms?: number;
  allow_rest_poll?: boolean;
}
/**
 * User-authored strategy. Immutable once stored as a StrategyVersion.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "StrategyDefinition".
 */
export interface StrategyDefinition {
  schema_version: 2;
  strategy_id: string;
  name: string;
  description?: string;
  direction: Direction;
  signal_timeframe: Timeframe;
  universe: Universe;
  when: RuleNode;
  risk: RiskModel;
  notification: NotificationPolicy;
  setup?: SetupPolicy;
  data_policy?: DataPolicy;
  execution_mode: ExecutionMode;
}
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "Activation".
 */
export interface Activation {
  mode: ExecutionMode;
  activated_at_ms: TimestampMs;
  instruments?: InstrumentId[];
}
/**
 * Immutable stored version. Editing creates a new version; live permissions never carry over.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "StrategyVersion".
 */
export interface StrategyVersion {
  strategy_version_id: string;
  strategy_id: string;
  version: number;
  tenant_id: string;
  author_user_id: string;
  created_at_ms: TimestampMs;
  definition: StrategyDefinition;
  definition_hash: string;
  compiler_version: string;
  evaluator_version: string;
  state: StrategyState;
  activation?: Activation | null;
}
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "StrategyVersionList".
 */
export interface StrategyVersionList {
  versions: StrategyVersion[];
}
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "ValidationIssue".
 */
export interface ValidationIssue {
  /**
   * JSON pointer into the definition.
   */
  path: string;
  code: string;
  message: string;
}
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "ValidationResult".
 */
export interface ValidationResult {
  valid: boolean;
  errors: ValidationIssue[];
  warnings: ValidationIssue[];
  required_plugins: string[];
  required_timeframes: Timeframe[];
  node_count: number;
  max_depth: number;
  definition_hash?: string | null;
}
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "ActivateRequest".
 */
export interface ActivateRequest {
  version: number;
  /**
   * AUTO_LIVE cannot be activated here; it requires an execution-engine deployment with consent.
   */
  mode: 'ALERT_ONLY' | 'PAPER' | 'SHADOW';
  instruments?: InstrumentId[];
}
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "BacktestRequest".
 */
export interface BacktestRequest {
  version: number;
  instrument_id: InstrumentId;
  from_ms: TimestampMs;
  to_ms: TimestampMs;
  fee_bps?: NonNegativeDecimalString;
  slippage_bps?: NonNegativeDecimalString;
  max_hold_bars?: number;
}
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "BacktestTrade".
 */
export interface BacktestTrade {
  signal_id: string;
  direction: Direction;
  entry_ms: TimestampMs;
  entry_price: DecimalString;
  stop_price: DecimalString;
  target_price: DecimalString;
  exit_ms: TimestampMs;
  exit_price: DecimalString;
  exit_reason: 'STOP' | 'TARGET' | 'TIMEOUT' | 'END_OF_DATA';
  r_multiple: DecimalString;
  fees_r: DecimalString;
  hold_bars: number;
}
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "BacktestMetrics".
 */
export interface BacktestMetrics {
  trades: number;
  wins: number;
  losses: number;
  win_rate: DecimalString | null;
  expectancy_r: DecimalString | null;
  profit_factor: DecimalString | null;
  max_drawdown_r: DecimalString;
  avg_r: DecimalString | null;
  avg_hold_bars: DecimalString | null;
}
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "BacktestReport".
 */
export interface BacktestReport {
  backtest_id: string;
  strategy_version_id: string;
  instrument_id: InstrumentId;
  from_ms: TimestampMs;
  to_ms: TimestampMs;
  bars_evaluated: number;
  signals: Signal[];
  trades: BacktestTrade[];
  metrics: BacktestMetrics;
  missing_bars: number;
  feed_state: FeedState;
  assumptions: string[];
  evaluator_version: string;
}
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "StreamEnvelope".
 */
export interface StreamEnvelope {
  type: StreamEventType;
  event_id: string;
  schema_version: 1;
  /**
   * Monotonic per connection stream (market) or per tenant (user).
   */
  sequence: number;
  event_ts_ms: TimestampMs;
  correlation_id?: string | null;
  /**
   * Channel key, e.g. `candles:binance:perp:BTCUSDT:5m`.
   */
  channel?: string | null;
  payload: {};
}
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "ChannelSpec".
 */
export interface ChannelSpec {
  channel: ChannelName;
  instrument_id?: InstrumentId;
  timeframe?: Timeframe;
  account_id?: string;
  feed_mode?: FeedMode;
}
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "ClientMessage".
 */
export interface ClientMessage {
  action: 'subscribe' | 'unsubscribe' | 'ping';
  channels?: ChannelSpec[];
  last_sequence?: number | null;
}
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "SnapshotRequiredPayload".
 */
export interface SnapshotRequiredPayload {
  reason: string;
  channels: string[];
}
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "DrawingAnchor".
 */
export interface DrawingAnchor {
  time_ms: TimestampMs;
  price: DecimalString;
}
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "DrawingStyle".
 */
export interface DrawingStyle {
  color: string;
  line_width: number;
  line_style: 'solid' | 'dashed' | 'dotted';
  fill_color?: string | null;
  text_color?: string | null;
  font_size?: number;
  extend_left?: boolean;
  extend_right?: boolean;
  show_labels?: boolean;
}
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "DrawingDocument".
 */
export interface DrawingDocument {
  schema_version: 1;
  drawing_id: string;
  /**
   * Plugin id, e.g. `trendline`, `fib-retracement`.
   */
  tool_id: string;
  tool_version: number;
  instrument_id: InstrumentId;
  /**
   * null = visible on every timeframe.
   */
  timeframe?: Timeframe | null;
  /**
   * @maxItems 64
   */
  anchors: DrawingAnchor[];
  style: DrawingStyle;
  text?: string | null;
  /**
   * Tool-specific settings, e.g. fib levels or position quantity.
   */
  props?: {};
  locked: boolean;
  visible: boolean;
  z_index: number;
  created_at_ms: TimestampMs;
  updated_at_ms: TimestampMs;
}
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "DrawingCollection".
 */
export interface DrawingCollection {
  schema_version: 1;
  workspace_id: string;
  instrument_id: InstrumentId;
  drawings: DrawingDocument[];
}
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "IndicatorLayerState".
 */
export interface IndicatorLayerState {
  plugin_id: string;
  visible: boolean;
  /**
   * AOI kinds shown; all kinds when omitted.
   */
  kinds?: AoiKind[];
  /**
   * Presentation only: per-kind colors, event markers, sub-pane visibility.
   */
  style?: {};
  /**
   * Indicator inputs validated by the backend against the plugin's input_schema.
   */
  settings?: {};
}
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "ChartState".
 */
export interface ChartState {
  chart_id: string;
  instrument_id: InstrumentId;
  timeframe: Timeframe;
  chart_type: ChartType;
  price_scale: PriceScaleMode;
  show_volume: boolean;
  indicator_layers: IndicatorLayerState[];
  link_group?: 'A' | 'B' | 'C' | 'D' | null;
  feed_mode: FeedMode;
  /**
   * Instruments overlaid on a separate percentage axis.
   *
   * @maxItems 3
   */
  compare?:
    | []
    | [InstrumentId]
    | [InstrumentId, InstrumentId]
    | [InstrumentId, InstrumentId, InstrumentId];
  /**
   * Draw UTC session boundaries.
   */
  session_separators?: boolean;
  visible_range?: {
    from_ms: TimestampMs;
    to_ms: TimestampMs;
  } | null;
}
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "SyncOptions".
 */
export interface SyncOptions {
  crosshair: boolean;
  instrument: boolean;
  timeframe: boolean;
  viewport: boolean;
}
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "WorkspaceDocument".
 */
export interface WorkspaceDocument {
  schema_version: 1;
  workspace_id: string;
  name: string;
  layout: LayoutId;
  sync: SyncOptions;
  active_chart_id: string;
  /**
   * @minItems 1
   * @maxItems 8
   */
  charts:
    | [ChartState]
    | [ChartState, ChartState]
    | [ChartState, ChartState, ChartState]
    | [ChartState, ChartState, ChartState, ChartState]
    | [ChartState, ChartState, ChartState, ChartState, ChartState]
    | [ChartState, ChartState, ChartState, ChartState, ChartState, ChartState]
    | [
        ChartState,
        ChartState,
        ChartState,
        ChartState,
        ChartState,
        ChartState,
        ChartState,
      ]
    | [
        ChartState,
        ChartState,
        ChartState,
        ChartState,
        ChartState,
        ChartState,
        ChartState,
        ChartState,
      ];
  favorites?: {
    timeframes?: Timeframe[];
    instruments?: InstrumentId[];
  };
  /**
   * Opaque per-feature panel state keyed by feature id.
   */
  panels?: {};
  updated_at_ms: TimestampMs;
}
