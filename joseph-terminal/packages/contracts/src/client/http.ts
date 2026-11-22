/**
 * @fileoverview Fetch-based HTTP implementations of the client ports.
 *
 * These clients carry only a short-lived bearer session token. Venue API
 * keys and signing material never exist in the browser.
 */
import type {
  AccountList,
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
  InstrumentQuery,
  MarketDataClient,
  SeriesQuery,
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

/** Query parameter values accepted by `request`. */
type QueryValue = string | number | readonly string[] | undefined;

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
    this.fetchImpl = options.fetchImpl ?? fetch.bind(globalThis);
  }

  /**
   * Sends a JSON request and parses the JSON response.
   * @param method HTTP method.
   * @param path Path beginning with `/`.
   * @param query Optional query parameters (undefined values are skipped).
   * @param body Optional JSON body.
   * @param headers Optional extra headers.
   * @return Parsed response body.
   * @throws {ApiError} For non-2xx responses.
   */
  async request<T>(
    method: 'GET' | 'POST' | 'PUT' | 'DELETE',
    path: string,
    query?: Record<string, QueryValue>,
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
    return parsed as T;
  }
}

/**
 * Normalizes any error payload into the contract `ErrorResponse`.
 * @param payload Parsed body.
 * @param status HTTP status.
 * @return Contract error body.
 */
function toErrorResponse(payload: unknown, status: number): ErrorResponse {
  if (
    payload &&
    typeof payload === 'object' &&
    'code' in payload &&
    'message' in payload
  ) {
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

/** HTTP implementation of `MarketDataClient` (market engine). */
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
   * Lists venues.
   * @return Venue status list.
   */
  listVenues(): Promise<VenueStatusList> {
    return this.http.request('GET', '/v1/venues');
  }

  /**
   * Searches instruments.
   * @param query Filters.
   * @return Instruments.
   */
  listInstruments(query: InstrumentQuery): Promise<InstrumentList> {
    return this.http.request('GET', '/v1/instruments', {
      venue: query.venue,
      market_type: query.marketType,
      q: query.q,
    });
  }

  /**
   * Fetches candles.
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
   * Fetches a snapshot.
   * @param instrumentId Canonical id.
   * @return Snapshot.
   */
  getSnapshot(instrumentId: string): Promise<MarketSnapshot> {
    return this.http.request('GET', '/v1/market/snapshot', {
      instrument_id: instrumentId,
    });
  }

  /**
   * Fetches the order book.
   * @param instrumentId Canonical id.
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
   * Fetches qualification results.
   * @param venue Optional venue.
   * @param status Optional status.
   * @return Report.
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
   * Lists indicator plugins.
   * @return Plugins.
   */
  listIndicatorPlugins(): Promise<IndicatorPluginList> {
    return this.http.request('GET', '/v1/indicators/plugins');
  }

  /**
   * Fetches indicator snapshots.
   * @param query Series query.
   * @return Snapshots.
   */
  getIndicatorSnapshots(query: SeriesQuery): Promise<IndicatorSnapshotList> {
    return this.http.request('GET', '/v1/indicators/snapshot', {
      instrument_id: query.instrumentId,
      tf: query.timeframe,
      set: query.plugins,
      at_ms: query.atMs,
    });
  }

  /**
   * Fetches AOIs.
   * @param query Series query.
   * @return AOIs.
   */
  getAois(query: SeriesQuery): Promise<AoiList> {
    return this.http.request('GET', '/v1/aois', {
      instrument_id: query.instrumentId,
      tf: query.timeframe,
      set: query.plugins,
      at_ms: query.atMs,
    });
  }
}

/** HTTP implementation of `StrategyClient` (market engine, authenticated). */
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
   * Lists strategies.
   * @return Versions.
   */
  listStrategies(): Promise<StrategyVersionList> {
    return this.http.request('GET', '/v1/strategies');
  }

  /**
   * Lists versions of one strategy.
   * @param strategyId Slug.
   * @return Versions.
   */
  getStrategy(strategyId: string): Promise<StrategyVersionList> {
    return this.http.request(
      'GET',
      `/v1/strategies/${encodeURIComponent(strategyId)}`
    );
  }

  /**
   * Validates a definition.
   * @param definition Definition.
   * @return Result.
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
   * Creates a strategy.
   * @param definition Definition.
   * @return Version 1.
   */
  createStrategy(definition: StrategyDefinition): Promise<StrategyVersion> {
    return this.http.request('POST', '/v1/strategies', undefined, definition);
  }

  /**
   * Creates a new version.
   * @param strategyId Slug.
   * @param definition Definition.
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
   * Runs a backtest.
   * @param strategyId Slug.
   * @param request Request.
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
   * Activates a version.
   * @param strategyId Slug.
   * @param request Request.
   * @return Version.
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
   * Pauses a strategy.
   * @param strategyId Slug.
   * @return Version.
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
   * Lists signals.
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
   * Lists notification subscriptions.
   * @return Subscriptions and configured channels.
   */
  listAlertSubscriptions(): Promise<AlertSubscriptionList> {
    return this.http.request('GET', '/v1/alerts/subscriptions');
  }

  /**
   * Creates or replaces a notification subscription.
   * @param subscription Subscription.
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

/** HTTP implementation of `ExecutionClient` (execution engine). */
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
   * Lists accounts.
   * @return Accounts.
   */
  listAccounts(): Promise<AccountList> {
    return this.http.request('GET', '/v1/accounts');
  }

  /**
   * Fetches an account summary.
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
   * Previews an order.
   * @param request Draft.
   * @return Preview.
   */
  previewOrder(request: OrderIntentRequest): Promise<OrderPreview> {
    return this.http.request('POST', '/v1/orders/preview', undefined, request);
  }

  /**
   * Submits an intent.
   * @param request Intent.
   * @param idempotencyKey Key.
   * @return Result.
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
   * Lists orders.
   * @param accountId Optional account.
   * @param states Optional states.
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
   * Cancels an order.
   * @param orderId Order id.
   * @return Order.
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
   * Amends an order.
   * @param orderId Order id.
   * @param request Amendment.
   * @return Order.
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
   * Lists positions.
   * @param accountId Optional account.
   * @return Positions.
   */
  listPositions(accountId?: string): Promise<PositionList> {
    return this.http.request('GET', '/v1/positions', {account_id: accountId});
  }

  /**
   * Lists fills.
   * @param accountId Optional account.
   * @return Fills.
   */
  listFills(accountId?: string): Promise<FillList> {
    return this.http.request('GET', '/v1/fills', {account_id: accountId});
  }

  /**
   * Previews a close.
   * @param positionId Position id.
   * @param request Close request.
   * @return Preview.
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
   * Closes a position.
   * @param positionId Position id.
   * @param request Close request.
   * @param idempotencyKey Key.
   * @return Result.
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
   * Lists kill switches.
   * @return Kill switches.
   */
  getKillSwitches(): Promise<KillSwitchList> {
    return this.http.request('GET', '/v1/risk/kill-switch');
  }

  /**
   * Sets a kill switch.
   * @param request Request.
   * @return State.
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
   * Lists audit events.
   * @param accountId Optional account.
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
   * Lists deployments.
   * @return Deployments.
   */
  listDeployments(): Promise<DeploymentList> {
    return this.http.request('GET', '/v1/deployments');
  }

  /**
   * Creates a deployment.
   * @param request Request.
   * @return Deployment.
   */
  createDeployment(request: DeploymentRequest): Promise<Deployment> {
    return this.http.request('POST', '/v1/deployments', undefined, request);
  }

  /**
   * Disables a deployment.
   * @param deploymentId Deployment id.
   * @return Deployment.
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
