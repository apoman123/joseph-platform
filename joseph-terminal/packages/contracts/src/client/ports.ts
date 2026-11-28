/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Client ports for the Joseph backends.
 *
 * Features depend on these interfaces only. The composition root injects
 * HTTP implementations (live) or `@joseph/mock-provider` implementations
 * (DEMO). Write operations exist only on the authenticated execution and
 * strategy clients; there is no browser path that signs or sends venue
 * orders directly.
 */

import type {
  AlertJob,
  AlertJobInput,
  ScanInput,
  ScanJob,
  ScriptVersion,
  Signal,
  AccountList,
  AccountLiveConsentRequest,
  AlertSubscription,
  AlertDelivery,
  AlertSubscriptionList,
  AccountSummary,
  ActivateRequest,
  AmendRequest,
  AoiList,
  AuditEventList,
  BacktestReport,
  BacktestRequest,
  CandleBatch,
  ChannelSpec,
  ClosePositionRequest,
  Deployment,
  DeploymentList,
  DeploymentRequest,
  FillList,
  IndicatorPluginList,
  IndicatorSeries,
  IndicatorSnapshotList,
  InstrumentList,
  KillSwitchList,
  KillSwitchRequest,
  KillSwitchState,
  MarketSnapshot,
  MarketType,
  Order,
  OrderBook,
  OrderIntentRequest,
  OrderList,
  OrderPreview,
  OrderState,
  PositionList,
  SessionList,
  SignalKind,
  SignalList,
  StrategyDefinition,
  StrategyVersion,
  StrategyVersionList,
  StreamEnvelope,
  SubmitIntentResponse,
  Timeframe,
  ValidationResult,
  Venue,
  VenueStatusList,
} from '../generated/contracts';
import type {ExchangeCalendarId} from '../sessions';

/** A private source catalog with explicit runtime availability. */
export interface PythonCatalog {
  /** Latest immutable versions visible to the authenticated tenant. */
  scripts: ScriptVersion[];
  /** Whether the operator enabled the isolated container runtime. */
  runtime_enabled: boolean;
  /** Next bounded catalog window, or null at the end. */
  next_offset?: number | null;
}

/** One declared input of a catalog script (`joseph_script.Input`). */
export interface CatalogInput {
  /** JSON default value. */
  default: unknown;
  /** Settings label; the input name when empty. */
  title: string;
  /** Inclusive numeric lower bound, or null. */
  minimum: number | null;
  /** Inclusive numeric upper bound, or null. */
  maximum: number | null;
  /** Accepted values shown as a choice list, or null. */
  options: unknown[] | null;
  /** Numeric spinner step, or null. */
  step: number | null;
  /** Settings section, or empty. */
  group: string;
  /** Help text, or empty. */
  tooltip: string;
}

/** A built-in or installed plugin script (`GET /v1/scripts/catalog`). */
export interface CatalogScript {
  /** `builtin:<id>` or `plugin:<entry point>:<id>`. */
  script_id: string;
  /** Script role. */
  kind: ScriptVersion['kind'];
  /** Built-in platform script or operator-installed plugin. */
  origin: 'builtin' | 'plugin';
  /** Display name. */
  title: string;
  /** Compact legend name. */
  short_title: string;
  /** Catalog description. */
  description: string;
  /** Catalog section such as `Price`. */
  category: string;
  /** Declared inputs in display order. */
  inputs: Record<string, CatalogInput>;
  /** Identity of the installed code (pinned by alert jobs). */
  source_hash: string;
  /** False when disabled by configuration. */
  enabled: boolean;
  /** Market data a filter loads beyond the snapshot. */
  metrics?: string[];
}

/** Catalog listing. */
export interface CatalogList {
  /** Scripts in catalog order. */
  scripts: CatalogScript[];
}

/**
 * Market filters (scans) and alert conditions over built-in, plugin and
 * owned scripts. The HTTP implementation is `PythonClient`; DEMO mode has
 * an offline implementation.
 */
export interface ScriptsClient {
  /**
   * Lists built-in and plugin scripts.
   * @param kind Optional role filter.
   * @return Catalog listing.
   */
  catalog(kind?: CatalogScript['kind']): Promise<CatalogList>;
  /**
   * Lists the caller's own scripts.
   * @param offset Nonnegative offset.
   * @return Owned latest versions and runtime availability.
   */
  list(offset?: number): Promise<PythonCatalog>;
  /**
   * Starts a scan of conditions combined with AND.
   * @param body Market, bounds and conditions.
   * @return Pending job.
   */
  screen(body: ScanInput): Promise<ScanJob>;
  /**
   * Reads scan progress and results.
   * @param id Job id.
   * @return Current job state.
   */
  scanStatus(id: string): Promise<ScanJob>;
  /**
   * Cancels a scan, keeping partial results.
   * @param id Job id.
   * @return Job state.
   */
  cancelScan(id: string): Promise<ScanJob>;
  /**
   * Lists alert jobs.
   * @return Owned alert jobs.
   */
  alerts(): Promise<AlertJob[]>;
  /**
   * Creates or replaces an alert job.
   * @param body Condition script, symbol, inputs and destinations.
   * @param id Job to replace, when editing.
   * @return Saved job.
   */
  saveAlert(body: AlertJobInput, id?: string): Promise<AlertJob>;
  /**
   * Deletes an alert job, keeping its log.
   * @param id Job id.
   * @return Resolves after removal.
   */
  deleteAlert(id: string): Promise<void>;
  /**
   * Lists the alert log: signals of kind `CUSTOM_ALERT`, newest first.
   * @param limit Maximum rows.
   * @return Alert signals.
   */
  alertLog(limit?: number): Promise<Signal[]>;
}

/** Query for the instrument registry. */
export interface InstrumentQuery {
  /** Restrict to one venue. */
  venue?: Venue;
  /** Restrict to one market type. */
  marketType?: MarketType;
  /** Free-text search on symbol/base, e.g. `BTC`. */
  q?: string;
  /** Maximum results (the engine serves up to 5000; default 500). */
  limit?: number;
}

/** Query for historical candles. */
export interface CandleQuery {
  /** Optional signal that cancels obsolete history reads and their body. */
  signal?: AbortSignal;
  /** Canonical instrument id. */
  instrumentId: string;
  /** Bar resolution. */
  timeframe: Timeframe;
  /** Inclusive start (UTC ms). */
  fromMs?: number;
  /** Exclusive end (UTC ms). */
  toMs?: number;
  /** Maximum number of bars (server caps apply). */
  limit?: number;
}

/** Query for indicator snapshots or areas of interest (AOIs) on one series. */
export interface SeriesQuery {
  /** Canonical instrument id. */
  instrumentId: string;
  /** Series timeframe. */
  timeframe: Timeframe;
  /** Optional plugin ids; all plugins when omitted. */
  plugins?: readonly string[];
  /** Optional point-in-time (UTC ms); latest when omitted. */
  atMs?: number;
  /**
   * Optional per-plugin input overrides, e.g. `{ema: {length: 50}}`. The
   * backend validates them against each plugin's input schema.
   */
  settings?: Readonly<Record<string, Readonly<Record<string, unknown>>>>;
}

/** Query for per-bar values of one plugin. */
export interface IndicatorSeriesQuery {
  /** Canonical instrument id. */
  instrumentId: string;
  /** Series timeframe. */
  timeframe: Timeframe;
  /** Plugin id, e.g. `volume_delta`. */
  pluginId: string;
  /** Inclusive start (UTC ms); a default window when omitted. */
  fromMs?: number;
  /** Optional input overrides for this plugin. */
  settings?: Readonly<Record<string, unknown>>;
}

/** Query for the trading sessions of an exchange calendar. */
export interface SessionQuery {
  /** Exchange calendar (never `24x7`, which has no sessions). */
  calendar: ExchangeCalendarId;
  /** Inclusive start (UTC ms). */
  fromMs: number;
  /** Exclusive end (UTC ms); the engine serves windows up to ~400 days. */
  toMs: number;
}

/** Query for the signal history. */
export interface SignalQuery {
  /** Restrict to one strategy. */
  strategyId?: string;
  /** Restrict to signal kinds. */
  kinds?: readonly SignalKind[];
  /** Restrict to one instrument. */
  instrumentId?: string;
  /** Inclusive lower bound on bar close (UTC ms). */
  fromMs?: number;
  /** Exclusive upper bound on bar close (UTC ms). */
  toMs?: number;
  /** Maximum rows. */
  limit?: number;
}

/** Read-only public market data and backend indicator outputs. */
export interface MarketDataClient {
  /**
   * Lists venues with negotiated capabilities and live feed health.
   * @return Venue status list.
   */
  listVenues(): Promise<VenueStatusList>;
  /**
   * Searches the instrument registry.
   * @param query Filters.
   * @return Matching instruments.
   */
  listInstruments(query: InstrumentQuery): Promise<InstrumentList>;
  /**
   * Fetches historical candles with provenance and gaps.
   * @param query Series and range.
   * @return Candle batch.
   */
  getCandles(query: CandleQuery): Promise<CandleBatch>;
  /**
   * Fetches the latest market snapshot.
   * @param instrumentId Canonical instrument id.
   * @return Snapshot (stale flag set when old).
   */
  getSnapshot(instrumentId: string): Promise<MarketSnapshot>;
  /**
   * Fetches the reconciled order book.
   * @param instrumentId Canonical instrument id.
   * @param depth Levels per side.
   * @return Order book.
   */
  getOrderBook(instrumentId: string, depth?: number): Promise<OrderBook>;
  /**
   * Lists indicator plugins exposed by the backend.
   * @return Plugin descriptors.
   */
  listIndicatorPlugins(): Promise<IndicatorPluginList>;
  /**
   * Fetches indicator snapshots for a series.
   * @param query Series and plugin filter.
   * @return Snapshots.
   */
  getIndicatorSnapshots(query: SeriesQuery): Promise<IndicatorSnapshotList>;
  /**
   * Fetches AOIs for a series.
   * @param query Series and optional point-in-time.
   * @return AOI list.
   */
  getAois(query: SeriesQuery): Promise<AoiList>;
  /**
   * Fetches per-bar published values of one plugin (sub-pane data).
   * @param query Series, plugin and optional range/settings.
   * @return Point-in-time values per closed bar.
   */
  getIndicatorSeries(query: IndicatorSeriesQuery): Promise<IndicatorSeries>;
  /**
   * Lists the trading sessions of an exchange calendar overlapping a window
   * (`GET /v1/calendars/{calendar_id}/sessions`). Optional: without it the
   * terminal uses the regular weekday hours of the calendar.
   * @param query Calendar and window.
   * @return Session list.
   */
  getSessions?(query: SessionQuery): Promise<SessionList>;
}

/** Authenticated strategy authoring, backtests and signal history. */
export interface StrategyClient {
  /**
   * Lists the tenant's strategies (latest version of each).
   * @return Strategy versions.
   */
  listStrategies(): Promise<StrategyVersionList>;
  /**
   * Lists every version of one strategy.
   * @param strategyId Strategy slug.
   * @return Versions, newest first.
   */
  getStrategy(strategyId: string): Promise<StrategyVersionList>;
  /**
   * Validates a definition without storing it.
   * @param definition Rule AST v2 definition.
   * @return Validation result.
   */
  validate(definition: StrategyDefinition): Promise<ValidationResult>;
  /**
   * Stores a new strategy as version 1.
   * @param definition Rule AST v2 definition.
   * @return Stored immutable version.
   */
  createStrategy(definition: StrategyDefinition): Promise<StrategyVersion>;
  /**
   * Stores a new immutable version of an existing strategy.
   * @param strategyId Strategy slug.
   * @param definition Edited definition.
   * @return Stored immutable version.
   */
  createVersion(
    strategyId: string,
    definition: StrategyDefinition
  ): Promise<StrategyVersion>;
  /**
   * Runs a backtest with the production evaluator.
   * @param strategyId Strategy slug.
   * @param request Version, instrument and range.
   * @return Backtest report.
   */
  backtest(
    strategyId: string,
    request: BacktestRequest
  ): Promise<BacktestReport>;
  /**
   * Activates a version in ALERT_ONLY, PAPER or SHADOW mode.
   * @param strategyId Strategy slug.
   * @param request Version and mode.
   * @return Updated version.
   */
  activate(
    strategyId: string,
    request: ActivateRequest
  ): Promise<StrategyVersion>;
  /**
   * Pauses the active version.
   * @param strategyId Strategy slug.
   * @return Updated version.
   */
  pause(strategyId: string): Promise<StrategyVersion>;
  /**
   * Lists signal history.
   * @param query Filters.
   * @return Signals, newest first.
   */
  listSignals(query: SignalQuery): Promise<SignalList>;
  /**
   * Lists the caller's notification subscriptions and the channels the
   * server can deliver to.
   * @return Subscriptions.
   */
  listAlertSubscriptions(): Promise<AlertSubscriptionList>;
  /**
   * Creates or replaces one notification subscription.
   * @param subscription Subscription (its id is the key).
   * @return Stored subscription.
   */
  putAlertSubscription(
    subscription: AlertSubscription
  ): Promise<AlertSubscription>;
  /**
   * Removes an owned notification destination.
   * @param subscriptionId Public subscription id.
   * @return Completion after deletion.
   */
  deleteAlertSubscription(subscriptionId: string): Promise<void>;
  /**
   * Queues a notification-only test, without producing a trading signal.
   * @param subscriptionId Enabled destination id.
   * @return Durable delivery status for polling.
   */
  testAlertSubscription(subscriptionId: string): Promise<AlertDelivery>;
  /**
   * Reads tenant-scoped provider delivery progress.
   * @param deliveryId Opaque durable delivery id.
   * @return Redacted delivery metadata; acceptance does not imply receipt.
   */
  getAlertDelivery(deliveryId: string): Promise<AlertDelivery>;
}

/** Authenticated execution trust boundary: accounts, orders, risk. */
export interface ExecutionClient {
  /**
   * Lists connected accounts for the tenant.
   * @return Account summaries.
   */
  listAccounts(): Promise<AccountList>;
  /**
   * Fetches one account summary.
   * @param accountId Account id.
   * @return Summary with positions and open orders.
   */
  getAccountSummary(accountId: string): Promise<AccountSummary>;
  /**
   * Arms manual LIVE orders on a verified account until a server-chosen
   * time (`POST /v1/accounts/{id}/live-consent`). The execution engine
   * verifies the step-up code exactly like deployment AUTO_LIVE consent.
   * @param accountId Account id.
   * @param request Step-up code, risk acknowledgement and lifetime.
   * @return Summary whose `live_armed_until_ms` is the arming end.
   */
  grantAccountLiveConsent(
    accountId: string,
    request: AccountLiveConsentRequest
  ): Promise<AccountSummary>;
  /**
   * Disarms LIVE on an account (`DELETE /v1/accounts/{id}/live-consent`).
   * @param accountId Account id.
   * @return Summary with `live_armed_until_ms` null.
   */
  revokeAccountLiveConsent(accountId: string): Promise<AccountSummary>;
  /**
   * Previews an order: rounding, fees, margin and the full risk verdict.
   * @param request Draft intent.
   * @return Preview including a short-lived preview token.
   */
  previewOrder(request: OrderIntentRequest): Promise<OrderPreview>;
  /**
   * Submits an order intent. Repeating the idempotency key replays the
   * original response instead of creating a second order.
   * @param request Intent confirmed by the user (with preview token).
   * @param idempotencyKey Client-generated unique key per user action.
   * @return Intent, risk decision and resulting orders.
   */
  submitIntent(
    request: OrderIntentRequest,
    idempotencyKey: string
  ): Promise<SubmitIntentResponse>;
  /**
   * Lists orders.
   * @param accountId Optional account filter.
   * @param states Optional state filter.
   * @return Orders.
   */
  listOrders(
    accountId?: string,
    states?: readonly OrderState[]
  ): Promise<OrderList>;
  /**
   * Requests cancellation; the final state comes from the venue.
   * @param orderId Order id.
   * @return Order in CANCEL_PENDING or its terminal state.
   */
  cancelOrder(orderId: string): Promise<Order>;
  /**
   * Requests an amendment after server-side re-validation.
   * @param orderId Order id.
   * @param request New price/quantity and expected version.
   * @return Updated order.
   */
  amendOrder(orderId: string, request: AmendRequest): Promise<Order>;
  /**
   * Lists positions.
   * @param accountId Optional account filter.
   * @return Positions.
   */
  listPositions(accountId?: string): Promise<PositionList>;
  /**
   * Lists fills.
   * @param accountId Optional account filter.
   * @return Fills, newest first.
   */
  listFills(accountId?: string): Promise<FillList>;
  /**
   * Previews a reduce-only close of a position.
   * @param positionId Position id.
   * @param request Fraction and order type.
   * @return Preview with token.
   */
  closePositionPreview(
    positionId: string,
    request: ClosePositionRequest
  ): Promise<OrderPreview>;
  /**
   * Closes (part of) a position with a reduce-only order.
   * @param positionId Position id.
   * @param request Fraction, order type and preview token.
   * @param idempotencyKey Unique key per user action.
   * @return Submission result.
   */
  closePosition(
    positionId: string,
    request: ClosePositionRequest,
    idempotencyKey: string
  ): Promise<SubmitIntentResponse>;
  /**
   * Lists kill-switch states.
   * @return Kill switches.
   */
  getKillSwitches(): Promise<KillSwitchList>;
  /**
   * Engages or releases a kill switch.
   * @param request Scope and options.
   * @return New state.
   */
  setKillSwitch(request: KillSwitchRequest): Promise<KillSwitchState>;
  /**
   * Lists audit events with chain verification.
   * @param accountId Optional account filter.
   * @param limit Maximum rows.
   * @return Audit events.
   */
  listAuditEvents(accountId?: string, limit?: number): Promise<AuditEventList>;
  /**
   * Lists strategy deployments.
   * @return Deployments.
   */
  listDeployments(): Promise<DeploymentList>;
  /**
   * Creates a deployment (AUTO_LIVE starts in PENDING_CONSENT).
   * @param request Strategy version, account, limits and sizing.
   * @return Deployment.
   */
  createDeployment(request: DeploymentRequest): Promise<Deployment>;
  /**
   * Disables a deployment.
   * @param deploymentId Deployment id.
   * @return Updated deployment.
   */
  disableDeployment(deploymentId: string): Promise<Deployment>;
}

/** Connection status of a stream client. */
export type StreamStatus = 'CONNECTING' | 'OPEN' | 'RECONNECTING' | 'CLOSED';

/** Handler for stream envelopes. */
export type StreamHandler = (envelope: StreamEnvelope) => void;

/**
 * Resumable WebSocket stream. Implementations resubscribe after reconnects
 * with `last_sequence` and surface `snapshot_required` to handlers so they
 * refetch REST snapshots.
 */
export interface StreamClient {
  /**
   * Subscribes to channels.
   * @param channels Channel specs.
   * @param handler Receives envelopes for these channels.
   * @return Unsubscribe function.
   */
  subscribe(
    channels: readonly ChannelSpec[],
    handler: StreamHandler
  ): () => void;
  /**
   * Observes connection status.
   * @param handler Receives status changes.
   * @return Unsubscribe function.
   */
  onStatus(handler: (status: StreamStatus) => void): () => void;
  /** Closes the connection and stops reconnecting. */
  close(): void;
}

/** All backend clients injected into features. */
export interface BackendClients {
  /** Public market data. */
  market: MarketDataClient;
  /** Market-data stream. */
  marketStream: StreamClient;
  /** Strategy authoring and signals. */
  strategy: StrategyClient;
  /** Execution trust boundary. */
  execution: ExecutionClient;
  /** Authenticated user stream (orders, fills, positions, signals). */
  userStream: StreamClient;
  /** Market filter scans and alert jobs over built-in and owned scripts. */
  scripts: ScriptsClient;
  /** Whether these clients serve synthetic DEMO data. */
  demo: boolean;
}
