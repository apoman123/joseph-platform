/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Fetch-based HTTP implementations of the client ports.
 *
 * These clients carry only a short-lived bearer session token. Venue API
 * keys and signing material never exist in the browser.
 */

import type {
  AccountList,
  AccountLiveConsentRequest,
  AlertSubscription,
  AlertSubscriptionList,
  AccountSummary,
  ActivateRequest,
  AmendRequest,
  AoiList,
  AuditEventList,
  BacktestReport,
  BacktestRequest,
  CandleBatch,
  ClosePositionRequest,
  Deployment,
  DeploymentList,
  DeploymentRequest,
  ErrorResponse,
  FillList,
  IndicatorPluginList,
  IndicatorSeries,
  IndicatorSnapshotList,
  InstrumentList,
  KillSwitchList,
  KillSwitchRequest,
  KillSwitchState,
  MarketSnapshot,
  Order,
  OrderBook,
  OrderIntentRequest,
  OrderList,
  OrderPreview,
  OrderState,
  PositionList,
  QualificationReport,
  QualificationStatus,
  SessionList,
  SignalList,
  StrategyDefinition,
  StrategyVersion,
  StrategyVersionList,
  SubmitIntentResponse,
  ValidationResult,
  Venue,
  VenueStatusList,
} from '../generated/contracts';
import type {
  CandleQuery,
  ExecutionClient,
  IndicatorSeriesQuery,
  InstrumentQuery,
  MarketDataClient,
  SeriesQuery,
  SessionQuery,
  SignalQuery,
  StrategyClient,
} from './ports';

/** Error raised for non-2xx responses, carrying the contract error body. */
export class ApiError extends Error {
  /**
   * Creates an API error.
   * @param status HTTP status code.
   * @param body Parsed contract error body.
   */
  constructor(
    readonly status: number,
    readonly body: ErrorResponse
  ) {
    super(`${body.code}: ${body.message}`);
    this.name = 'ApiError';
  }
}

/** Options for an HTTP client. */
export interface HttpClientOptions {
  /** Base URL such as `http://localhost:8000`. */
  baseUrl: string;
  /** Returns the current bearer token, or null when signed out. */
  getToken?: () => string | null;
  /** Fetch implementation (injectable for tests). */
  fetchImpl?: typeof fetch;
}

/** Query parameter values accepted by `request`; arrays go comma-separated. */
type QueryValue = string | number | readonly string[];

/**
 * Minimal JSON HTTP transport shared by the typed clients.
 */
export class HttpTransport {
  private readonly baseUrl: string;
  private readonly fetchImpl: typeof fetch;

  /**
   * Creates a transport.
   * @param options Base URL, token provider and fetch implementation.
   */
  constructor(private readonly options: HttpClientOptions) {
    this.baseUrl = options.baseUrl.replace(/\/$/, '');
    // A detached `fetch` throws "Illegal invocation" in browsers.
    this.fetchImpl = options.fetchImpl ?? fetch.bind(globalThis);
  }

  /**
   * Sends a JSON request and parses the JSON response.
   * @param method HTTP method.
   * @param path Path beginning with `/`.
   * @param query Optional query parameters (undefined values are skipped).
   * @param body Optional JSON body.
   * @param headers Optional extra headers.
   * @return Parsed response body, typed by the caller and not validated;
   *     `{}` for an empty body.
   * @throws `ApiError` for a non-2xx response. The promise also rejects
   *     with the `fetch` error when the request fails, and with a
   *     `SyntaxError` when the body is not JSON.
   */
  async request<T>(
    method: 'GET' | 'POST' | 'PUT' | 'DELETE',
    path: string,
    query?: Record<string, QueryValue | undefined>,
    body?: unknown,
    headers?: Record<string, string>
  ): Promise<T> {
    const url = new URL(`${this.baseUrl}${path}`);
    for (const [key, value] of Object.entries(query ?? {})) {
      if (value === undefined) continue;
      url.searchParams.set(
        key,
        Array.isArray(value) ? value.join(',') : String(value)
      );
    }
    const token = this.options.getToken?.() ?? null;
    const response = await this.fetchImpl(url.toString(), {
      method,
      cache: 'no-store',
      headers: {
        Accept: 'application/json',
        ...(body === undefined ? {} : {'Content-Type': 'application/json'}),
        ...(token ? {Authorization: `Bearer ${token}`} : {}),
        ...headers,
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const text = await response.text();
    const parsed: unknown = text ? JSON.parse(text) : {};
    if (!response.ok) {
      throw new ApiError(
        response.status,
        toErrorResponse(parsed, response.status)
      );
    }
    // The engines answer with the contract type of the route; the caller
    // names it, and the body is not validated here.
    return parsed as T;
  }
}

/**
 * Encodes per-plugin setting overrides as a compact JSON query value.
 * @param settings Overrides keyed by plugin id.
 * @return JSON text, or undefined when there are none.
 */
function encodeSettings(
  settings:
    Readonly<Record<string, Readonly<Record<string, unknown>>>> | undefined
): string | undefined {
  return settings && Object.keys(settings).length > 0
    ? JSON.stringify(settings)
    : undefined;
}

/**
 * Normalizes any error payload into the contract `ErrorResponse`.
 * @param payload Parsed body.
 * @param status HTTP status.
 * @return The payload when it has `code` and `message`; otherwise an
 *     `UNAUTHENTICATED` (401) or `INTERNAL` error, retryable for 5xx.
 */
function toErrorResponse(payload: unknown, status: number): ErrorResponse {
  if (
    payload &&
    typeof payload === 'object' &&
    'code' in payload &&
    'message' in payload
  ) {
    // A body with `code` and `message` is taken to be the engines' contract
    // error; anything else (a proxy page, an empty body) is replaced below.
    return payload as ErrorResponse;
  }
  return {
    code: status === 401 ? 'UNAUTHENTICATED' : 'INTERNAL',
    message: `HTTP ${status}`,
    trace_id: 'unknown',
    retryable: status >= 500,
    field_errors: [],
  };
}

/**
 * HTTP implementation of `MarketDataClient` (market engine). Every method
 * rejects with `ApiError` for a non-2xx response.
 */
export class HttpMarketDataClient implements MarketDataClient {
  private readonly http: HttpTransport;

  /**
   * Creates the client.
   * @param options Transport options for the market engine.
   */
  constructor(options: HttpClientOptions) {
    this.http = new HttpTransport(options);
  }

  /**
   * Lists venues (`GET /v1/venues`).
   * @return Venue status list.
   */
  listVenues(): Promise<VenueStatusList> {
    return this.http.request('GET', '/v1/venues');
  }

  /**
   * Searches instruments (`GET /v1/instruments`).
   * @param query Filters.
   * @return Instruments.
   */
  listInstruments(query: InstrumentQuery): Promise<InstrumentList> {
    return this.http.request('GET', '/v1/instruments', {
      venue: query.venue,
      market_type: query.marketType,
      q: query.q,
      limit: query.limit,
    });
  }

  /**
   * Fetches candles (`GET /v1/candles`).
   * @param query Series and range.
   * @return Candle batch.
   */
  getCandles(query: CandleQuery): Promise<CandleBatch> {
    return this.http.request('GET', '/v1/candles', {
      instrument_id: query.instrumentId,
      tf: query.timeframe,
      from_ms: query.fromMs,
      to_ms: query.toMs,
      limit: query.limit,
    });
  }

  /**
   * Fetches a snapshot (`GET /v1/market/snapshot`).
   * @param instrumentId Canonical instrument id.
   * @return Snapshot.
   */
  getSnapshot(instrumentId: string): Promise<MarketSnapshot> {
    return this.http.request('GET', '/v1/market/snapshot', {
      instrument_id: instrumentId,
    });
  }

  /**
   * Fetches the order book (`GET /v1/market/orderbook`).
   * @param instrumentId Canonical instrument id.
   * @param depth Levels per side.
   * @return Order book.
   */
  getOrderBook(instrumentId: string, depth?: number): Promise<OrderBook> {
    return this.http.request('GET', '/v1/market/orderbook', {
      instrument_id: instrumentId,
      depth,
    });
  }

  /**
   * Fetches qualification results (`GET /v1/market/qualification`).
   * @param venue Optional venue filter.
   * @param status Optional status filter.
   * @return Qualification report.
   */
  getQualification(
    venue?: Venue,
    status?: QualificationStatus
  ): Promise<QualificationReport> {
    return this.http.request('GET', '/v1/market/qualification', {
      venue,
      status,
    });
  }

  /**
   * Lists indicator plugins (`GET /v1/indicators/plugins`).
   * @return Plugins.
   */
  listIndicatorPlugins(): Promise<IndicatorPluginList> {
    return this.http.request('GET', '/v1/indicators/plugins');
  }

  /**
   * Fetches indicator snapshots (`GET /v1/indicators/snapshot`).
   * @param query Series, optional plugin ids, point in time and settings.
   * @return Snapshots.
   */
  getIndicatorSnapshots(query: SeriesQuery): Promise<IndicatorSnapshotList> {
    return this.http.request('GET', '/v1/indicators/snapshot', {
      instrument_id: query.instrumentId,
      tf: query.timeframe,
      set: query.plugins,
      at_ms: query.atMs,
      settings: encodeSettings(query.settings),
    });
  }

  /**
   * Fetches AOIs (`GET /v1/aois`).
   * @param query Series, optional plugin ids, point in time and settings.
   * @return AOIs.
   */
  getAois(query: SeriesQuery): Promise<AoiList> {
    return this.http.request('GET', '/v1/aois', {
      instrument_id: query.instrumentId,
      tf: query.timeframe,
      set: query.plugins,
      at_ms: query.atMs,
      settings: encodeSettings(query.settings),
    });
  }

  /**
   * Fetches per-bar values of one plugin (`GET /v1/indicators/series`).
   * @param query Series, plugin and optional start and settings.
   * @return Indicator series.
   */
  getIndicatorSeries(query: IndicatorSeriesQuery): Promise<IndicatorSeries> {
    return this.http.request('GET', '/v1/indicators/series', {
      instrument_id: query.instrumentId,
      tf: query.timeframe,
      plugin: query.pluginId,
      from_ms: query.fromMs,
      settings: query.settings
        ? JSON.stringify({[query.pluginId]: query.settings})
        : undefined,
    });
  }

  /**
   * Lists the trading sessions of an exchange calendar
   * (`GET /v1/calendars/{calendar_id}/sessions`).
   * @param query Calendar and window.
   * @return Session list.
   */
  getSessions(query: SessionQuery): Promise<SessionList> {
    return this.http.request(
      'GET',
      `/v1/calendars/${encodeURIComponent(query.calendar)}/sessions`,
      {from_ms: query.fromMs, to_ms: query.toMs}
    );
  }
}

/**
 * HTTP implementation of `StrategyClient` (market engine, authenticated).
 * Every method rejects with `ApiError` for a non-2xx response.
 */
export class HttpStrategyClient implements StrategyClient {
  private readonly http: HttpTransport;

  /**
   * Creates the client.
   * @param options Transport options for the market engine.
   */
  constructor(options: HttpClientOptions) {
    this.http = new HttpTransport(options);
  }

  /**
   * Lists strategies (`GET /v1/strategies`).
   * @return The latest version of each strategy.
   */
  listStrategies(): Promise<StrategyVersionList> {
    return this.http.request('GET', '/v1/strategies');
  }

  /**
   * Lists versions of one strategy (`GET /v1/strategies/{strategy_id}`).
   * @param strategyId Strategy slug.
   * @return Versions, newest first.
   */
  getStrategy(strategyId: string): Promise<StrategyVersionList> {
    return this.http.request(
      'GET',
      `/v1/strategies/${encodeURIComponent(strategyId)}`
    );
  }

  /**
   * Validates a definition (`POST /v1/strategies/validate`).
   * @param definition Rule AST v2 definition.
   * @return Validation result.
   */
  validate(definition: StrategyDefinition): Promise<ValidationResult> {
    return this.http.request(
      'POST',
      '/v1/strategies/validate',
      undefined,
      definition
    );
  }

  /**
   * Creates a strategy (`POST /v1/strategies`).
   * @param definition Rule AST v2 definition.
   * @return Version 1.
   */
  createStrategy(definition: StrategyDefinition): Promise<StrategyVersion> {
    return this.http.request('POST', '/v1/strategies', undefined, definition);
  }

  /**
   * Creates a new version (`POST /v1/strategies/{strategy_id}/versions`).
   * @param strategyId Strategy slug.
   * @param definition Edited definition.
   * @return New version.
   */
  createVersion(
    strategyId: string,
    definition: StrategyDefinition
  ): Promise<StrategyVersion> {
    return this.http.request(
      'POST',
      `/v1/strategies/${encodeURIComponent(strategyId)}/versions`,
      undefined,
      definition
    );
  }

  /**
   * Runs a backtest (`POST /v1/strategies/{strategy_id}/backtests`).
   * @param strategyId Strategy slug.
   * @param request Version, instrument and range.
   * @return Report.
   */
  backtest(
    strategyId: string,
    request: BacktestRequest
  ): Promise<BacktestReport> {
    return this.http.request(
      'POST',
      `/v1/strategies/${encodeURIComponent(strategyId)}/backtests`,
      undefined,
      request
    );
  }

  /**
   * Activates a version (`POST /v1/strategies/{strategy_id}/activate`).
   * @param strategyId Strategy slug.
   * @param request Version and mode.
   * @return Updated version.
   */
  activate(
    strategyId: string,
    request: ActivateRequest
  ): Promise<StrategyVersion> {
    return this.http.request(
      'POST',
      `/v1/strategies/${encodeURIComponent(strategyId)}/activate`,
      undefined,
      request
    );
  }

  /**
   * Pauses a strategy (`POST /v1/strategies/{strategy_id}/pause`).
   * @param strategyId Strategy slug.
   * @return Updated version.
   */
  pause(strategyId: string): Promise<StrategyVersion> {
    return this.http.request(
      'POST',
      `/v1/strategies/${encodeURIComponent(strategyId)}/pause`,
      undefined,
      {}
    );
  }

  /**
   * Lists signals (`GET /v1/signals`).
   * @param query Filters.
   * @return Signals.
   */
  listSignals(query: SignalQuery): Promise<SignalList> {
    return this.http.request('GET', '/v1/signals', {
      strategy_id: query.strategyId,
      kind: query.kinds,
      instrument_id: query.instrumentId,
      from_ms: query.fromMs,
      to_ms: query.toMs,
      limit: query.limit,
    });
  }

  /**
   * Lists notification subscriptions (`GET /v1/alerts/subscriptions`).
   * @return Subscriptions and configured channels.
   */
  listAlertSubscriptions(): Promise<AlertSubscriptionList> {
    return this.http.request('GET', '/v1/alerts/subscriptions');
  }

  /**
   * Creates or replaces a notification subscription
   * (`PUT /v1/alerts/subscriptions/{subscription_id}`).
   * @param subscription Subscription; its id is the key.
   * @return Stored subscription.
   */
  putAlertSubscription(
    subscription: AlertSubscription
  ): Promise<AlertSubscription> {
    return this.http.request(
      'PUT',
      `/v1/alerts/subscriptions/${encodeURIComponent(subscription.subscription_id)}`,
      undefined,
      subscription
    );
  }
}

/**
 * HTTP implementation of `ExecutionClient` (execution engine). Every method
 * rejects with `ApiError` for a non-2xx response.
 */
export class HttpExecutionClient implements ExecutionClient {
  private readonly http: HttpTransport;

  /**
   * Creates the client.
   * @param options Transport options for the execution engine.
   */
  constructor(options: HttpClientOptions) {
    this.http = new HttpTransport(options);
  }

  /**
   * Lists accounts (`GET /v1/accounts`).
   * @return Accounts.
   */
  listAccounts(): Promise<AccountList> {
    return this.http.request('GET', '/v1/accounts');
  }

  /**
   * Fetches an account summary (`GET /v1/accounts/{account_id}/summary`).
   * @param accountId Account id.
   * @return Summary.
   */
  getAccountSummary(accountId: string): Promise<AccountSummary> {
    return this.http.request(
      'GET',
      `/v1/accounts/${encodeURIComponent(accountId)}/summary`
    );
  }

  /**
   * Arms manual LIVE orders on an account
   * (`POST /v1/accounts/{account_id}/live-consent`); the engine verifies
   * the step-up code.
   * @param accountId Account id.
   * @param request Step-up code, acknowledgement and lifetime.
   * @return Summary with `live_armed_until_ms`.
   */
  grantAccountLiveConsent(
    accountId: string,
    request: AccountLiveConsentRequest
  ): Promise<AccountSummary> {
    return this.http.request(
      'POST',
      `/v1/accounts/${encodeURIComponent(accountId)}/live-consent`,
      undefined,
      request
    );
  }

  /**
   * Disarms LIVE on an account
   * (`DELETE /v1/accounts/{account_id}/live-consent`).
   * @param accountId Account id.
   * @return Summary with `live_armed_until_ms` null.
   */
  revokeAccountLiveConsent(accountId: string): Promise<AccountSummary> {
    return this.http.request(
      'DELETE',
      `/v1/accounts/${encodeURIComponent(accountId)}/live-consent`
    );
  }

  /**
   * Previews an order (`POST /v1/orders/preview`).
   * @param request Draft intent.
   * @return Preview with a short-lived preview token.
   */
  previewOrder(request: OrderIntentRequest): Promise<OrderPreview> {
    return this.http.request('POST', '/v1/orders/preview', undefined, request);
  }

  /**
   * Submits an intent (`POST /v1/orders/intents`).
   * @param request Intent confirmed by the user, with its preview token.
   * @param idempotencyKey Unique key per user action, sent as the
   *     `Idempotency-Key` header.
   * @return Intent, risk decision and resulting orders.
   */
  submitIntent(
    request: OrderIntentRequest,
    idempotencyKey: string
  ): Promise<SubmitIntentResponse> {
    return this.http.request('POST', '/v1/orders/intents', undefined, request, {
      'Idempotency-Key': idempotencyKey,
    });
  }

  /**
   * Lists orders (`GET /v1/orders`).
   * @param accountId Optional account filter.
   * @param states Optional state filter.
   * @return Orders.
   */
  listOrders(
    accountId?: string,
    states?: readonly OrderState[]
  ): Promise<OrderList> {
    return this.http.request('GET', '/v1/orders', {
      account_id: accountId,
      state: states,
    });
  }

  /**
   * Cancels an order (`POST /v1/orders/{order_id}/cancel`).
   * @param orderId Order id.
   * @return Order in CANCEL_PENDING or its terminal state.
   */
  cancelOrder(orderId: string): Promise<Order> {
    return this.http.request(
      'POST',
      `/v1/orders/${encodeURIComponent(orderId)}/cancel`,
      undefined,
      {}
    );
  }

  /**
   * Amends an order (`POST /v1/orders/{order_id}/amend`).
   * @param orderId Order id.
   * @param request New price/quantity and expected version.
   * @return Updated order.
   */
  amendOrder(orderId: string, request: AmendRequest): Promise<Order> {
    return this.http.request(
      'POST',
      `/v1/orders/${encodeURIComponent(orderId)}/amend`,
      undefined,
      request
    );
  }

  /**
   * Lists positions (`GET /v1/positions`).
   * @param accountId Optional account filter.
   * @return Positions.
   */
  listPositions(accountId?: string): Promise<PositionList> {
    return this.http.request('GET', '/v1/positions', {account_id: accountId});
  }

  /**
   * Lists fills (`GET /v1/fills`).
   * @param accountId Optional account filter.
   * @return Fills.
   */
  listFills(accountId?: string): Promise<FillList> {
    return this.http.request('GET', '/v1/fills', {account_id: accountId});
  }

  /**
   * Previews a close (`POST /v1/positions/{position_id}/close-preview`).
   * @param positionId Position id.
   * @param request Fraction and order type.
   * @return Preview with token.
   */
  closePositionPreview(
    positionId: string,
    request: ClosePositionRequest
  ): Promise<OrderPreview> {
    return this.http.request(
      'POST',
      `/v1/positions/${encodeURIComponent(positionId)}/close-preview`,
      undefined,
      request
    );
  }

  /**
   * Closes a position (`POST /v1/positions/{position_id}/close`).
   * @param positionId Position id.
   * @param request Fraction, order type and preview token.
   * @param idempotencyKey Unique key per user action, sent as the
   *     `Idempotency-Key` header.
   * @return Submission result.
   */
  closePosition(
    positionId: string,
    request: ClosePositionRequest,
    idempotencyKey: string
  ): Promise<SubmitIntentResponse> {
    return this.http.request(
      'POST',
      `/v1/positions/${encodeURIComponent(positionId)}/close`,
      undefined,
      request,
      {'Idempotency-Key': idempotencyKey}
    );
  }

  /**
   * Lists kill switches (`GET /v1/risk/kill-switch`).
   * @return Kill switches.
   */
  getKillSwitches(): Promise<KillSwitchList> {
    return this.http.request('GET', '/v1/risk/kill-switch');
  }

  /**
   * Sets a kill switch (`POST /v1/risk/kill-switch`).
   * @param request Scope and options.
   * @return New state.
   */
  setKillSwitch(request: KillSwitchRequest): Promise<KillSwitchState> {
    return this.http.request(
      'POST',
      '/v1/risk/kill-switch',
      undefined,
      request
    );
  }

  /**
   * Lists audit events (`GET /v1/audit/events`).
   * @param accountId Optional account filter.
   * @param limit Maximum rows.
   * @return Events.
   */
  listAuditEvents(accountId?: string, limit?: number): Promise<AuditEventList> {
    return this.http.request('GET', '/v1/audit/events', {
      account_id: accountId,
      limit,
    });
  }

  /**
   * Lists deployments (`GET /v1/deployments`).
   * @return Deployments.
   */
  listDeployments(): Promise<DeploymentList> {
    return this.http.request('GET', '/v1/deployments');
  }

  /**
   * Creates a deployment (`POST /v1/deployments`).
   * @param request Strategy version, account, limits and sizing.
   * @return Deployment.
   */
  createDeployment(request: DeploymentRequest): Promise<Deployment> {
    return this.http.request('POST', '/v1/deployments', undefined, request);
  }

  /**
   * Disables a deployment (`POST /v1/deployments/{deployment_id}/disable`).
   * @param deploymentId Deployment id.
   * @return Updated deployment.
   */
  disableDeployment(deploymentId: string): Promise<Deployment> {
    return this.http.request(
      'POST',
      `/v1/deployments/${encodeURIComponent(deploymentId)}/disable`,
      undefined,
      {}
    );
  }
}
