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
  AlertDelivery,
  AlertSubscriptionList,
  AccountSummary,
  AmendRequest,
  AoiList,
  AuditEventList,
  CandleBatch,
  CatalogList,
  CatalogScript,
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
  SessionList,
  ScriptResult,
  ScriptRun,
  ScriptVersion,
  SignalList,
  StrategyActivation,
  StrategyActivationInput,
  StrategyActivationList,
  SubmitIntentResponse,
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
  StrategyScript,
  StrategyScriptList,
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
  /** GET response deadline in positive milliseconds (default 30,000). */
  readTimeoutMs?: number;
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
   * @param options Base URL, token provider, fetch and optional read deadline.
   * @throws RangeError If the read deadline is not finite and positive.
   */
  constructor(private readonly options: HttpClientOptions) {
    this.baseUrl = options.baseUrl.replace(/\/$/, '');
    // A detached `fetch` throws "Illegal invocation" in browsers.
    this.fetchImpl = options.fetchImpl ?? fetch.bind(globalThis);
    const deadline = options.readTimeoutMs ?? 30_000;
    if (!Number.isFinite(deadline) || deadline <= 0) {
      throw new RangeError('readTimeoutMs must be finite and positive');
    }
  }

  /**
   * Sends a JSON request and parses the JSON response.
   * @param method HTTP method.
   * @param path Path beginning with `/`.
   * @param query Optional query parameters (undefined values are skipped).
   * @param body Optional JSON body.
   * @param headers Optional extra headers.
   * @param signal Optional cancellation signal, including during body reads.
   * @return Parsed response body, typed by the caller and not validated;
   *     `{}` for an empty body.
   * @throws `ApiError` for a non-2xx response. The promise also rejects
   *     with the `fetch` error when the request fails, and with a
   *     `SyntaxError` when the body is not JSON. GET requests reject on the
   *     read deadline; cancellation rejects with the signal's reason.
   */
  async request<T>(
    method: 'GET' | 'POST' | 'PUT' | 'DELETE',
    path: string,
    query?: Record<string, QueryValue | undefined>,
    body?: unknown,
    headers?: Record<string, string>,
    signal?: AbortSignal
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
    const controller = new AbortController();
    const cancel = () => void controller.abort(signal?.reason);
    if (signal?.aborted) cancel();
    else signal?.addEventListener('abort', cancel, {once: true});
    // Mutations have no automatic deadline or retry: a timed-out trade may
    // already have executed and must be reconciled by its caller.
    const timer =
      method === 'GET'
        ? setTimeout(
            () =>
              void controller.abort(
                new Error(
                  `GET ${path} timed out. Check the market engine connection and retry.`
                )
              ),
            this.options.readTimeoutMs ?? 30_000
          )
        : null;
    try {
      const response = await this.fetchImpl(url.toString(), {
        method,
        signal: controller.signal,
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
    } catch (error: unknown) {
      if (controller.signal.aborted) throw controller.signal.reason;
      throw error;
    } finally {
      if (timer !== null) clearTimeout(timer);
      signal?.removeEventListener('abort', cancel);
    }
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
   * @param query Series, range and optional cancellation signal.
   * @return Candle batch.
   * @throws Error If the request fails, is cancelled or exceeds its deadline.
   */
  getCandles(query: CandleQuery): Promise<CandleBatch> {
    return this.http.request(
      'GET',
      '/v1/candles',
      {
        instrument_id: query.instrumentId,
        tf: query.timeframe,
        from_ms: query.fromMs,
        to_ms: query.toMs,
        limit: query.limit,
      },
      undefined,
      undefined,
      query.signal
    );
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

/** One page of `GET /v1/scripts`. */
interface OwnedScriptPage {
  /** Latest owned versions. */
  scripts: ScriptVersion[];
  /** Whether owned source can execute. */
  runtime_enabled: boolean;
  /** Next page offset, or null at the end. */
  next_offset?: number | null;
}

/**
 * Describes an owned Strategy script with its saved defaults as inputs.
 * @param row Latest owned version.
 * @return Strategy summary without input bounds.
 */
function ownedStrategy(row: ScriptVersion): StrategyScript {
  return {
    script_id: row.script_id,
    origin: 'tenant',
    title: row.name,
    description: '',
    version: row.version,
    inputs: Object.fromEntries(
      Object.entries(row.defaults ?? {}).map(([name, value]) => [
        name,
        {
          default: value ?? null,
          title: name,
          minimum: null,
          maximum: null,
          options: null,
          step: null,
          group: '',
          tooltip: '',
        },
      ])
    ),
    source_available: true,
    enabled: true,
  };
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
   * Lists Strategy scripts: the catalog (`GET /v1/scripts/catalog?kind=
   * strategy`) and the owned scripts (`GET /v1/scripts`, every page).
   * @return Catalog strategies first, then owned ones.
   */
  async listStrategyScripts(): Promise<StrategyScriptList> {
    const catalog = await this.http.request<CatalogList>(
      'GET',
      '/v1/scripts/catalog',
      {kind: 'strategy'}
    );
    const scripts: StrategyScript[] = catalog.scripts.map(row => ({
      script_id: row.script_id,
      origin: row.origin,
      title: row.title,
      description: row.description,
      version: 0,
      inputs: row.inputs,
      source_available: row.source_available,
      enabled: row.enabled,
    }));
    let offset: number | null | undefined = 0;
    let runtime = false;
    for (let page = 0; page < 10 && typeof offset === 'number'; page++) {
      const owned: OwnedScriptPage = await this.http.request(
        'GET',
        '/v1/scripts',
        {offset}
      );
      runtime = owned.runtime_enabled;
      for (const row of owned.scripts) {
        if (row.kind === 'strategy') scripts.push(ownedStrategy(row));
      }
      offset = owned.next_offset;
    }
    return {scripts, runtime_enabled: runtime};
  }

  /**
   * Reads a strategy's source (`GET /v1/scripts/catalog/{id}` for catalog
   * ids, `GET /v1/scripts/{id}` for owned scripts).
   * @param scriptId Strategy id.
   * @return Source text.
   * @throws `ApiError` (403) for protected plugin source.
   */
  async getStrategySource(scriptId: string): Promise<string> {
    const id = encodeURIComponent(scriptId);
    if (scriptId.includes(':')) {
      const row = await this.http.request<CatalogScript>(
        'GET',
        `/v1/scripts/catalog/${id}`
      );
      if (row.source === null || row.source === undefined) {
        throw new ApiError(403, {
          code: 'FORBIDDEN',
          message: 'This plugin strategy does not publish its source.',
          trace_id: 'client',
          retryable: false,
          field_errors: [],
        });
      }
      return row.source;
    }
    const row = await this.http.request<ScriptVersion>(
      'GET',
      `/v1/scripts/${id}`
    );
    return row.source;
  }

  /**
   * Copies a strategy into a new owned script (`POST /v1/scripts`), with
   * the declared input defaults as saved defaults.
   * @param scriptId Strategy to copy.
   * @param newId New owned script slug.
   * @return Saved version 1.
   */
  async copyStrategy(scriptId: string, newId: string): Promise<ScriptVersion> {
    const [source, list] = await Promise.all([
      this.getStrategySource(scriptId),
      this.listStrategyScripts(),
    ]);
    const original = list.scripts.find(row => row.script_id === scriptId);
    return this.http.request('POST', '/v1/scripts', undefined, {
      script_id: newId,
      kind: 'strategy',
      name: `${original?.title ?? scriptId} (copy)`.slice(0, 120),
      source,
      defaults: Object.fromEntries(
        Object.entries(original?.inputs ?? {}).map(([name, spec]) => [
          name,
          spec?.default ?? null,
        ])
      ),
    });
  }

  /**
   * Backtests a strategy (`POST /v1/scripts/{script_id}/run`).
   * @param scriptId Strategy id.
   * @param request Instrument, timeframe, bars, inputs and costs.
   * @return Simulation result.
   */
  backtestStrategy(
    scriptId: string,
    request: ScriptRun
  ): Promise<ScriptResult> {
    return this.http.request(
      'POST',
      `/v1/scripts/${encodeURIComponent(scriptId)}/run`,
      undefined,
      request
    );
  }

  /**
   * Lists activations (`GET /v1/strategy-activations`).
   * @return Activations.
   */
  listActivations(): Promise<StrategyActivationList> {
    return this.http.request('GET', '/v1/strategy-activations');
  }

  /**
   * Activates a strategy (`POST /v1/strategy-activations`).
   * @param request Activation request.
   * @return The activation.
   */
  createActivation(
    request: StrategyActivationInput
  ): Promise<StrategyActivation> {
    return this.http.request(
      'POST',
      '/v1/strategy-activations',
      undefined,
      request
    );
  }

  /**
   * Pauses an activation (`POST /v1/strategy-activations/{id}/pause`).
   * @param activationId Activation id.
   * @return The activation.
   */
  pauseActivation(activationId: string): Promise<StrategyActivation> {
    return this.http.request(
      'POST',
      `/v1/strategy-activations/${encodeURIComponent(activationId)}/pause`,
      undefined,
      {}
    );
  }

  /**
   * Resumes an activation (`POST /v1/strategy-activations/{id}/resume`).
   * @param activationId Activation id.
   * @return The activation.
   */
  resumeActivation(activationId: string): Promise<StrategyActivation> {
    return this.http.request(
      'POST',
      `/v1/strategy-activations/${encodeURIComponent(activationId)}/resume`,
      undefined,
      {}
    );
  }

  /**
   * Deletes an activation (`DELETE /v1/strategy-activations/{id}`).
   * @param activationId Activation id.
   * @return Completion.
   */
  async deleteActivation(activationId: string): Promise<void> {
    await this.http.request(
      'DELETE',
      `/v1/strategy-activations/${encodeURIComponent(activationId)}`
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

  /**
   * Deletes an owned destination.
   * @param subscriptionId Public subscription id.
   * @return Completion after HTTP 204.
   */
  deleteAlertSubscription(subscriptionId: string): Promise<void> {
    return this.http.request(
      'DELETE',
      `/v1/alerts/subscriptions/${encodeURIComponent(subscriptionId)}`
    );
  }

  /**
   * Queues an explicit test notification without executing a trade.
   * @param subscriptionId Enabled, configured destination id.
   * @return Pending delivery metadata.
   */
  testAlertSubscription(subscriptionId: string): Promise<AlertDelivery> {
    return this.http.request(
      'POST',
      `/v1/alerts/subscriptions/${encodeURIComponent(subscriptionId)}/test`
    );
  }

  /**
   * Reads a tenant-scoped delivery's progress.
   * @param deliveryId Opaque outbox delivery identifier.
   * @return Public metadata with a redacted failure when present.
   */
  getAlertDelivery(deliveryId: string): Promise<AlertDelivery> {
    return this.http.request(
      'GET',
      `/v1/alerts/deliveries/${encodeURIComponent(deliveryId)}`
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
