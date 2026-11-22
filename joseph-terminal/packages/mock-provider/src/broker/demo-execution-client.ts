/**
 * @fileoverview DEMO paper broker implementing `ExecutionClient` in the
 * browser for offline development and tests.
 *
 * It imitates the server contract (preview tokens, idempotency, risk
 * decisions, order lifecycle, kill switch, audit chain) so features can be
 * built without a backend. It is a simulation: no order ever reaches a
 * venue, and the authoritative Risk Gateway/OMS live in the execution
 * engine.
 */
import {
  ApiError,
  Decimal,
  type AccountList,
  type AccountSummary,
  type AmendRequest,
  type AuditEvent,
  type AuditEventList,
  type ClosePositionRequest,
  type Deployment,
  type DeploymentList,
  type DeploymentRequest,
  type ErrorCode,
  type ExecutionClient,
  type Fill,
  type FillList,
  type Instrument,
  type KillSwitchList,
  type KillSwitchRequest,
  type KillSwitchState,
  type Order,
  type OrderIntent,
  type OrderIntentRequest,
  type OrderList,
  type OrderPreview,
  type OrderState,
  type Position,
  type PositionList,
  type RiskCheckResult,
  type RiskDecision,
  type SubmitIntentResponse,
  type TradingCapabilities,
} from '@joseph/contracts';
import {canonicalJson, sha256Hex} from '../strategy/sha256';
import {
  DEMO_ACCOUNT_ID,
  DEMO_BROKER_INSTRUMENTS,
  DEMO_MARKS,
  DEMO_TENANT_ID,
  DEMO_USER_ID,
} from './instruments';

/** Events published by the DEMO broker. */
export type DemoBrokerEvent =
  | {type: 'order.updated'; payload: Order}
  | {type: 'fill.created'; payload: Fill}
  | {type: 'position.updated'; payload: Position}
  | {type: 'risk.kill_switch'; payload: KillSwitchState}
  | {type: 'account.updated'; payload: AccountSummary};

/** Options of the DEMO broker. */
export interface DemoBrokerOptions {
  /** Clock (UTC ms). */
  now?: () => number;
  /** Timer used for simulated latency; defaults to `setTimeout`. */
  schedule?: (task: () => void, delayMs: number) => void;
  /** Reference mark per instrument; defaults to static DEMO marks. */
  markPrice?: (instrumentId: string) => string | null;
  /** Delay before the simulated venue acknowledges an order. */
  ackDelayMs?: number;
  /** Delay before a marketable order fills. */
  fillDelayMs?: number;
  /** When set, resting limits fill at their price after this delay. */
  restingFillDelayMs?: number | null;
  /** Split marketable fills into two parts (exercise PARTIALLY_FILLED). */
  partialFills?: boolean;
  /** Starting paper equity. */
  startingEquity?: string;
}

/** Fee rates (DEMO). */
const TAKER_FEE = Decimal.parse('0.0005');
const MAKER_FEE = Decimal.parse('0.0002');
const SLIPPAGE = Decimal.parse('0.0002');
const MAX_NOTIONAL = Decimal.parse('1000000');
const PREVIEW_TTL_MS = 30_000;
const POLICY_VERSION = 'demo-risk-1';
const TERMINAL: ReadonlySet<OrderState> = new Set<OrderState>([
  'FILLED',
  'CANCELED',
  'REJECTED',
  'EXPIRED',
  'FAILED',
]);

/** DEMO trading capabilities of the paper account. */
const DEMO_CAPABILITIES: TradingCapabilities = {
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
 * Builds an ApiError with a contract error body.
 * @param status HTTP-like status.
 * @param code Error code.
 * @param message Message.
 * @return Error instance.
 */
function apiError(status: number, code: ErrorCode, message: string): ApiError {
  return new ApiError(status, {
    code,
    message,
    trace_id: 'demo',
    retryable: false,
    field_errors: [],
  });
}

/** Internal position record. */
interface PositionBook {
  /** Signed quantity (positive = long). */
  qty: Decimal;
  /** Average entry price. */
  entry: Decimal;
  /** Realized PnL. */
  realized: Decimal;
}

/** DEMO implementation of the execution client. */
export class DemoExecutionClient implements ExecutionClient {
  private readonly now: () => number;
  private readonly schedule: (task: () => void, delayMs: number) => void;
  private readonly mark: (instrumentId: string) => string | null;
  private readonly ackDelayMs: number;
  private readonly fillDelayMs: number;
  private readonly restingFillDelayMs: number | null;
  private readonly partialFills: boolean;
  private readonly startingEquity: Decimal;
  private readonly instruments = new Map<string, Instrument>(
    DEMO_BROKER_INSTRUMENTS.map(i => [i.instrument_id, i])
  );
  private readonly orders = new Map<string, Order>();
  private readonly fills: Fill[] = [];
  private readonly positions = new Map<string, PositionBook>();
  private readonly previews = new Map<
    string,
    {canonical: string; expiresAt: number}
  >();
  private readonly idempotency = new Map<
    string,
    {canonical: string; response: SubmitIntentResponse}
  >();
  private readonly killSwitches = new Map<string, KillSwitchState>();
  private readonly audit: AuditEvent[] = [];
  private readonly deployments = new Map<string, Deployment>();
  private readonly pendingBrackets = new Map<string, OrderIntentRequest>();
  private readonly listeners = new Set<(event: DemoBrokerEvent) => void>();
  private counter = 0;

  /**
   * Creates the DEMO broker.
   * @param options Simulation options.
   */
  constructor(options: DemoBrokerOptions = {}) {
    this.now = options.now ?? Date.now;
    this.schedule =
      options.schedule ?? ((task, delay) => void setTimeout(task, delay));
    this.mark = options.markPrice ?? (id => DEMO_MARKS[id] ?? null);
    this.ackDelayMs = options.ackDelayMs ?? 250;
    this.fillDelayMs = options.fillDelayMs ?? 600;
    this.restingFillDelayMs = options.restingFillDelayMs ?? null;
    this.partialFills = options.partialFills ?? true;
    this.startingEquity = Decimal.parse(options.startingEquity ?? '100000');
  }

  /**
   * Subscribes to broker events (used by `DemoUserStream`).
   * @param listener Listener.
   * @return Unsubscribe function.
   */
  onEvent(listener: (event: DemoBrokerEvent) => void): () => void {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }

  /**
   * Publishes an event.
   * @param event Event.
   */
  private emit(event: DemoBrokerEvent): void {
    for (const listener of [...this.listeners]) listener(event);
  }

  /**
   * Returns a new sequential id.
   * @param prefix Prefix.
   * @return Id.
   */
  private nextId(prefix: string): string {
    this.counter += 1;
    return `${prefix}-${this.counter.toString().padStart(6, '0')}`;
  }

  /**
   * Appends a hash-chained audit event.
   * @param action Action name.
   * @param entityType Entity type.
   * @param entityId Entity id.
   * @param payload JSON payload.
   */
  private record(
    action: string,
    entityType: string,
    entityId: string,
    payload: Record<string, unknown>
  ): void {
    const prevHash = this.audit[this.audit.length - 1]?.hash ?? 'genesis';
    const base = {
      event_id: this.nextId('demo-audit'),
      tenant_id: DEMO_TENANT_ID,
      account_id: DEMO_ACCOUNT_ID,
      actor: DEMO_USER_ID,
      action,
      entity_type: entityType,
      entity_id: entityId,
      trace_id: this.nextId('demo-trace'),
      ts_ms: this.now(),
      payload,
      prev_hash: prevHash,
    };
    this.audit.push({...base, hash: sha256Hex(prevHash + canonicalJson(base))});
  }

  /**
   * Returns the instrument or throws.
   * @param instrumentId Instrument id.
   * @return Instrument.
   */
  private instrument(instrumentId: string): Instrument | null {
    return this.instruments.get(instrumentId) ?? null;
  }

  /**
   * Rounds a request to instrument precision.
   * @param request Request.
   * @return Normalized copy.
   */
  private normalize(request: OrderIntentRequest): OrderIntentRequest {
    const instrument = this.instrument(request.instrument_id);
    if (!instrument) return {...request, preview_token: null};
    const tick = Decimal.parse(instrument.tick_size);
    const lot = Decimal.parse(instrument.lot_size);
    const round = (value: string | null | undefined) =>
      value && Decimal.isValid(value)
        ? Decimal.parse(value).roundToStep(tick).toString()
        : (value ?? null);
    const qty = Decimal.isValid(request.quantity_base)
      ? Decimal.parse(request.quantity_base).floorToStep(lot).toString()
      : request.quantity_base;
    return {
      ...request,
      quantity_base: qty,
      limit_price: round(request.limit_price),
      trigger_price: round(request.trigger_price),
      preview_token: null,
    };
  }

  /**
   * Returns whether any engaged kill switch covers a request.
   * @param request Request.
   * @return Engaged switch, if any.
   */
  private blockingSwitch(request: OrderIntentRequest): KillSwitchState | null {
    for (const state of this.killSwitches.values()) {
      if (!state.engaged) continue;
      if (state.scope === 'GLOBAL') return state;
      if (state.scope === 'ACCOUNT' && state.target_id === request.account_id)
        return state;
      if (
        state.scope === 'STRATEGY' &&
        request.strategy_version_id &&
        state.target_id === request.strategy_version_id
      )
        return state;
    }
    return null;
  }

  /**
   * Evaluates DEMO risk checks for a normalized request.
   * @param request Normalized request.
   * @return Decision and derived values.
   */
  private evaluate(request: OrderIntentRequest): {
    risk: RiskDecision;
    notional: Decimal;
    fee: Decimal;
    margin: Decimal;
    reference: Decimal | null;
  } {
    const checks: RiskCheckResult[] = [];
    const add = (
      check: string,
      domain: RiskCheckResult['domain'],
      outcome: RiskCheckResult['outcome'],
      message: string,
      code: ErrorCode | null = null,
      observed: string | null = null,
      limit: string | null = null
    ) => checks.push({check, domain, outcome, message, code, observed, limit});
    const instrument = this.instrument(request.instrument_id);
    if (request.mode === 'LIVE') {
      add(
        'mode.permitted',
        'MODE',
        'FAIL',
        'Live trading is not available in DEMO mode.',
        'LIVE_TRADING_DISABLED'
      );
    } else {
      add(
        'mode.permitted',
        'MODE',
        'PASS',
        `${request.mode.toLowerCase()} mode is enabled for this account.`
      );
    }
    if (request.account_id === DEMO_ACCOUNT_ID)
      add('identity.account', 'IDENTITY', 'PASS', 'Account belongs to you.');
    else
      add(
        'identity.account',
        'IDENTITY',
        'FAIL',
        'Unknown account.',
        'FORBIDDEN'
      );
    if (instrument && instrument.status === 'TRADING')
      add(
        'market.instrument_trading',
        'MARKET_STATUS',
        'PASS',
        'Instrument is trading.'
      );
    else
      add(
        'market.instrument_trading',
        'MARKET_STATUS',
        'FAIL',
        'This instrument is not available in the DEMO broker.',
        'UNSUPPORTED'
      );
    const markText = this.mark(request.instrument_id);
    if (markText)
      add(
        'data.mark_fresh',
        'DATA_QUALITY',
        'WARN',
        'DEMO marks are synthetic.',
        null,
        markText
      );
    else
      add(
        'data.mark_fresh',
        'DATA_QUALITY',
        'FAIL',
        'No mark price is available.',
        'MARKET_STALE'
      );
    const mark = markText ? Decimal.parse(markText) : null;
    const reference = request.limit_price
      ? Decimal.parse(request.limit_price)
      : request.trigger_price
        ? Decimal.parse(request.trigger_price)
        : mark;
    const qty = Decimal.isValid(request.quantity_base)
      ? Decimal.parse(request.quantity_base)
      : Decimal.zero();
    let notional = Decimal.zero();
    if (instrument) {
      if (qty.isZero() || qty.cmp(Decimal.parse(instrument.min_qty)) < 0) {
        add(
          'size.lot',
          'SIZE',
          'FAIL',
          `Quantity must be at least ${instrument.min_qty}.`,
          'INVALID_LOT_SIZE',
          qty.toString(),
          instrument.min_qty
        );
      } else {
        add(
          'size.lot',
          'SIZE',
          'PASS',
          `Quantity is a multiple of ${instrument.lot_size}.`,
          null,
          qty.toString()
        );
      }
      if (
        (request.order_type === 'limit' ||
          request.order_type === 'stop_limit') &&
        !request.limit_price
      ) {
        add(
          'size.tick',
          'SIZE',
          'FAIL',
          'A limit price is required.',
          'INVALID_TICK_SIZE'
        );
      } else {
        add(
          'size.tick',
          'SIZE',
          'PASS',
          `Prices are multiples of ${instrument.tick_size}.`
        );
      }
      notional = reference ? qty.mul(reference) : Decimal.zero();
      const minNotional = Decimal.parse(instrument.min_notional ?? '0');
      if (reference && notional.cmp(minNotional) < 0) {
        add(
          'size.min_notional',
          'SIZE',
          'FAIL',
          `Order value is below ${instrument.min_notional}.`,
          'MIN_NOTIONAL',
          notional.toFixed(2),
          instrument.min_notional ?? null
        );
      } else {
        add(
          'size.min_notional',
          'SIZE',
          'PASS',
          'Order value meets the minimum.',
          null,
          notional.toFixed(2),
          instrument.min_notional ?? null
        );
      }
    }
    const leverage =
      request.leverage && Decimal.isValid(request.leverage)
        ? Decimal.parse(request.leverage)
        : Decimal.parse('1');
    if (leverage.cmp(Decimal.parse('20')) > 0)
      add(
        'size.leverage',
        'SIZE',
        'FAIL',
        'Maximum leverage is 20x.',
        'RISK_LIMIT',
        leverage.toString(),
        '20'
      );
    else
      add(
        'size.leverage',
        'SIZE',
        'PASS',
        `Leverage ${leverage.toString()}x is within limits.`,
        null,
        leverage.toString(),
        '20'
      );
    const margin = leverage.isZero() ? notional : notional.div(leverage);
    if (notional.cmp(MAX_NOTIONAL) > 0)
      add(
        'exposure.max_notional',
        'EXPOSURE',
        'FAIL',
        'Order exceeds the account notional limit.',
        'RISK_LIMIT',
        notional.toFixed(2),
        MAX_NOTIONAL.toString()
      );
    else
      add(
        'exposure.max_notional',
        'EXPOSURE',
        'PASS',
        'Within the account notional limit.',
        null,
        notional.toFixed(2),
        MAX_NOTIONAL.toString()
      );
    const available = this.availableMargin();
    if (!request.reduce_only && margin.cmp(available) > 0)
      add(
        'account.margin',
        'ACCOUNT',
        'FAIL',
        'Not enough available margin.',
        'INSUFFICIENT_MARGIN',
        margin.toFixed(2),
        available.toFixed(2)
      );
    else
      add(
        'account.margin',
        'ACCOUNT',
        'PASS',
        'Enough available margin.',
        null,
        margin.toFixed(2),
        available.toFixed(2)
      );
    const engaged = this.blockingSwitch(request);
    if (engaged && !request.reduce_only) {
      add(
        'kill_switch.clear',
        'KILL_SWITCH',
        'FAIL',
        `Kill switch engaged (${engaged.scope.toLowerCase()}): new opening orders are blocked.`,
        'KILL_SWITCH_ENGAGED'
      );
    } else if (engaged) {
      add(
        'kill_switch.clear',
        'KILL_SWITCH',
        'PASS',
        'Risk-reducing order allowed while the kill switch is engaged.'
      );
    } else {
      add(
        'kill_switch.clear',
        'KILL_SWITCH',
        'PASS',
        'No kill switch engaged.'
      );
    }
    if (request.reduce_only) {
      const book = this.positions.get(request.instrument_id);
      const reduces =
        book &&
        !book.qty.isZero() &&
        (request.side === 'sell'
          ? !book.qty.isNegative()
          : book.qty.isNegative());
      if (!reduces)
        add(
          'duplicate.reduce_only',
          'DUPLICATE',
          'FAIL',
          'Reduce-only order has no position to reduce.',
          'POSITION_UNKNOWN'
        );
      else
        add(
          'duplicate.reduce_only',
          'DUPLICATE',
          'PASS',
          'Reduces an existing position.'
        );
    }
    if (
      request.bracket &&
      (request.bracket.stop_loss ||
        (request.bracket.take_profit?.length ?? 0) > 0)
    ) {
      add(
        'protection.bracket',
        'PROTECTION',
        'PASS',
        'Protective orders are simulated in DEMO.'
      );
    } else {
      add(
        'protection.bracket',
        'PROTECTION',
        'SKIP',
        'No protective orders attached.'
      );
    }
    const failures = checks.filter(c => c.outcome === 'FAIL');
    const decision: RiskDecision['decision'] =
      failures.length === 0
        ? 'ALLOW'
        : failures.some(c => c.domain === 'SIZE')
          ? 'REJECT'
          : 'BLOCK';
    const fee = notional.mul(
      request.order_type === 'market' ? TAKER_FEE : MAKER_FEE
    );
    return {
      risk: {
        decision,
        checks,
        policy_version: POLICY_VERSION,
        evaluated_at_ms: this.now(),
        reservation_id: null,
      },
      notional,
      fee,
      margin,
      reference,
    };
  }

  /**
   * Computes available margin (equity minus margin in use).
   * @return Available margin.
   */
  private availableMargin(): Decimal {
    let used = Decimal.zero();
    for (const [id, book] of this.positions) {
      const mark = this.mark(id);
      if (mark) used = used.add(book.qty.abs().mul(Decimal.parse(mark)));
    }
    return this.equity().sub(used);
  }

  /**
   * Computes equity including realized and unrealized PnL.
   * @return Equity.
   */
  private equity(): Decimal {
    let equity = this.startingEquity;
    for (const [id, book] of this.positions) {
      equity = equity.add(book.realized);
      const mark = this.mark(id);
      if (mark && !book.qty.isZero())
        equity = equity.add(Decimal.parse(mark).sub(book.entry).mul(book.qty));
    }
    for (const fill of this.fills) equity = equity.sub(Decimal.parse(fill.fee));
    return equity;
  }

  /**
   * Lists accounts.
   * @return Account list with the DEMO paper account.
   */
  async listAccounts(): Promise<AccountList> {
    return {accounts: [this.summary()]};
  }

  /**
   * Fetches the account summary.
   * @param accountId Account id.
   * @return Summary.
   */
  async getAccountSummary(accountId: string): Promise<AccountSummary> {
    if (accountId !== DEMO_ACCOUNT_ID)
      throw apiError(404, 'NOT_FOUND', 'Unknown account.');
    return this.summary();
  }

  /**
   * Builds the account summary.
   * @return Summary.
   */
  private summary(): AccountSummary {
    return {
      account_id: DEMO_ACCOUNT_ID,
      tenant_id: DEMO_TENANT_ID,
      venue: 'mock',
      label: 'Demo paper account',
      status: 'CONNECTED',
      modes: ['PAPER', 'SHADOW'],
      equity: this.equity().toFixed(2),
      available_margin: this.availableMargin().toFixed(2),
      currency: 'USD',
      positions: this.positionList(),
      open_orders: [...this.orders.values()].filter(
        o => !TERMINAL.has(o.state)
      ),
      capabilities: DEMO_CAPABILITIES,
      health: 'HEALTHY',
      stale: false,
      last_sync_ms: this.now(),
    };
  }

  /**
   * Previews an order.
   * @param request Draft.
   * @return Preview.
   */
  async previewOrder(request: OrderIntentRequest): Promise<OrderPreview> {
    const normalized = this.normalize(request);
    const {risk, notional, fee, margin} = this.evaluate(normalized);
    const token = this.nextId('demo-preview');
    const expiresAt = this.now() + PREVIEW_TTL_MS;
    this.previews.set(token, {canonical: canonicalJson(normalized), expiresAt});
    const mark = this.mark(request.instrument_id);
    const warnings = ['DEMO simulation: no order reaches a venue.'];
    if (normalized.quantity_base !== request.quantity_base)
      warnings.push(`Quantity rounded down to ${normalized.quantity_base}.`);
    return {
      allowed: risk.decision === 'ALLOW',
      normalized,
      notional: notional.toFixed(2),
      estimated_fee: fee.toFixed(4),
      estimated_slippage_pct: request.order_type === 'market' ? '0.02' : null,
      margin_required: margin.toFixed(2),
      mark_price: mark,
      best_bid: mark,
      best_ask: mark,
      risk,
      warnings,
      preview_token: token,
      expires_at_ms: expiresAt,
      protection: {
        native_stop_loss: true,
        native_take_profit: true,
        note: 'DEMO: protective orders are simulated in your browser and do not exist at any venue.',
      },
    };
  }

  /**
   * Submits an intent with idempotency.
   * @param request Intent request.
   * @param idempotencyKey Key.
   * @return Submission result.
   */
  async submitIntent(
    request: OrderIntentRequest,
    idempotencyKey: string
  ): Promise<SubmitIntentResponse> {
    const canonical = canonicalJson(request);
    const existing = this.idempotency.get(idempotencyKey);
    if (existing) {
      if (existing.canonical !== canonical) {
        throw new ApiError(409, {
          code: 'IDEMPOTENCY_CONFLICT',
          message:
            'This idempotency key was already used for a different order.',
          trace_id: 'demo',
          retryable: false,
          field_errors: [],
        });
      }
      return {...existing.response, replayed: true};
    }
    const normalized = this.normalize(request);
    if (request.preview_token) {
      const preview = this.previews.get(request.preview_token);
      if (!preview || this.now() >= preview.expiresAt) {
        throw apiError(
          409,
          'PREVIEW_EXPIRED',
          'The preview expired. Preview the order again.'
        );
      }
      if (preview.canonical !== canonicalJson(normalized)) {
        throw apiError(
          409,
          'PREVIEW_REQUIRED',
          'The order changed after the preview. Preview it again.'
        );
      }
    }
    const {risk} = this.evaluate(normalized);
    const intent: OrderIntent = {
      intent_id: this.nextId('demo-intent'),
      tenant_id: DEMO_TENANT_ID,
      user_id: DEMO_USER_ID,
      idempotency_key: idempotencyKey,
      client_order_id: this.nextId('demo-cl'),
      created_at_ms: this.now(),
      request,
    };
    const orders: Order[] = [];
    if (risk.decision === 'ALLOW') {
      const order = this.createOrder(intent, normalized, 'PRIMARY', null);
      orders.push(order);
      this.record('order.submit', 'order', order.order_id, {
        intent_id: intent.intent_id,
        mode: normalized.mode,
      });
    } else {
      this.record('order.blocked', 'intent', intent.intent_id, {
        decision: risk.decision,
      });
    }
    const response: SubmitIntentResponse = {
      intent,
      risk,
      orders,
      replayed: false,
    };
    this.idempotency.set(idempotencyKey, {canonical, response});
    return response;
  }

  /**
   * Creates an order in SUBMITTING and schedules its simulated lifecycle.
   * @param intent Intent.
   * @param request Normalized request.
   * @param role Order role.
   * @param parentId Parent order id for bracket children.
   * @return Order snapshot.
   */
  private createOrder(
    intent: OrderIntent,
    request: OrderIntentRequest,
    role: Order['role'],
    parentId: string | null
  ): Order {
    const now = this.now();
    const order: Order = {
      order_id: this.nextId('demo-ord'),
      intent_id: intent.intent_id,
      tenant_id: DEMO_TENANT_ID,
      account_id: request.account_id,
      instrument_id: request.instrument_id,
      client_order_id:
        role === 'PRIMARY' ? intent.client_order_id : this.nextId('demo-cl'),
      exchange_order_id: null,
      parent_order_id: parentId,
      mode: request.mode,
      role,
      side: request.side,
      order_type: request.order_type,
      time_in_force: request.time_in_force,
      quantity_base: request.quantity_base,
      filled_base: '0',
      avg_fill_price: null,
      limit_price: request.limit_price ?? null,
      trigger_price: request.trigger_price ?? null,
      reduce_only: request.reduce_only,
      state: role === 'PRIMARY' ? 'SUBMITTING' : 'ACKNOWLEDGED',
      state_reason: null,
      strategy_version_id: request.strategy_version_id ?? null,
      signal_id: request.signal_id ?? null,
      created_at_ms: now,
      updated_at_ms: now,
      version: 0,
    };
    this.orders.set(order.order_id, order);
    this.emit({type: 'order.updated', payload: order});
    if (role === 'PRIMARY') {
      this.pendingBrackets.set(order.order_id, request);
      this.schedule(() => this.acknowledge(order.order_id), this.ackDelayMs);
    }
    return order;
  }

  /**
   * Updates an order and publishes the change.
   * @param orderId Order id.
   * @param patch Fields to change.
   * @return Updated order, or null when absent.
   */
  private update(orderId: string, patch: Partial<Order>): Order | null {
    const current = this.orders.get(orderId);
    if (!current) return null;
    const next: Order = {
      ...current,
      ...patch,
      updated_at_ms: this.now(),
      version: current.version + 1,
    };
    this.orders.set(orderId, next);
    this.emit({type: 'order.updated', payload: next});
    return next;
  }

  /**
   * Simulates the venue acknowledgment.
   * @param orderId Order id.
   */
  private acknowledge(orderId: string): void {
    const order = this.orders.get(orderId);
    if (!order || order.state !== 'SUBMITTING') return;
    this.update(orderId, {
      state: 'ACKNOWLEDGED',
      exchange_order_id: `demo-x-${orderId}`,
    });
    this.tryFill(orderId);
  }

  /**
   * Fills the order if it is marketable, otherwise leaves it resting.
   * @param orderId Order id.
   */
  private tryFill(orderId: string): void {
    const order = this.orders.get(orderId);
    if (
      !order ||
      (order.state !== 'ACKNOWLEDGED' && order.state !== 'PARTIALLY_FILLED')
    )
      return;
    const markText = this.mark(order.instrument_id);
    if (!markText) return;
    const mark = Decimal.parse(markText);
    const buy = order.side === 'buy';
    let price: Decimal | null = null;
    if (order.order_type === 'market') {
      price = buy
        ? mark.mul(Decimal.parse('1').add(SLIPPAGE))
        : mark.mul(Decimal.parse('1').sub(SLIPPAGE));
    } else if (order.order_type === 'limit' && order.limit_price) {
      const limit = Decimal.parse(order.limit_price);
      const marketable = buy ? limit.cmp(mark) >= 0 : limit.cmp(mark) <= 0;
      // A marketable limit fills at the better of its limit and the mark.
      if (marketable)
        price = buy
          ? mark.cmp(limit) < 0
            ? mark
            : limit
          : mark.cmp(limit) > 0
            ? mark
            : limit;
    } else if (order.trigger_price) {
      const trigger = Decimal.parse(order.trigger_price);
      if (buy ? mark.cmp(trigger) >= 0 : mark.cmp(trigger) <= 0) price = mark;
    }
    if (!price) {
      if (this.restingFillDelayMs !== null && order.role === 'PRIMARY') {
        const restingPrice = order.limit_price ?? order.trigger_price;
        this.schedule(() => {
          const latest = this.orders.get(orderId);
          if (
            latest &&
            restingPrice &&
            (latest.state === 'ACKNOWLEDGED' ||
              latest.state === 'PARTIALLY_FILLED')
          ) {
            this.applyFill(
              orderId,
              Decimal.parse(latest.quantity_base).sub(
                Decimal.parse(latest.filled_base)
              ),
              Decimal.parse(restingPrice),
              'maker'
            );
          }
        }, this.restingFillDelayMs);
      }
      return;
    }
    const instrument = this.instrument(order.instrument_id);
    const tick = Decimal.parse(instrument?.tick_size ?? '0.01');
    const lot = Decimal.parse(instrument?.lot_size ?? '0.001');
    const fillPrice = price.roundToStep(tick);
    const remaining = Decimal.parse(order.quantity_base).sub(
      Decimal.parse(order.filled_base)
    );
    const liquidity = order.order_type === 'market' ? 'taker' : 'maker';
    const first =
      this.partialFills && order.state === 'ACKNOWLEDGED'
        ? remaining.div(Decimal.parse('2')).floorToStep(lot)
        : remaining;
    if (first.isZero() || first.eq(remaining)) {
      this.schedule(
        () => this.applyFill(orderId, remaining, fillPrice, liquidity),
        this.fillDelayMs
      );
      return;
    }
    this.schedule(() => {
      this.applyFill(orderId, first, fillPrice, liquidity);
      this.schedule(() => {
        const latest = this.orders.get(orderId);
        if (!latest) return;
        this.applyFill(
          orderId,
          Decimal.parse(latest.quantity_base).sub(
            Decimal.parse(latest.filled_base)
          ),
          fillPrice,
          liquidity
        );
      }, this.fillDelayMs);
    }, this.fillDelayMs);
  }

  /**
   * Applies a fill to an order and the position.
   * @param orderId Order id.
   * @param quantity Fill quantity.
   * @param price Fill price.
   * @param liquidity Maker or taker.
   */
  private applyFill(
    orderId: string,
    quantity: Decimal,
    price: Decimal,
    liquidity: 'maker' | 'taker'
  ): void {
    const order = this.orders.get(orderId);
    if (
      !order ||
      (order.state !== 'ACKNOWLEDGED' && order.state !== 'PARTIALLY_FILLED')
    )
      return;
    let qty = quantity;
    const book = this.positions.get(order.instrument_id) ?? {
      qty: Decimal.zero(),
      entry: Decimal.zero(),
      realized: Decimal.zero(),
    };
    if (order.reduce_only) {
      const reduces =
        !book.qty.isZero() &&
        (order.side === 'sell'
          ? !book.qty.isNegative()
          : book.qty.isNegative());
      if (!reduces) {
        this.update(orderId, {
          state: 'CANCELED',
          state_reason: 'Nothing left to reduce.',
        });
        return;
      }
      if (qty.cmp(book.qty.abs()) > 0) qty = book.qty.abs();
    }
    if (qty.isZero() || qty.isNegative()) return;
    const filledBefore = Decimal.parse(order.filled_base);
    const filled = filledBefore.add(qty);
    const avgBefore = order.avg_fill_price
      ? Decimal.parse(order.avg_fill_price)
      : Decimal.zero();
    const avg = avgBefore.mul(filledBefore).add(price.mul(qty)).div(filled);
    const fee = qty
      .mul(price)
      .mul(liquidity === 'taker' ? TAKER_FEE : MAKER_FEE);
    const fill: Fill = {
      fill_id: this.nextId('demo-fill'),
      order_id: orderId,
      account_id: order.account_id,
      instrument_id: order.instrument_id,
      exchange_fill_id: this.nextId('demo-xfill'),
      side: order.side,
      price: price.toString(),
      quantity_base: qty.toString(),
      fee: fee.toFixed(6),
      fee_asset: 'USD',
      liquidity,
      ts_ms: this.now(),
      mode: order.mode,
    };
    this.fills.unshift(fill);
    this.emit({type: 'fill.created', payload: fill});
    const complete = filled.cmp(Decimal.parse(order.quantity_base)) >= 0;
    // A reduce-only order that flattens the position before its full size
    // filled has nothing left to reduce: the remainder is canceled, as
    // venues do, instead of pretending the whole quantity filled.
    const flattened = order.reduce_only && qty.cmp(book.qty.abs()) >= 0;
    const instrument = this.instrument(order.instrument_id);
    const tick = Decimal.parse(instrument?.tick_size ?? '0.01');
    this.update(orderId, {
      filled_base: filled.toString(),
      avg_fill_price: avg
        .roundToStep(tick.div(Decimal.parse('1000')))
        .toString(),
      state: complete ? 'FILLED' : flattened ? 'CANCELED' : 'PARTIALLY_FILLED',
      state_reason:
        !complete && flattened
          ? 'Remaining reduce-only quantity canceled: the position is flat.'
          : (order.state_reason ?? null),
    });
    this.applyToPosition(order, qty, price, book);
    if (complete && order.role === 'PRIMARY') this.placeBracket(orderId);
  }

  /**
   * Updates the netted one-way position.
   * @param order Filled order.
   * @param qty Fill quantity.
   * @param price Fill price.
   * @param book Current position book.
   */
  private applyToPosition(
    order: Order,
    qty: Decimal,
    price: Decimal,
    book: PositionBook
  ): void {
    const signed = order.side === 'buy' ? qty : Decimal.zero().sub(qty);
    const next: PositionBook = {...book};
    const sameSide =
      book.qty.isZero() || book.qty.isNegative() === signed.isNegative();
    if (sameSide) {
      const total = book.qty.add(signed);
      next.entry = book.qty.isZero()
        ? price
        : book.entry.mul(book.qty.abs()).add(price.mul(qty)).div(total.abs());
      next.qty = total;
    } else {
      const closing = qty.cmp(book.qty.abs()) <= 0 ? qty : book.qty.abs();
      const pnlPerUnit = book.qty.isNegative()
        ? book.entry.sub(price)
        : price.sub(book.entry);
      next.realized = book.realized.add(pnlPerUnit.mul(closing));
      next.qty = book.qty.add(signed);
      if (next.qty.isZero()) next.entry = Decimal.zero();
      else if (next.qty.isNegative() !== book.qty.isNegative())
        next.entry = price;
    }
    this.positions.set(order.instrument_id, next);
    const position = this.toPosition(order.instrument_id, next);
    this.emit({type: 'position.updated', payload: position});
    if (next.qty.isZero()) this.cancelChildren(order.instrument_id);
    this.emit({type: 'account.updated', payload: this.summary()});
  }

  /**
   * Places simulated SL/TP children after the primary order filled.
   * @param orderId Primary order id.
   */
  private placeBracket(orderId: string): void {
    const request = this.pendingBrackets.get(orderId);
    this.pendingBrackets.delete(orderId);
    const parent = this.orders.get(orderId);
    if (!request?.bracket || !parent) return;
    const exitSide: OrderIntentRequest['side'] =
      parent.side === 'buy' ? 'sell' : 'buy';
    const intent: OrderIntent = {
      intent_id: parent.intent_id,
      tenant_id: DEMO_TENANT_ID,
      user_id: DEMO_USER_ID,
      idempotency_key: `${parent.intent_id}:bracket`,
      client_order_id: parent.client_order_id,
      created_at_ms: this.now(),
      request,
    };
    const base: OrderIntentRequest = {
      ...request,
      side: exitSide,
      reduce_only: true,
      position_effect: 'close',
      bracket: null,
      quantity_base: parent.filled_base,
    };
    if (request.bracket.stop_loss) {
      this.createOrder(
        intent,
        {
          ...base,
          order_type: 'stop_market',
          trigger_price: request.bracket.stop_loss.trigger,
          trigger_basis: request.bracket.stop_loss.basis,
          limit_price: null,
          time_in_force: 'GTC',
        },
        'STOP_LOSS',
        orderId
      );
    }
    for (const tp of request.bracket.take_profit ?? []) {
      this.createOrder(
        intent,
        {
          ...base,
          order_type: 'limit',
          limit_price: tp.trigger,
          trigger_price: null,
          time_in_force: 'GTC',
        },
        'TAKE_PROFIT',
        orderId
      );
    }
  }

  /**
   * Cancels resting bracket children when a position is flat.
   * @param instrumentId Instrument id.
   */
  private cancelChildren(instrumentId: string): void {
    for (const order of this.orders.values()) {
      if (
        order.instrument_id === instrumentId &&
        order.role !== 'PRIMARY' &&
        !TERMINAL.has(order.state)
      ) {
        this.update(order.order_id, {
          state: 'CANCELED',
          state_reason: 'Position closed.',
        });
      }
    }
  }

  /**
   * Converts a position book to the contract shape.
   * @param instrumentId Instrument id.
   * @param book Position book.
   * @return Position.
   */
  private toPosition(instrumentId: string, book: PositionBook): Position {
    const mark = this.mark(instrumentId);
    const side = book.qty.isZero()
      ? 'flat'
      : book.qty.isNegative()
        ? 'short'
        : 'long';
    const unrealized =
      mark && !book.qty.isZero()
        ? Decimal.parse(mark).sub(book.entry).mul(book.qty).toFixed(2)
        : null;
    const instrument = this.instrument(instrumentId);
    const tick = Decimal.parse(instrument?.tick_size ?? '0.01');
    const liquidation = book.qty.isZero()
      ? null
      : (book.qty.isNegative()
          ? book.entry.mul(Decimal.parse('1.5'))
          : book.entry.mul(Decimal.parse('0.5'))
        )
          .roundToStep(tick)
          .toString();
    return {
      position_id: `demo-pos-${instrumentId}`,
      account_id: DEMO_ACCOUNT_ID,
      instrument_id: instrumentId,
      side,
      quantity_base: book.qty.abs().toString(),
      entry_price: book.qty.isZero()
        ? null
        : book.entry.roundToStep(tick.div(Decimal.parse('1000'))).toString(),
      mark_price: mark,
      unrealized_pnl: unrealized,
      realized_pnl: book.realized.toFixed(2),
      liquidation_price: liquidation,
      margin: mark ? book.qty.abs().mul(Decimal.parse(mark)).toFixed(2) : null,
      leverage: '1',
      funding_accrued: '0',
      mode: 'PAPER',
      stale: false,
      as_of_ms: this.now(),
    };
  }

  /**
   * Lists non-flat positions.
   * @return Positions.
   */
  private positionList(): Position[] {
    return [...this.positions.entries()]
      .filter(([, book]) => !book.qty.isZero())
      .map(([id, book]) => this.toPosition(id, book));
  }

  /**
   * Lists orders.
   * @param accountId Optional account.
   * @param states Optional states.
   * @return Orders, newest first.
   */
  async listOrders(
    accountId?: string,
    states?: readonly OrderState[]
  ): Promise<OrderList> {
    const orders = [...this.orders.values()]
      .filter(
        o =>
          (!accountId || o.account_id === accountId) &&
          (!states || states.includes(o.state))
      )
      .sort(
        (a, b) =>
          b.created_at_ms - a.created_at_ms ||
          b.order_id.localeCompare(a.order_id)
      );
    return {orders};
  }

  /**
   * Requests cancellation.
   * @param orderId Order id.
   * @return Order in CANCEL_PENDING.
   */
  async cancelOrder(orderId: string): Promise<Order> {
    const order = this.orders.get(orderId);
    if (!order) throw apiError(404, 'NOT_FOUND', 'Order not found.');
    if (TERMINAL.has(order.state))
      throw apiError(
        409,
        'CONFLICT',
        `The order is already ${order.state.toLowerCase()}.`
      );
    if (order.state === 'UNKNOWN_RECONCILING')
      throw apiError(
        409,
        'ORDER_UNKNOWN',
        'The order state is being reconciled.'
      );
    const pending = this.update(orderId, {state: 'CANCEL_PENDING'});
    this.record('order.cancel', 'order', orderId, {});
    this.schedule(() => {
      const latest = this.orders.get(orderId);
      if (latest?.state === 'CANCEL_PENDING')
        this.update(orderId, {
          state: 'CANCELED',
          state_reason: 'Canceled by you.',
        });
    }, this.ackDelayMs);
    return pending ?? order;
  }

  /**
   * Amends price or quantity with optimistic concurrency.
   * @param orderId Order id.
   * @param request Amendment.
   * @return Updated order.
   */
  async amendOrder(orderId: string, request: AmendRequest): Promise<Order> {
    const order = this.orders.get(orderId);
    if (!order) throw apiError(404, 'NOT_FOUND', 'Order not found.');
    if (order.state !== 'ACKNOWLEDGED' && order.state !== 'PARTIALLY_FILLED') {
      throw apiError(409, 'CONFLICT', 'Only working orders can be amended.');
    }
    if (
      request.expected_version !== undefined &&
      request.expected_version !== order.version
    ) {
      throw apiError(
        409,
        'CONFLICT',
        'The order changed since you last saw it. Review it and try again.'
      );
    }
    const instrument = this.instrument(order.instrument_id);
    const tick = Decimal.parse(instrument?.tick_size ?? '0.01');
    const lot = Decimal.parse(instrument?.lot_size ?? '0.001');
    const patch: Partial<Order> = {};
    if (request.limit_price)
      patch.limit_price = Decimal.parse(request.limit_price)
        .roundToStep(tick)
        .toString();
    if (request.trigger_price)
      patch.trigger_price = Decimal.parse(request.trigger_price)
        .roundToStep(tick)
        .toString();
    if (request.quantity_base) {
      const qty = Decimal.parse(request.quantity_base).floorToStep(lot);
      if (qty.cmp(Decimal.parse(order.filled_base)) <= 0)
        throw apiError(
          422,
          'INVALID_LOT_SIZE',
          'New quantity must exceed the filled quantity.'
        );
      patch.quantity_base = qty.toString();
    }
    const updated = this.update(orderId, patch) ?? order;
    this.record('order.amend', 'order', orderId, {...patch});
    this.tryFill(orderId);
    return updated;
  }

  /**
   * Lists positions.
   * @param accountId Optional account.
   * @return Positions.
   */
  async listPositions(accountId?: string): Promise<PositionList> {
    return {
      positions: this.positionList().filter(
        p => !accountId || p.account_id === accountId
      ),
    };
  }

  /**
   * Lists fills.
   * @param accountId Optional account.
   * @return Fills, newest first.
   */
  async listFills(accountId?: string): Promise<FillList> {
    return {
      fills: this.fills.filter(f => !accountId || f.account_id === accountId),
    };
  }

  /**
   * Builds the reduce-only request that closes part of a position.
   * @param positionId Position id.
   * @param request Close request.
   * @return Intent request.
   */
  private closeRequest(
    positionId: string,
    request: ClosePositionRequest
  ): OrderIntentRequest {
    const position = this.positionList().find(
      p => p.position_id === positionId
    );
    if (!position)
      throw apiError(404, 'NOT_FOUND', 'Position not found or already flat.');
    const instrument = this.instrument(position.instrument_id);
    const lot = Decimal.parse(instrument?.lot_size ?? '0.001');
    const fraction = Decimal.parse(request.fraction);
    if (fraction.isZero() || fraction.cmp(Decimal.parse('1')) > 0)
      throw apiError(
        422,
        'VALIDATION_FAILED',
        'Fraction must be between 0 and 1.'
      );
    return {
      account_id: position.account_id,
      instrument_id: position.instrument_id,
      side: position.side === 'long' ? 'sell' : 'buy',
      position_effect: 'close',
      order_type: request.order_type,
      quantity_base: Decimal.parse(position.quantity_base)
        .mul(fraction)
        .floorToStep(lot)
        .toString(),
      limit_price:
        request.order_type === 'limit' ? (request.limit_price ?? null) : null,
      trigger_price: null,
      trigger_basis: null,
      time_in_force: request.order_type === 'market' ? 'IOC' : 'GTC',
      reduce_only: true,
      margin_mode: 'isolated',
      leverage: null,
      bracket: null,
      mode: position.mode,
      source: 'MANUAL_TICKET',
      strategy_version_id: null,
      signal_id: null,
      expires_at_ms: null,
      preview_token: request.preview_token ?? null,
    };
  }

  /**
   * Previews a reduce-only close.
   * @param positionId Position id.
   * @param request Close request.
   * @return Preview.
   */
  async closePositionPreview(
    positionId: string,
    request: ClosePositionRequest
  ): Promise<OrderPreview> {
    return this.previewOrder(this.closeRequest(positionId, request));
  }

  /**
   * Closes part of a position.
   * @param positionId Position id.
   * @param request Close request with preview token.
   * @param idempotencyKey Key.
   * @return Submission result.
   */
  async closePosition(
    positionId: string,
    request: ClosePositionRequest,
    idempotencyKey: string
  ): Promise<SubmitIntentResponse> {
    return this.submitIntent(
      this.closeRequest(positionId, request),
      idempotencyKey
    );
  }

  /**
   * Lists kill switches.
   * @return States.
   */
  async getKillSwitches(): Promise<KillSwitchList> {
    return {switches: [...this.killSwitches.values()]};
  }

  /**
   * Engages or releases a kill switch.
   * @param request Request.
   * @return New state.
   */
  async setKillSwitch(request: KillSwitchRequest): Promise<KillSwitchState> {
    if (!request.reason.trim())
      throw apiError(422, 'VALIDATION_FAILED', 'A reason is required.');
    const state: KillSwitchState = {
      scope: request.scope,
      target_id: request.target_id ?? null,
      engaged: request.engage,
      cancel_open_orders: request.engage && request.cancel_open_orders,
      flatten_positions: request.engage && request.flatten_positions,
      reason: request.reason,
      actor: DEMO_USER_ID,
      changed_at_ms: this.now(),
    };
    this.killSwitches.set(`${request.scope}:${request.target_id ?? ''}`, state);
    this.record(
      request.engage ? 'kill_switch.engage' : 'kill_switch.release',
      'kill_switch',
      request.scope,
      {...state}
    );
    this.emit({type: 'risk.kill_switch', payload: state});
    if (state.engaged && state.cancel_open_orders) {
      for (const order of this.orders.values()) {
        if (
          !TERMINAL.has(order.state) &&
          this.inScope(
            state,
            order.account_id,
            order.strategy_version_id ?? null
          )
        ) {
          this.update(order.order_id, {
            state: 'CANCELED',
            state_reason: 'Canceled by kill switch.',
          });
        }
      }
    }
    if (
      state.engaged &&
      state.flatten_positions &&
      this.inScope(state, DEMO_ACCOUNT_ID, null)
    ) {
      for (const position of this.positionList()) {
        await this.submitIntent(
          {
            ...this.closeRequest(position.position_id, {
              fraction: '1',
              order_type: 'market',
            }),
            source: 'EMERGENCY',
          },
          this.nextId('demo-flatten')
        );
      }
    }
    return state;
  }

  /**
   * Returns whether an entity is covered by a kill switch scope.
   * @param state Kill switch.
   * @param accountId Account id.
   * @param strategyVersionId Strategy version, if any.
   * @return True when covered.
   */
  private inScope(
    state: KillSwitchState,
    accountId: string,
    strategyVersionId: string | null
  ): boolean {
    if (state.scope === 'GLOBAL') return true;
    if (state.scope === 'ACCOUNT') return state.target_id === accountId;
    return strategyVersionId !== null && state.target_id === strategyVersionId;
  }

  /**
   * Lists audit events newest first and verifies the chain.
   * @param accountId Optional account filter.
   * @param limit Maximum rows.
   * @return Events and chain validity.
   */
  async listAuditEvents(
    accountId?: string,
    limit = 200
  ): Promise<AuditEventList> {
    let valid = true;
    let prev = 'genesis';
    for (const event of this.audit) {
      const {hash, ...rest} = event;
      if (
        event.prev_hash !== prev ||
        sha256Hex(prev + canonicalJson(rest)) !== hash
      )
        valid = false;
      prev = hash;
    }
    const events = this.audit
      .filter(e => !accountId || e.account_id === accountId)
      .slice(-limit)
      .reverse();
    return {events, chain_valid: valid};
  }

  /**
   * Lists deployments.
   * @return Deployments.
   */
  async listDeployments(): Promise<DeploymentList> {
    return {deployments: [...this.deployments.values()]};
  }

  /**
   * Creates a deployment; AUTO_LIVE waits for consent that DEMO never grants.
   * @param request Request.
   * @return Deployment.
   */
  async createDeployment(request: DeploymentRequest): Promise<Deployment> {
    if (request.account_id !== DEMO_ACCOUNT_ID)
      throw apiError(404, 'NOT_FOUND', 'Unknown account.');
    const now = this.now();
    const deployment: Deployment = {
      deployment_id: this.nextId('demo-dep'),
      tenant_id: DEMO_TENANT_ID,
      strategy_version_id: request.strategy_version_id,
      account_id: request.account_id,
      mode: request.mode,
      state: request.mode === 'AUTO_LIVE' ? 'PENDING_CONSENT' : 'ENABLED',
      limits: request.limits,
      sizing: request.sizing,
      live_consent: null,
      created_at_ms: now,
      updated_at_ms: now,
    };
    this.deployments.set(deployment.deployment_id, deployment);
    this.record('deployment.create', 'deployment', deployment.deployment_id, {
      mode: request.mode,
    });
    return deployment;
  }

  /**
   * Disables a deployment.
   * @param deploymentId Deployment id.
   * @return Updated deployment.
   */
  async disableDeployment(deploymentId: string): Promise<Deployment> {
    const deployment = this.deployments.get(deploymentId);
    if (!deployment) throw apiError(404, 'NOT_FOUND', 'Deployment not found.');
    const next: Deployment = {
      ...deployment,
      state: 'DISABLED',
      updated_at_ms: this.now(),
    };
    this.deployments.set(deploymentId, next);
    this.record('deployment.disable', 'deployment', deploymentId, {});
    return next;
  }
}
