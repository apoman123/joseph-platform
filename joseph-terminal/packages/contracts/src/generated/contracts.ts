/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

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
 * Market venue identifier: a crypto venue, a securities exchange (`twse` Taiwan Stock Exchange, `tpex` Taipei Exchange mainboard, `nasdaq`, `nyse`, `cboe` Cboe BZX; `nyse` also covers NYSE Arca and NYSE American listings, `cboe` lists GOVT and many other ETFs) or a rate publisher (`treasury`, US Treasury yield curves). `mock` is the synthetic DEMO venue and is never tradable live.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "Venue".
 */
export type Venue =
  | 'bybit'
  | 'okx'
  | 'coinbase'
  | 'kraken'
  | 'deriv'
  | 'binance'
  | 'hyperliquid'
  | 'lighter'
  | 'bitget'
  | 'mexc'
  | 'twse'
  | 'tpex'
  | 'nasdaq'
  | 'nyse'
  | 'cboe'
  | 'treasury'
  | 'mock';
/**
 * Securities broker that executes orders on exchange venues: `ibkr` (Interactive Brokers: nasdaq, nyse, cboe), `sinopac` (SinoPac Shioaji: twse, tpex), `cathay` (Cathay Securities: twse, tpex).
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "Broker".
 */
export type Broker = 'ibkr' | 'sinopac' | 'cathay';
/**
 * Where an account trades: a crypto venue (orders go to the venue itself) or a securities broker.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "TradingVenue".
 */
export type TradingVenue =
  | 'binance'
  | 'hyperliquid'
  | 'lighter'
  | 'bitget'
  | 'mexc'
  | 'ibkr'
  | 'sinopac'
  | 'cathay'
  | 'mock';
/**
 * Session calendar. `24x7` markets never close and use UTC-aligned bars. Exchange calendars align intraday bars to the session open (the last bar of a session is cut at the close), make a daily bar one session and weekly/monthly bars the sessions of an ISO week/calendar month in exchange time. XTAI: TWSE and TPEx 09:00-13:30 Asia/Taipei. XNYS: NYSE/Nasdaq/Cboe 09:30-16:00 America/New_York with early closes. USBOND: US Treasury market business days 08:00-17:00 America/New_York.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "CalendarId".
 */
export type CalendarId = '24x7' | 'XTAI' | 'XNYS' | 'USBOND';
/**
 * Product type at the venue. `stock` and `etf` are exchange-listed cash securities; `rate` is a published, non-tradable rate series (Treasury par and real yields, in percent).
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "MarketType".
 */
export type MarketType = 'perp' | 'spot' | 'future' | 'stock' | 'etf' | 'rate';
/**
 * Canonical venue-scoped instrument id `{venue}:{market_type}:{venue_market_id}`, e.g. `binance:perp:BTCUSDT`, `hyperliquid:perp:BTC`, `twse:stock:2330`, `tpex:etf:00679B`, `nasdaq:etf:TLT`, `cboe:etf:GOVT` or `treasury:rate:UST10Y`. The same base asset on two venues is two instruments.
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
  | '10m'
  | '15m'
  | '30m'
  | '45m'
  | '1h'
  | '2h'
  | '4h'
  | '6h'
  | '8h'
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
  | 'LIVE_NOT_ARMED'
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
 * Corporate-action normalization of historical OHLC: RAW, stock SPLIT normalization, or provider ALL actions. Actual quotes, orders and reported volume stay native.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "PriceAdjustment".
 */
export type PriceAdjustment = 'RAW' | 'SPLIT' | 'ALL';
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
 * Time in force. DAY (TWSE ROD) expires at the close of the exchange session and applies to exchange-listed securities only; crypto venues trade 24x7 and use GTC/IOC/FOK/POST_ONLY.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "TimeInForce".
 */
export type TimeInForce = 'GTC' | 'IOC' | 'FOK' | 'POST_ONLY' | 'DAY';
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
 * AOI kind, declared by the publishing plugin in `IndicatorPluginInfo.output_kinds`; unique across installed plugins.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "AoiKind".
 */
export type AoiKind = string;
/**
 * Lifecycle of an AOI. `active` and `tested` are live; `invalidated` and `expired` have ended, optionally with a plugin-defined `end_reason`.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "AoiState".
 */
export type AoiState = 'active' | 'tested' | 'invalidated' | 'expired';
/**
 * Plugin-defined, display-only reason why an AOI ended, labelled by the plugin's `PluginDisplay.end_reasons`.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "EndReason".
 */
export type EndReason = string;
/**
 * `pine_parity` reproduces the reference Pine semantics; `trade` uses venue trades with a published aggressor side (true delta); `trade_classification` uses trades classified against the quote (Lee-Ready / quote rule) on stock exchanges that publish no aggressor side, an estimate distinct from true delta. Neither trade method is ever presented as parity.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "IndicatorMethod".
 */
export type IndicatorMethod = 'pine_parity' | 'trade' | 'trade_classification';
/**
 * Event name, declared by the publishing plugin in `IndicatorPluginInfo.output_events`. `AOI_INVALIDATED` and `AOI_EXPIRED` are reserved for any plugin; several plugins may publish one name.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "StructureEventName".
 */
export type StructureEventName = string;
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
 * Indicator plugin id: lower snake case, unique across installed plugins.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "PluginId".
 */
export type PluginId = string;
/**
 * Colour role resolved by the terminal theme: `side` follows the AOI side, `sign` the sign of the plotted value (histograms only), `up` and `down` are the bullish and bearish colours, `accent` the user accent. Events may not use `side` or `sign`.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "DisplayTone".
 */
export type DisplayTone =
  'side' | 'sign' | 'up' | 'down' | 'neutral' | 'accent' | 'warning' | 'muted';
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "LineStyle".
 */
export type LineStyle = 'solid' | 'dashed' | 'dotted';
/**
 * Default colour as `#RRGGBB`; user colours take precedence.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "HexColor".
 */
export type HexColor = string;
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
export type AssetClass = 'CRYPTO' | 'RWA' | 'EQUITY' | 'BOND' | 'UNKNOWN';
/**
 * Origin of a bar's buy volume. `taker`: the venue's aggressor flag (true delta). `classified`: trades classified against the prevailing quote (Lee-Ready / quote rule) on markets that publish no aggressor side, such as stock exchanges.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "BuyVolumeMethod".
 */
export type BuyVolumeMethod = 'taker' | 'classified';
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
export type CreatedAtMs = number;
export type Emitted = number;
export type Enabled = boolean;
export type HistoryBars = number;
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "JsonValue".
 */
export type JsonValue = unknown;
export type InstrumentId1 = string;
export type JobId = string;
export type LastCheckedMs = number | null;
export type LastError = string | null;
export type LastProcessedMs = number;
export type ScriptId = string;
/**
 * @maxItems 50
 */
export type SubscriptionIds = string[];
export type Version = number;
export type Enabled1 = boolean;
export type HistoryBars1 = number;
export type InstrumentId2 = string;
export type ScriptId1 = string;
/**
 * @maxItems 50
 */
export type SubscriptionIds1 = string[];
export type Version1 = number;
export type Available = boolean;
export type Currency = string;
export type DefaultLimit = number;
export type Id = string;
export type Label = string;
export type Reason = string | null;
export type Section = string;
export type Type = 'stocks' | 'etfs' | 'crypto';
export type Datasets = HeatmapDataset[];
export type Available1 = boolean;
export type Id1 = 'sector' | 'asset_class' | 'none';
export type Label1 = string;
export type Reason1 = string | null;
export type Available2 = boolean;
export type Format = 'currency' | 'percent' | 'number' | 'ratio';
export type Id2 = string;
export type Label2 = string;
export type Reason2 = string | null;
export type Scale = ('diverging' | 'sequential') | null;
export type AsOfMs = number | null;
export type Field = string;
export type Note = string | null;
export type Source = string;
export type ColorMetrics = HeatmapMetric[];
export type Currency1 = string;
export type Dataset = string;
export type DatasetLabel = string;
export type DefaultColor = string;
export type DefaultGrouping = 'sector' | 'asset_class' | 'none';
export type DefaultSize = string;
export type Demo = boolean;
export type GeneratedAtMs = number;
export type Groupings = HeatmapGrouping[];
export type Limit = number;
export type Pending = boolean;
export type PricedCount = number;
export type Provenance = HeatmapProvenance[];
export type SizeMetrics = HeatmapMetric[];
export type AsOfMs1 = number | null;
export type ChartUnavailableReason = string | null;
export type Error = string | null;
export type Exchange = string | null;
export type Group = string | null;
export type Industry = string | null;
export type InstrumentId3 = string | null;
export type Key = string;
export type Name = string | null;
export type Price = string | null;
export type Symbol = string;
export type Tiles = HeatmapTile[];
export type Type1 = 'stocks' | 'etfs' | 'crypto';
export type UniverseCount = number;
export type ScriptId2 = string;
export type Version2 = number;
export type Color = string | null;
export type TimeMs = number;
export type Value = number | null;
export type Name1 = string;
/**
 * @maxItems 2000
 */
export type Points = PlotPoint[];
export type HistoryBars2 = number;
export type MaxSymbols = number;
export type Query = string;
export type Version3 = number | null;
export type CreatedAtMs1 = number;
export type Error1 = string | null;
export type Errors = {
  [k: string]: string | undefined;
}[];
export type JobId1 = string;
export type Matches = {
  [k: string]: JsonValue | undefined;
}[];
export type Scanned = number;
export type ScriptId3 = string;
export type SourceHash = string;
export type Status =
  'pending' | 'running' | 'complete' | 'failed' | 'cancelled';
export type Total = number;
export type UniverseCount1 = number;
export type Version4 = number;
export type Message = string;
export type Name2 = string;
export type Severity = 'INFO' | 'WARNING' | 'ENTRY' | 'CRITICAL';
export type TimeMs1 = number;
export type ExpectedVersion = number | null;
export type Kind = 'indicator' | 'strategy' | 'library' | 'filter' | 'alert';
export type Name3 = string;
export type Overlay = boolean;
export type InstrumentId4 = string | null;
export type Limit1 = number;
export type ScriptId4 = string;
export type Source1 = string;
/**
 * @maxItems 2000
 */
export type Alerts = ScriptAlertEvent[];
export type Id3 = string;
export type Kind1 =
  | 'line'
  | 'polyline'
  | 'box'
  | 'label'
  | 'table'
  | 'fill'
  | 'hline'
  | 'linefill'
  | 'barcolor'
  | 'bgcolor'
  | 'shape'
  | 'candle'
  | 'bar';
/**
 * @maxItems 16000
 */
export type BarVisuals = ScriptVisual[];
export type BarsCount = number;
export type Complete = boolean;
export type DataSource = string;
/**
 * @maxItems 2000
 */
export type EquityCurve = PlotPoint[];
export type FeedState1 = string;
export type Effect = 'entry' | 'exit' | 'reversal';
export type Fee = number;
export type Price1 = number;
export type Quantity = number;
export type Side = 'buy' | 'sell';
export type TimeMs2 = number;
/**
 * @maxItems 4000
 */
export type Fills = SimulatedFill[];
export type InstrumentId5 = string;
export type Kind2 = 'indicator' | 'strategy' | 'filter' | 'alert';
export type Matched = boolean | null;
export type Overlay1 = boolean;
/**
 * @maxItems 64
 */
export type Plots = PlotSeries[];
export type ScriptId5 = string;
export type SourceHash1 = string;
export type Tf = string;
export type Version5 = number;
/**
 * @maxItems 500
 */
export type Visuals = ScriptVisual[];
export type CommissionBps = number;
export type FromMs = number | null;
export type IncludeOpen = boolean;
export type InitialEquity = number;
export type InstrumentId6 = string;
export type Limit2 = number;
export type SlippageBps = number;
export type ToMs = number | null;
export type Version6 = number | null;
export type CreatedAtMs2 = number;
export type ExpectedVersion1 = number | null;
export type Kind3 = 'indicator' | 'strategy' | 'library' | 'filter' | 'alert';
export type Name4 = string;
export type Overlay2 = boolean;
export type ScriptId6 = string;
export type Source2 = string;
export type SourceHash2 = string;
export type Version7 = number;
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
 * UNKNOWN: funding applies but is unavailable. NOT_APPLICABLE: the instrument has no funding (exchange-listed securities).
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "FundingRisk".
 */
export type FundingRisk =
  'NORMAL' | 'WATCH' | 'HIGH_RISK' | 'UNKNOWN' | 'NOT_APPLICABLE';
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
export type Severity1 = 'INFO' | 'WARNING' | 'ENTRY' | 'CRITICAL';
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
  | 'POSITION_EXITED'
  | 'CUSTOM_ALERT';
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
  | StateNode
  | InZoneNode
  | AoiTouchNode
  | AoiOverlapNode
  | EventNode
  | SequenceNode
  | CompareNode
  | InRangeNode
  | CrossesNode
  | PersistForNode
  | WithinBarsNode
  | TimeWindowNode
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
 * One trading session [open_ms, close_ms).
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "TradingSession".
 */
export interface TradingSession {
  /**
   * Exchange-local trading date.
   */
  day: string;
  open_ms: TimestampMs;
  close_ms: TimestampMs;
}
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
  /**
   * Securities only: allow execution outside regular trading hours (Interactive Brokers outsideRth; limit and stop-limit orders). Omitted when false.
   */
  outside_rth?: boolean;
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
  venue: TradingVenue;
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
  /**
   * Exchange venues this broker (or paper account) routes securities to, e.g. ibkr -> nasdaq, nyse, cboe; sinopac and cathay -> twse, tpex. Empty for crypto venues, which trade their own instruments.
   */
  tradable_venues?: Venue[];
  /**
   * Securities trade from cash: no leverage and no margin modes.
   */
  cash_account?: boolean;
  /**
   * Securities may be sold short; false means long-only (sells are limited to shares held).
   */
  short_selling?: boolean;
  /**
   * Orders may carry an outside-regular-trading-hours flag (Interactive Brokers).
   */
  outside_rth?: boolean;
  /**
   * Intraday odd-lot orders (1 to board_lot-1 shares; TWSE limit ROD only, e.g. Shioaji order_lot IntradayOdd).
   */
  odd_lot?: boolean;
  /**
   * The broker queues DAY market orders placed while the exchange is closed for the next open; Joseph additionally requires configuration to allow it.
   */
  queue_market_when_closed?: boolean;
  /**
   * The account is a broker paper/simulation environment (IBKR paper account, Shioaji simulation) or the Joseph simulator.
   */
  simulation?: boolean;
  /**
   * Settlement currencies of the account, e.g. USD or TWD.
   */
  currencies?: string[];
}
/**
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "AccountSummary".
 */
export interface AccountSummary {
  account_id: string;
  tenant_id: string;
  venue: TradingVenue;
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
  /**
   * Settled cash of a securities or paper account, when known.
   */
  settled_cash?: DecimalString | null;
  /**
   * Net cash awaiting settlement (TWSE T+2, US T+1); negative when purchases are payable.
   */
  unsettled_cash?: DecimalString | null;
  /**
   * End of the server-side LIVE arming of this account (POST /v1/accounts/{account_id}/live-consent). Manual LIVE orders (ticket, chart, quick trade) need an unexpired, unrevoked arming; `null` when the account is not armed.
   */
  live_armed_until_ms?: TimestampMs | null;
}
/**
 * Arms manual LIVE orders on one verified (LIVE-capable) account for a bounded time: `POST /v1/accounts/{account_id}/live-consent`. The step-up code is verified exactly like the AUTO_LIVE consent of a deployment. `DELETE` on the same path disarms. Both return the `AccountSummary`.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "AccountLiveConsentRequest".
 */
export interface AccountLiveConsentRequest {
  /**
   * Step-up (TOTP) code verified by the execution engine.
   */
  step_up_code: string;
  /**
   * Explicit acknowledgement that orders use real money.
   */
  acknowledge_risk: true;
  /**
   * Arming lifetime. Defaults to, and may not exceed, the server's JOSEPH_LIVE_ARM_MAX_MS (8 hours unless configured; never more than 24 hours).
   */
  duration_ms?: number;
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
  /**
   * Plugin-defined id of the structure the AOI belongs to; `IN_ZONE` groups bind on it.
   */
  group_id?: string | null;
  /**
   * Why a non-live AOI ended; null while it is live or when the plugin gives no reason.
   */
  end_reason?: EndReason | null;
  indicator_version: string;
  method: IndicatorMethod;
  meta?: {};
}
/**
 * Indicator event confirmed at a closed bar.
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
  /**
   * Published values, keyed as declared in the plugin's `output_values`.
   */
  values: {
    [k: string]: IndicatorValue | undefined;
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
 * One value a plugin publishes in `IndicatorSnapshot.values` and `IndicatorSeriesPoint.values`. Every published value may be null (for example before warm-up). `choices` is only allowed for string values.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "ValueSpec".
 */
export interface ValueSpec {
  key: string;
  /**
   * JSON form of the value: `decimal` is a decimal string, `timestamp_ms` an integer UTC ms time.
   */
  type: 'decimal' | 'integer' | 'boolean' | 'string' | 'timestamp_ms';
  unit: 'price' | 'percent' | 'ratio' | 'volume' | 'bars' | 'none';
  description: string;
  /**
   * Closed set of values of a string value; any string when empty or omitted.
   *
   * @maxItems 32
   */
  choices?: string[];
}
/**
 * How the terminal draws AOIs of one kind. `box` fills [lower, upper] from `created_at_ms`; `level` draws a line at `lower`.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "KindStyle".
 */
export interface KindStyle {
  kind: AoiKind;
  label: string;
  render: 'box' | 'level';
  tone?: DisplayTone;
  color?: HexColor | null;
  fill_opacity?: number | null;
  line_style?: LineStyle | null;
  midline?: boolean;
  /**
   * Draws live AOIs as pending (dashed) until they end.
   */
  pending_until_ended?: boolean;
}
/**
 * How the terminal draws events of one name: a marker on the bar, a segment at `price`, both, or nothing.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "EventStyle".
 */
export interface EventStyle {
  name: StructureEventName;
  label: string;
  render: 'marker' | 'segment' | 'marker_and_segment' | 'none';
  position?: 'above' | 'below' | 'at';
  shape?: 'arrow-up' | 'arrow-down' | 'circle' | 'square';
  tone?: DisplayTone;
  color?: HexColor | null;
  /**
   * `meta` key holding a UTC ms time: markers sit on that bar and segments start there.
   */
  time_meta?: string | null;
  line_style?: LineStyle | null;
}
/**
 * One published value drawn as a line or histogram, on the price pane or a pane the plugin declares.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "PlotStyle".
 */
export interface PlotStyle {
  /**
   * Key declared in `output_values`.
   */
  value: string;
  label: string;
  /**
   * `price`, or the id of a declared `PaneStyle`.
   */
  pane: string;
  render: 'line' | 'histogram';
  tone?: DisplayTone;
  color?: HexColor | null;
  line_style?: LineStyle;
  line_width?: number;
}
/**
 * Horizontal reference line in a study pane.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "PaneLevel".
 */
export interface PaneLevel {
  value: number;
  label: string;
  tone?: DisplayTone;
  line_style?: LineStyle;
}
/**
 * Study pane below the price pane.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "PaneStyle".
 */
export interface PaneStyle {
  /**
   * Pane id, unique within the plugin; `price` is reserved.
   */
  id: string;
  title: string;
  precision?: number;
  /**
   * @maxItems 8
   */
  levels?:
    | []
    | [PaneLevel]
    | [PaneLevel, PaneLevel]
    | [PaneLevel, PaneLevel, PaneLevel]
    | [PaneLevel, PaneLevel, PaneLevel, PaneLevel]
    | [PaneLevel, PaneLevel, PaneLevel, PaneLevel, PaneLevel]
    | [PaneLevel, PaneLevel, PaneLevel, PaneLevel, PaneLevel, PaneLevel]
    | [
        PaneLevel,
        PaneLevel,
        PaneLevel,
        PaneLevel,
        PaneLevel,
        PaneLevel,
        PaneLevel,
      ]
    | [
        PaneLevel,
        PaneLevel,
        PaneLevel,
        PaneLevel,
        PaneLevel,
        PaneLevel,
        PaneLevel,
        PaneLevel,
      ];
}
/**
 * Label shown for AOIs that ended with one `end_reason`.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "EndReasonStyle".
 */
export interface EndReasonStyle {
  reason: EndReason;
  label: string;
}
/**
 * Display hints the terminal renders a plugin from. Styled kinds must be declared in `output_kinds`, styled events in `output_events` (or be reserved), plotted values in `output_values`; the engine rejects a plugin that breaks these rules. Kinds and events without a style use the terminal defaults.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "PluginDisplay".
 */
export interface PluginDisplay {
  /**
   * Listed in the Layers menu and drawn on the chart; false for plugins used only by drawing tools or strategies.
   */
  layer: boolean;
  /**
   * Shown on charts that have no saved layer state.
   */
  default_visible: boolean;
  z_index: number;
  /**
   * Event markers are drawn by default.
   */
  show_events: boolean;
  kinds: KindStyle[];
  events: EventStyle[];
  plots: PlotStyle[];
  panes: PaneStyle[];
  end_reasons: EndReasonStyle[];
}
/**
 * Metadata of one installed indicator plugin: what it computes, the AOI kinds, events and values it publishes, and how the terminal displays it.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "IndicatorPluginInfo".
 */
export interface IndicatorPluginInfo {
  plugin_id: PluginId;
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
  output_values: ValueSpec[];
  display: PluginDisplay;
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
    [k: string]: IndicatorValue | undefined;
  };
}
/**
 * Per-bar values of one plugin (e.g. volume delta for a sub-pane). Values are point-in-time: each point is what the plugin published when that bar closed.
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
 * One rung of a price-dependent tick ladder: prices at or above min_price move in tick_size steps.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "TickStep".
 */
export interface TickStep {
  min_price: NonNegativeDecimalString;
  tick_size: NonNegativeDecimalString;
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
  calendar?: CalendarId;
  /**
   * Exchange that lists a security, e.g. `Nasdaq`, `NYSE Arca`, `Cboe BZX`, `TWSE`, `TPEx`; null for crypto.
   */
  listing_exchange?: string | null;
  /**
   * Price-dependent ticks (TWSE, TPEx). Empty when tick_size applies at every price.
   */
  tick_ladder?: TickStep[];
  /**
   * Daily price limit from the reference price in percent (TWSE and TPEx 10); null when unlimited.
   */
  price_limit_pct?: NonNegativeDecimalString | null;
  /**
   * Shares per board lot (TWSE and TPEx 1000); null when orders are in single units.
   */
  board_lot?: NonNegativeDecimalString | null;
  /**
   * Issuer or series name in English, e.g. `Taiwan Semiconductor Manufacturing` or `US 10-Year Treasury par yield`; null when the venue publishes none (crypto).
   */
  name?: string | null;
  /**
   * Name in the listing market's language, e.g. `台積電` on TWSE, `環球晶` on TPEx; null when none.
   */
  local_name?: string | null;
}
/**
 * Trading sessions of a calendar overlapping [from_ms, to_ms). Empty for 24x7 markets, which never close.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "SessionList".
 */
export interface SessionList {
  calendar: CalendarId;
  sessions: TradingSession[];
  from_ms: TimestampMs;
  to_ms: TimestampMs;
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
  /**
   * How taker_buy_base_volume was obtained; null when it is null.
   */
  taker_buy_method?: BuyVolumeMethod | null;
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
  price_adjustment?: PriceAdjustment;
  price_adjustment_epoch_ms?: TimestampMs;
  price_adjustment_refresh_after_ms?: TimestampMs;
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
  /**
   * 1m-and-above bars fully covered by recorded public trades carry a true delta summed from the trades' aggressor side, although the venue's klines carry none.
   */
  trade_delta?: boolean;
  /**
   * The market publishes no aggressor side (stock exchanges); buy volume is classified from trades against the prevailing quote (taker_buy_method = classified).
   */
  classified_delta?: boolean;
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
  /**
   * Subset of timeframes the engine aggregates from a shorter native timeframe that tiles it exactly (e.g. 1M from 1d where the venue publishes no monthly bars).
   */
  aggregated_timeframes?: Timeframe[];
  calendar?: CalendarId;
  /**
   * Delay of the data this deployment is entitled to (e.g. 900000 on a free consolidated feed); null when real time.
   */
  data_delay_ms?: number | null;
  /**
   * The data source needs an API key (e.g. Alpaca for Nasdaq/NYSE).
   */
  requires_credentials?: boolean;
  /**
   * Whether that key is configured on this deployment (true when none is required).
   */
  credentials_configured?: boolean;
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
 * Persistent Python alert evaluation state and visible diagnostics.
 *
 * Attributes:
 *     job_id: Opaque tenant-owned alert identifier.
 *     created_at_ms: Creation time in UTC epoch milliseconds.
 *     last_processed_ms: Latest successfully evaluated bar close/checkpoint.
 *     last_error: Visible missing-data, provider or user-code failure, if any.
 *     last_checked_ms: Last evaluation attempt in UTC epoch milliseconds.
 *     emitted: Number of distinct conditions recorded for this job.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "AlertJob".
 */
export interface AlertJob {
  created_at_ms: CreatedAtMs;
  emitted?: Emitted;
  enabled?: Enabled;
  history_bars?: HistoryBars;
  inputs?: Inputs;
  instrument_id: InstrumentId1;
  job_id: JobId;
  last_checked_ms?: LastCheckedMs;
  last_error?: LastError;
  last_processed_ms: LastProcessedMs;
  script_id: ScriptId;
  subscription_ids?: SubscriptionIds;
  tf: Timeframe;
  version: Version;
}
export interface Inputs {
  [k: string]: JsonValue | undefined;
}
/**
 * Explicitly enables a pinned Python condition on future closed bars.
 *
 * Attributes:
 *     script_id: Owned alert, indicator or strategy script publishing
 *         conditions.
 *     version: Positive immutable tenant version, pinned rather than
 *         following edits; 0 for a built-in or plugin script, whose
 *         installed source hash is pinned instead.
 *     instrument_id: Canonical instrument whose new closed bars are evaluated.
 *     tf: Source resolution, one second through one calendar month.
 *     inputs: JSON values overriding the pinned version's defaults.
 *     subscription_ids: Explicit owned notification destinations; UI is
 *         implicit.
 *     enabled: Whether evaluation and external notification routing are
 *         active.
 *     history_bars: Context depth, two through two thousand closed bars.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "AlertJobInput".
 */
export interface AlertJobInput {
  enabled?: Enabled1;
  history_bars?: HistoryBars1;
  inputs?: Inputs1;
  instrument_id: InstrumentId2;
  script_id: ScriptId1;
  subscription_ids?: SubscriptionIds1;
  tf: Timeframe;
  version: Version1;
}
export interface Inputs1 {
  [k: string]: JsonValue | undefined;
}
/**
 * Every data set and the default per heatmap family.
 *
 * Attributes:
 *     datasets: Data sets in menu order.
 *     defaults: Default data set id per heatmap family.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "HeatmapCatalog".
 */
export interface HeatmapCatalog {
  datasets: Datasets;
  defaults: Defaults;
}
/**
 * A selectable data set (an index, a market or a coin universe).
 *
 * Attributes:
 *     id: Stable id, e.g. ``us_sp500`` or ``crypto_all``.
 *     type: Heatmap family.
 *     label: Human label, e.g. ``S&P 500``.
 *     section: Menu section, e.g. ``United States``, ``Taiwan``, ``Crypto``.
 *     currency: Quote currency of prices and sizes (``USD`` or ``TWD``).
 *     default_limit: Tiles shown by default (the largest first).
 *     available: Whether the configured sources can serve it.
 *     reason: Why it is unavailable, or None.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "HeatmapDataset".
 */
export interface HeatmapDataset {
  available: Available;
  currency: Currency;
  default_limit: DefaultLimit;
  id: Id;
  label: Label;
  reason?: Reason;
  section: Section;
  type: Type;
}
export interface Defaults {
  [k: string]: string | undefined;
}
/**
 * A grouping option of the selected data set.
 *
 * Attributes:
 *     id: ``sector``, ``asset_class`` or ``none``.
 *     label: Human label, e.g. ``Sector``.
 *     available: Whether tiles carry this grouping.
 *     reason: Why the grouping is unavailable, or None.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "HeatmapGrouping".
 */
export interface HeatmapGrouping {
  available: Available1;
  id: Id1;
  label: Label1;
  reason?: Reason1;
}
/**
 * One size or colour metric and whether the source supplies it.
 *
 * Attributes:
 *     id: Stable metric id, e.g. ``market_cap`` or ``change_1d``.
 *     label: Human label, e.g. ``Change 1D, %``.
 *     format: Value format: currency amount, percent, plain number or
 *         ratio (relative volume).
 *     scale: Colour scale of a colour metric: ``diverging`` (red/grey/green)
 *         or ``sequential`` (single-hue intensity); None for size metrics.
 *     available: Whether at least the source can supply the metric.
 *     reason: Why the metric is unavailable, or None when available.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "HeatmapMetric".
 */
export interface HeatmapMetric {
  available: Available2;
  format: Format;
  id: Id2;
  label: Label2;
  reason?: Reason2;
  scale?: Scale;
}
/**
 * Where one part of the heatmap comes from.
 *
 * Attributes:
 *     field: What the source supplies, e.g. ``Sector & market cap``.
 *     source: Source label, e.g. ``Nasdaq.com screener (unofficial,
 *         delayed)``.
 *     as_of_ms: When the source data was fetched or observed, UTC epoch
 *         milliseconds, or None when it is not loaded yet.
 *     note: Optional caveat such as ``derived: shares x last price``.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "HeatmapProvenance".
 */
export interface HeatmapProvenance {
  as_of_ms?: AsOfMs;
  field: Field;
  note?: Note;
  source: Source;
}
/**
 * A heatmap of one data set with metric availability and provenance.
 *
 * Attributes:
 *     type: Heatmap family.
 *     dataset: Data set id.
 *     dataset_label: Data set label.
 *     currency: Currency of prices and currency-valued metrics.
 *     tiles: Tiles, largest default size first.
 *     universe_count: Members of the data set before the limit.
 *     limit: Requested maximum tiles.
 *     priced_count: Tiles with a price.
 *     size_metrics: Size-by options with availability.
 *     color_metrics: Colour-by options with availability.
 *     groupings: Group-by options with availability.
 *     default_size: Default size metric id.
 *     default_color: Default colour metric id.
 *     default_grouping: Default grouping id.
 *     provenance: Source of each part of the data.
 *     source_errors: Per-source failures limiting the result.
 *     pending: True while a slow source is still loading; the client
 *         should poll again soon for fuller coverage.
 *     generated_at_ms: Response time, UTC epoch milliseconds.
 *     demo: True for synthetic DEMO data.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "HeatmapSnapshot".
 */
export interface HeatmapSnapshot {
  color_metrics: ColorMetrics;
  currency: Currency1;
  dataset: Dataset;
  dataset_label: DatasetLabel;
  default_color: DefaultColor;
  default_grouping: DefaultGrouping;
  default_size: DefaultSize;
  demo?: Demo;
  generated_at_ms: GeneratedAtMs;
  groupings: Groupings;
  limit: Limit;
  pending?: Pending;
  priced_count: PricedCount;
  provenance: Provenance;
  size_metrics: SizeMetrics;
  source_errors?: SourceErrors;
  tiles: Tiles;
  type: Type1;
  universe_count: UniverseCount;
}
export interface SourceErrors {
  [k: string]: string | undefined;
}
/**
 * One symbol of the map.
 *
 * Attributes:
 *     key: Unique key within the data set (ticker or coin id).
 *     symbol: Display ticker.
 *     name: Company, fund or coin name, or None.
 *     group: Sector (stocks), asset class (ETFs) or None (crypto).
 *     industry: Industry within the sector, or None.
 *     exchange: Listing exchange, or None.
 *     instrument_id: Canonical instrument to chart, or None when no
 *         configured venue lists it.
 *     chart_unavailable_reason: Why no chart can open, or None.
 *     price: Last price as a decimal string, or None.
 *     metrics: Available metric values by metric id (decimal strings);
 *         a missing key means the value is unavailable for this symbol.
 *     as_of_ms: Quote observation or fetch time, or None.
 *     error: Per-symbol source problem, or None.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "HeatmapTile".
 */
export interface HeatmapTile {
  as_of_ms?: AsOfMs1;
  chart_unavailable_reason?: ChartUnavailableReason;
  error?: Error;
  exchange?: Exchange;
  group?: Group;
  industry?: Industry;
  instrument_id?: InstrumentId3;
  key: Key;
  metrics?: Metrics;
  name?: Name;
  price?: Price;
  symbol: Symbol;
}
export interface Metrics {
  [k: string]: string | undefined;
}
/**
 * Pins one reusable library to an immutable tenant-owned version.
 *
 * Attributes:
 *     script_id: Tenant-local library slug.
 *     version: Positive immutable version number; never resolves latest.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "LibraryRef".
 */
export interface LibraryRef {
  script_id: ScriptId2;
  version: Version2;
}
/**
 * One script-produced scalar at a closed bar.
 *
 * Attributes:
 *     time_ms: Bar start in UTC epoch milliseconds.
 *     value: Finite numeric output, or None during warm-up.
 *     color: Optional literal per-bar hex color for conditional plot coloring.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "PlotPoint".
 */
export interface PlotPoint {
  color?: Color;
  time_ms: TimeMs;
  value: Value;
}
/**
 * Named output series displayed by the terminal.
 *
 * Attributes:
 *     name: Public series name, one to sixty-four characters.
 *     points: At most two thousand chronological closed-bar values.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "PlotSeries".
 */
export interface PlotSeries {
  name: Name1;
  points: Points;
}
/**
 * Selects a real market universe for an isolated Python filter job.
 *
 * Attributes:
 *     version: Optional immutable filter version; defaults to the latest.
 *     venue: Exchange/market venue to scan, or all enabled venues.
 *     market_type: Optional instrument market category, such as stock or perp.
 *     query: Optional symbol/name search within that market.
 *     max_symbols: Explicit maximum instruments, one through ten thousand.
 *     history_bars: Closed bars per instrument; zero uses snapshots only.
 *     tf: Resolution used when historical bars are requested.
 *     inputs: JSON values overriding the pinned filter's defaults.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "ScanInput".
 */
export interface ScanInput {
  history_bars?: HistoryBars2;
  inputs?: Inputs2;
  market_type?: MarketType | null;
  max_symbols?: MaxSymbols;
  query?: Query;
  tf?: Timeframe;
  venue?: Venue | null;
  version?: Version3;
}
export interface Inputs2 {
  [k: string]: JsonValue | undefined;
}
/**
 * Progress and reproducible results for an asynchronous market scan.
 *
 * Attributes:
 *     job_id: Opaque tenant-owned scan identifier.
 *     script_id: Filter script slug.
 *     version: Immutable filter version pinned at submission.
 *     status: Pending, running, complete, failed or cancelled job state.
 *     created_at_ms: Submission time, UTC epoch milliseconds.
 *     total: Number of eligible instruments selected by the explicit scan
 *         bound.
 *     scanned: Instruments evaluated or reported unavailable so far.
 *     matches: Matching instrument records with real source snapshots.
 *     errors: Per-instrument missing-data or user-code errors.
 *     error: Job-level error, otherwise None.
 *     source_hash: Immutable source/dependency configuration hash.
 *     universe_count: Total eligible market instruments before max_symbols.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "ScanJob".
 */
export interface ScanJob {
  created_at_ms: CreatedAtMs1;
  error?: Error1;
  errors?: Errors;
  job_id: JobId1;
  matches?: Matches;
  scanned?: Scanned;
  script_id: ScriptId3;
  source_hash: SourceHash;
  status: Status;
  total?: Total;
  universe_count?: UniverseCount1;
  version: Version4;
}
/**
 * One user-authored alert condition matched on a source bar.
 *
 * Attributes:
 *     name: Stable condition name used for per-bar delivery deduplication.
 *     message: Plain-text message, at most five hundred characters.
 *     time_ms: Source bar start, UTC epoch milliseconds.
 *     severity: Notification priority used by delivery filters and quiet
 *         hours.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "ScriptAlertEvent".
 */
export interface ScriptAlertEvent {
  message: Message;
  name: Name2;
  severity?: Severity;
  time_ms: TimeMs1;
}
/**
 * Creates an immutable script version with explicit type and dependencies.
 *
 * Attributes:
 *     script_id: Stable tenant-local slug; must match the path on updates.
 *     kind: One of the five script roles; immutable after creation.
 *     name: Display title, one to one hundred twenty characters.
 *     source: UTF-8 Python source, at most sixty-four KiB.
 *     overlay: Whether indicator/strategy plots share the main price pane.
 *     defaults: Default JSON input values overridden by an individual run.
 *     libraries: Python import aliases mapped to pinned library versions.
 *     expected_version: Optimistic lock for edits; required for a new version.
 *     requests: Up to four declared cross-symbol/timeframe source aliases.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "ScriptInput".
 */
export interface ScriptInput {
  defaults?: Defaults1;
  expected_version?: ExpectedVersion;
  kind: Kind;
  libraries?: Libraries;
  name: Name3;
  overlay?: Overlay;
  requests?: Requests;
  script_id: ScriptId4;
  source: Source1;
}
export interface Defaults1 {
  [k: string]: JsonValue | undefined;
}
export interface Libraries {
  [k: string]: LibraryRef | undefined;
}
export interface Requests {
  [k: string]: SeriesRequest | undefined;
}
/**
 * Declares another symbol/resolution before isolated indicator execution.
 *
 * Attributes:
 *     instrument_id: Optional canonical symbol; defaults to the chart symbol.
 *     tf: Requested resolution, with confirmed-bar visibility enforced by
 *         host.
 *     limit: Maximum closed source bars, one through five hundred.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "SeriesRequest".
 */
export interface SeriesRequest {
  instrument_id?: InstrumentId4;
  limit?: Limit1;
  tf: Timeframe;
}
/**
 * Reproducible plots and optional simulated strategy performance.
 *
 * Attributes:
 *     script_id: Tenant-owned script slug.
 *     version: Immutable script version evaluated.
 *     kind: Indicator, strategy, filter or alert; libraries are import-only.
 *     source_hash: Content hash including default inputs and dependency pins.
 *     instrument_id: Canonical source instrument identifier.
 *     tf: Source bar resolution.
 *     bars_count: Number of source bars evaluated, including provisional bars
 *         only when explicitly requested for an indicator.
 *     feed_state: Source provenance; DEMO data remains explicitly marked.
 *     data_source: Upstream provider provenance label.
 *     complete: Whether the requested history has no closed-bar gaps.
 *     overlay: Whether plots share the main price pane.
 *     plots: At most sixty-four scalar output series.
 *     fills: Simulated next-bar-open fills; empty for indicators.
 *     equity_curve: Marked simulated quote equity; empty for indicators.
 *     metrics: Finite numeric simulation statistics, including fees and
 *         unfilled final-bar intents; no performance is fabricated.
 *     matched: Boolean decision for a filter, otherwise None.
 *     alerts: Previewed alert events; delivery requires an enabled alert job.
 *     visuals: Bounded declarative drawings, fills, coloring and tables.
 *     bar_closes: Confirmed source bar starts mapped to actual evaluated
 *         closes;
 *         alert checkpoints never consult a later, different market window.
 *     plot_styles: Per-series input/style settings used by the renderer.
 *     bar_visuals: Per-bar colors, symbols and custom OHLC series, separately
 *         bounded from persistent drawing objects.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "ScriptResult".
 */
export interface ScriptResult {
  alerts?: Alerts;
  bar_closes?: BarCloses;
  bar_visuals?: BarVisuals;
  bars_count: BarsCount;
  complete: Complete;
  data_source: DataSource;
  equity_curve: EquityCurve;
  feed_state: FeedState1;
  fills: Fills;
  instrument_id: InstrumentId5;
  kind: Kind2;
  matched?: Matched;
  metrics: Metrics1;
  overlay: Overlay1;
  plot_styles?: PlotStyles;
  plots: Plots;
  script_id: ScriptId5;
  source_hash: SourceHash1;
  tf: Tf;
  version: Version5;
  visuals?: Visuals;
}
export interface BarCloses {
  [k: string]: number | undefined;
}
/**
 * A bounded declarative drawing; never executable browser code.
 *
 * Attributes:
 *     id: Stable visual id unique within this script instance.
 *     kind: Plot/drawing category interpreted by the chart renderer.
 *     data: JSON coordinates, text and presentation; renderers read only
 *         documented fields and never inject HTML, CSS URLs or JavaScript.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "ScriptVisual".
 */
export interface ScriptVisual {
  data: Data;
  id: Id3;
  kind: Kind1;
}
export interface Data {
  [k: string]: JsonValue | undefined;
}
/**
 * An isolated strategy simulation fill, never an executable broker order.
 *
 * Attributes:
 *     time_ms: Fill bar start in UTC epoch milliseconds.
 *     side: Buy or sell after converting the signed position target.
 *     quantity: Positive base-asset quantity filled.
 *     price: Fill price including adverse slippage, in quote/base units.
 *     fee: Nonnegative commission in quote units.
 *     effect: Entry, exit or reversal for unambiguous chart trade markers.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "SimulatedFill".
 */
export interface SimulatedFill {
  effect?: Effect;
  fee: Fee;
  price: Price1;
  quantity: Quantity;
  side: Side;
  time_ms: TimeMs2;
}
export interface Metrics1 {
  [k: string]: number | undefined;
}
export interface PlotStyles {
  [k: string]:
    | {
        [k: string]: JsonValue | undefined;
      }
    | undefined;
}
/**
 * Requests a reproducible run using closed, real backend candles.
 *
 * Attributes:
 *     version: Pinned script version, or latest when omitted.
 *     instrument_id: Canonical venue:market:symbol identifier.
 *     tf: Supported chart timeframe, one second through one calendar month.
 *     limit: Maximum closed bars, from two through two thousand.
 *     from_ms: Optional inclusive UTC epoch millisecond lower boundary.
 *     to_ms: Optional exclusive UTC epoch millisecond visibility boundary.
 *     include_open: Evaluate the provisional last bar for indicators only;
 *         alerts and simulated orders always require closed bars.
 *     inputs: JSON input values overriding the version's defaults.
 *     initial_equity: Positive initial simulated cash in quote units.
 *     commission_bps: Commission in basis points per simulated fill.
 *     slippage_bps: Adverse simulated slippage in basis points per fill.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "ScriptRun".
 */
export interface ScriptRun {
  commission_bps?: CommissionBps;
  from_ms?: FromMs;
  include_open?: IncludeOpen;
  initial_equity?: InitialEquity;
  inputs?: Inputs3;
  instrument_id: InstrumentId6;
  limit?: Limit2;
  slippage_bps?: SlippageBps;
  tf: Timeframe;
  to_ms?: ToMs;
  version?: Version6;
}
export interface Inputs3 {
  [k: string]: JsonValue | undefined;
}
/**
 * A tenant-visible immutable, content-addressed script version.
 *
 * Attributes:
 *     version: Positive monotonically increasing version number.
 *     source_hash: SHA-256 of the source and dependency/input configuration.
 *     created_at_ms: Creation time in UTC epoch milliseconds.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "ScriptVersion".
 */
export interface ScriptVersion {
  created_at_ms: CreatedAtMs2;
  defaults?: Defaults2;
  expected_version?: ExpectedVersion1;
  kind: Kind3;
  libraries?: Libraries1;
  name: Name4;
  overlay?: Overlay2;
  requests?: Requests1;
  script_id: ScriptId6;
  source: Source2;
  source_hash: SourceHash2;
  version: Version7;
}
export interface Defaults2 {
  [k: string]: JsonValue | undefined;
}
export interface Libraries1 {
  [k: string]: LibraryRef | undefined;
}
export interface Requests1 {
  [k: string]: SeriesRequest | undefined;
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
  /**
   * Currency of turnover thresholds is the listing currency (TWD on TWSE).
   */
  unit: 'USD' | 'TWD' | 'ratio' | 'pct';
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
  /**
   * NONE denotes a directionless Python alert.
   */
  direction: 'LONG' | 'SHORT' | 'NONE';
  timeframe: Timeframe;
  kind: SignalKind;
  setup_state: SetupState;
  severity: Severity1;
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
  /**
   * Zone groups (AOI `group_id`s) bound by true `IN_ZONE` conditions.
   */
  group_ids?: string[];
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
  bypass_severities?: Severity1[];
}
/**
 * Where and when a tenant receives signal notifications. Channel credentials stay on the server. Browser registrations are write-only and omitted from responses.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "AlertSubscription".
 */
export interface AlertSubscription {
  subscription_id: string;
  channel: 'TELEGRAM' | 'LINE' | 'EMAIL' | 'WEBHOOK' | 'WHATSAPP' | 'WEB_PUSH';
  /**
   * Telegram chat id, WhatsApp E.164 phone number, Web Push device identifier, LINE user id, e-mail address or HTTPS webhook URL.
   */
  target: string;
  /**
   * Empty means every kind.
   */
  kinds: SignalKind[];
  min_severity: Severity1;
  /**
   * Empty means every strategy.
   */
  strategy_ids: string[];
  enabled: boolean;
  quiet_hours?: QuietHours | null;
  /**
   * Private browser PushSubscription input. Never returned. Required for initial WEB_PUSH registration.
   */
  web_push?: {
    /**
     * HTTPS endpoint on a supported browser push service.
     */
    endpoint: string;
    keys: {
      /**
       * Base64url uncompressed P-256 public encryption key.
       */
      p256dh: string;
      /**
       * Base64url 16-byte authentication secret; write only.
       */
      auth: string;
    };
  };
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
    | 'UI'
    | 'TELEGRAM'
    | 'LINE'
    | 'EMAIL'
    | 'WEBHOOK'
    | 'EXECUTION'
    | 'WHATSAPP'
    | 'WEB_PUSH'
  )[];
  /**
   * Public VAPID application server key, or null when push is not configured.
   */
  web_push_public_key?: string | null;
}
/**
 * Tenant delivery progress. Delivered means provider acceptance, not receipt or read confirmation.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "AlertDelivery".
 */
export interface AlertDelivery {
  /**
   * Opaque durable outbox identifier.
   */
  delivery_id: string;
  /**
   * Notification channel.
   */
  channel: string;
  /**
   * Pending retry, provider accepted, or permanently failed.
   */
  status: 'PENDING' | 'DELIVERED' | 'DEAD';
  /**
   * Completed delivery attempts.
   */
  attempts: number;
  /**
   * Redacted last failure, if any.
   */
  last_error: string | null;
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
 * Indicator state on one or more timeframes, read only from bars closed at or before the evaluated bar close. A timeframe counts when every `where` clause matches the latest warm snapshot of `plugin_id`; a null or missing value does not match, and a missing snapshot is UNKNOWN. match=ALL requires every timeframe; K_OF_N requires k of them.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "StateNode".
 */
export interface StateNode {
  op: 'STATE';
  id?: string;
  label?: string;
  plugin_id: PluginId;
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
  /**
   * @minItems 1
   * @maxItems 8
   */
  where:
    | [ValueMatch]
    | [ValueMatch, ValueMatch]
    | [ValueMatch, ValueMatch, ValueMatch]
    | [ValueMatch, ValueMatch, ValueMatch, ValueMatch]
    | [ValueMatch, ValueMatch, ValueMatch, ValueMatch, ValueMatch]
    | [ValueMatch, ValueMatch, ValueMatch, ValueMatch, ValueMatch, ValueMatch]
    | [
        ValueMatch,
        ValueMatch,
        ValueMatch,
        ValueMatch,
        ValueMatch,
        ValueMatch,
        ValueMatch,
      ]
    | [
        ValueMatch,
        ValueMatch,
        ValueMatch,
        ValueMatch,
        ValueMatch,
        ValueMatch,
        ValueMatch,
        ValueMatch,
      ];
  match?: 'ALL' | 'K_OF_N';
  k?: number;
}
/**
 * One STATE clause: the published value `field` equals one of `any_of`, compared with the same JSON type (a string never equals a boolean).
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "ValueMatch".
 */
export interface ValueMatch {
  field: string;
  /**
   * @minItems 1
   * @maxItems 16
   */
  any_of:
    | [string | boolean]
    | [string | boolean, string | boolean]
    | [string | boolean, string | boolean, string | boolean]
    | [string | boolean, string | boolean, string | boolean, string | boolean]
    | [
        string | boolean,
        string | boolean,
        string | boolean,
        string | boolean,
        string | boolean,
      ]
    | [
        string | boolean,
        string | boolean,
        string | boolean,
        string | boolean,
        string | boolean,
        string | boolean,
      ]
    | [
        string | boolean,
        string | boolean,
        string | boolean,
        string | boolean,
        string | boolean,
        string | boolean,
        string | boolean,
      ]
    | [
        string | boolean,
        string | boolean,
        string | boolean,
        string | boolean,
        string | boolean,
        string | boolean,
        string | boolean,
        string | boolean,
      ]
    | [
        string | boolean,
        string | boolean,
        string | boolean,
        string | boolean,
        string | boolean,
        string | boolean,
        string | boolean,
        string | boolean,
        string | boolean,
      ]
    | [
        string | boolean,
        string | boolean,
        string | boolean,
        string | boolean,
        string | boolean,
        string | boolean,
        string | boolean,
        string | boolean,
        string | boolean,
        string | boolean,
      ]
    | [
        string | boolean,
        string | boolean,
        string | boolean,
        string | boolean,
        string | boolean,
        string | boolean,
        string | boolean,
        string | boolean,
        string | boolean,
        string | boolean,
        string | boolean,
      ]
    | [
        string | boolean,
        string | boolean,
        string | boolean,
        string | boolean,
        string | boolean,
        string | boolean,
        string | boolean,
        string | boolean,
        string | boolean,
        string | boolean,
        string | boolean,
        string | boolean,
      ]
    | [
        string | boolean,
        string | boolean,
        string | boolean,
        string | boolean,
        string | boolean,
        string | boolean,
        string | boolean,
        string | boolean,
        string | boolean,
        string | boolean,
        string | boolean,
        string | boolean,
        string | boolean,
      ]
    | [
        string | boolean,
        string | boolean,
        string | boolean,
        string | boolean,
        string | boolean,
        string | boolean,
        string | boolean,
        string | boolean,
        string | boolean,
        string | boolean,
        string | boolean,
        string | boolean,
        string | boolean,
        string | boolean,
      ]
    | [
        string | boolean,
        string | boolean,
        string | boolean,
        string | boolean,
        string | boolean,
        string | boolean,
        string | boolean,
        string | boolean,
        string | boolean,
        string | boolean,
        string | boolean,
        string | boolean,
        string | boolean,
        string | boolean,
        string | boolean,
      ]
    | [
        string | boolean,
        string | boolean,
        string | boolean,
        string | boolean,
        string | boolean,
        string | boolean,
        string | boolean,
        string | boolean,
        string | boolean,
        string | boolean,
        string | boolean,
        string | boolean,
        string | boolean,
        string | boolean,
        string | boolean,
        string | boolean,
      ];
}
/**
 * The price is inside [lower, upper] of a live AOI of `kinds` (and `side`, when given) detected on `tf`, read from the snapshots of the plugins that publish those kinds. Nodes sharing a `group` must agree on the AOI `group_id`: the binding is the intersection of the group ids among each member's live candidate AOIs, and a member is TRUE only when a containing AOI's group id is in the binding. A missing member snapshot makes the binding UNKNOWN.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "InZoneNode".
 */
export interface InZoneNode {
  op: 'IN_ZONE';
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
  side?: 'bullish' | 'bearish';
  tf: Timeframe;
  price_source?: 'close' | 'high' | 'low' | 'hl2';
  group?: string;
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
 * An indicator event confirmed within the last `within_bars` closed bars (0 = current bar).
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "EventNode".
 */
export interface EventNode {
  op: 'EVENT';
  id?: string;
  label?: string;
  name: StructureEventName;
  source?: PluginId;
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
  source?: PluginId;
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
 * The evaluated bar starts, in `time_zone` local time, inside one of `windows`, on one of `weekdays` (ISO local weekday, 1 = Monday; every day when omitted).
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "TimeWindowNode".
 */
export interface TimeWindowNode {
  op: 'TIME_WINDOW';
  id?: string;
  label?: string;
  /**
   * IANA time zone, e.g. America/New_York.
   */
  time_zone: string;
  /**
   * @minItems 1
   * @maxItems 8
   */
  windows:
    | [LocalTimeWindow]
    | [LocalTimeWindow, LocalTimeWindow]
    | [LocalTimeWindow, LocalTimeWindow, LocalTimeWindow]
    | [LocalTimeWindow, LocalTimeWindow, LocalTimeWindow, LocalTimeWindow]
    | [
        LocalTimeWindow,
        LocalTimeWindow,
        LocalTimeWindow,
        LocalTimeWindow,
        LocalTimeWindow,
      ]
    | [
        LocalTimeWindow,
        LocalTimeWindow,
        LocalTimeWindow,
        LocalTimeWindow,
        LocalTimeWindow,
        LocalTimeWindow,
      ]
    | [
        LocalTimeWindow,
        LocalTimeWindow,
        LocalTimeWindow,
        LocalTimeWindow,
        LocalTimeWindow,
        LocalTimeWindow,
        LocalTimeWindow,
      ]
    | [
        LocalTimeWindow,
        LocalTimeWindow,
        LocalTimeWindow,
        LocalTimeWindow,
        LocalTimeWindow,
        LocalTimeWindow,
        LocalTimeWindow,
        LocalTimeWindow,
      ];
  /**
   * @minItems 1
   * @maxItems 7
   */
  weekdays?:
    | [number]
    | [number, number]
    | [number, number, number]
    | [number, number, number, number]
    | [number, number, number, number, number]
    | [number, number, number, number, number, number]
    | [number, number, number, number, number, number, number];
}
/**
 * Local-time window [start, end); an end at or before the start wraps past midnight.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "LocalTimeWindow".
 */
export interface LocalTimeWindow {
  start: string;
  end: string;
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
  /**
   * NEAREST_AOI targets the nearest live AOI of `target_kinds` beyond the entry (its lower bound above a LONG entry, its upper bound below a SHORT entry); FIXED_R targets `fixed_r` times the risk.
   */
  target_model: 'NEAREST_AOI' | 'FIXED_R';
  /**
   * AOI kinds NEAREST_AOI may target.
   *
   * @minItems 1
   * @maxItems 8
   */
  target_kinds?:
    | [AoiKind]
    | [AoiKind, AoiKind]
    | [AoiKind, AoiKind, AoiKind]
    | [AoiKind, AoiKind, AoiKind, AoiKind]
    | [AoiKind, AoiKind, AoiKind, AoiKind, AoiKind]
    | [AoiKind, AoiKind, AoiKind, AoiKind, AoiKind, AoiKind]
    | [AoiKind, AoiKind, AoiKind, AoiKind, AoiKind, AoiKind, AoiKind]
    | [AoiKind, AoiKind, AoiKind, AoiKind, AoiKind, AoiKind, AoiKind, AoiKind];
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
  channels?: (
    'UI' | 'TELEGRAM' | 'LINE' | 'EMAIL' | 'WEBHOOK' | 'WHATSAPP' | 'WEB_PUSH'
  )[];
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
 * Payload of snapshot_required: the resume point cannot be replayed, because last_sequence is older than the retention window or newer than the newest buffered sequence (a server restart). The client refetches REST snapshots of the listed channels.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "SnapshotRequiredPayload".
 */
export interface SnapshotRequiredPayload {
  /**
   * Human-readable cause, e.g. that the resume point is older than retention or that the server restarted.
   */
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
  /**
   * Stroke width in CSS pixels (1..50), including broad highlighter strokes.
   */
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
  /**
   * True when explicitly removed; hidden layers retain their settings.
   */
  removed?: boolean;
  /**
   * Retains the legend row when a user hides an added study.
   */
  present?: boolean;
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
  /**
   * Tab content; omitted means candles for existing workspaces.
   */
  view_type?: 'candles' | 'heatmap';
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
   * Saved chart tabs without a fixed count limit; layout controls visible panes.
   *
   * @minItems 1
   */
  charts: [ChartState, ...ChartState[]];
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
/**
 * Drawings of one exact instrument inside a workspace export.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "WorkspaceExportDrawings".
 */
export interface WorkspaceExportDrawings {
  instrument_id: InstrumentId;
  /**
   * @maxItems 2000
   */
  drawings: DrawingDocument[];
  /**
   * Stored drawing payloads this build cannot read (for example a newer drawing schema). Exported and restored verbatim, never deleted (ADR 0007).
   *
   * @maxItems 500
   */
  opaque?: {}[];
}
/**
 * Workspace export file, format version 2: the workspace document (schema v1, unchanged) plus the drawings of its instruments. Import also accepts a plain v1 WorkspaceDocument (export format 1, no drawings) and the V4 starter shape (format 0), migrated step by step; drawings of unknown tools are kept.
 *
 * This interface was referenced by `JosephContracts`'s JSON-Schema
 * via the `definition` "WorkspaceExport".
 */
export interface WorkspaceExport {
  format: 'joseph.workspace-export';
  schema_version: 2;
  exported_at_ms: TimestampMs;
  workspace: WorkspaceDocument;
  /**
   * @maxItems 500
   */
  drawings: WorkspaceExportDrawings[];
}
