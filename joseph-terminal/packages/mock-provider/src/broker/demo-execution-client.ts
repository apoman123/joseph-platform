/**
 * @fileoverview DEMO paper broker implementing `ExecutionClient` in the
 * browser for offline development and tests.
 *
 * It imitates the server contract (preview tokens, idempotency, risk
 * decisions, order lifecycle, kill switch, audit chain) so features can be
 * built without a backend. It is a simulation: no order ever reaches a
 * venue, and the authoritative Risk Gateway/OMS live in the execution
 * engine.
 *
 * Accounts: the crypto paper account plus simulated broker accounts
 * (IBKR paper for Nasdaq/NYSE/Cboe BZX, a SinoPac Shioaji simulation for TWSE and
 * TPEx, a SinoPac live account without credentials and Cathay awaiting
 * its API SDK). Orders route like the engine: IBKR trades XNYS listings,
 * Taiwan brokers XTAI listings (TWSE and TPEx). Stock orders use whole
 * shares (Taiwan board lots or odd lots), tick-ladder prices and cash, never leverage.
 *
 * LIVE: like the engine, manual LIVE orders need a server-side arming of
 * the account (`grantAccountLiveConsent` with a step-up code, here
 * `DEMO_STEP_UP_CODE`), which expires by the injected clock and can be
 * revoked; without it the mode check fails with `LIVE_NOT_ARMED` and only
 * risk-reducing orders pass (with a warning). No default DEMO account
 * offers LIVE; the opt-in `liveRehearsal` option adds a simulated IBKR live
 * account to rehearse arming. Nothing ever reaches a venue.
 */
import {
  ApiError,
  Decimal,
  type AccountList,
  type AccountLiveConsentRequest,
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
} from '@joseph/contracts';
import {canonicalJson, sha256Hex} from '../strategy/sha256';
import {
  DEMO_ACCOUNTS,
  DEMO_ACCOUNT_ID,
  DEMO_BROKER_INSTRUMENTS,
  DEMO_LIVE_ARM_MAX_MS,
  DEMO_LIVE_REHEARSAL_ACCOUNT,
  DEMO_MARKS,
  DEMO_STEP_UP_CODE,
  DEMO_TENANT_ID,
  DEMO_USER_ID,
  demoRoutingProblem,
  type DemoAccountSpec,
} from './instruments';
import {demoCalendar} from '../market/demo-sessions';

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
  /** Starting paper equity of the crypto paper account. */
  startingEquity?: string;
  /**
   * Adds the simulated IBKR live account (`DEMO_LIVE_REHEARSAL_ACCOUNT`)
   * that offers LIVE, to rehearse server-side arming. Off by default: no
   * DEMO account offers LIVE.
   */
  liveRehearsal?: boolean;
}

/** Server-side LIVE arming of one DEMO account. */
interface LiveArming {
  /** When it was granted. */
  grantedAtMs: number;
  /** When it ends. */
  expiresAtMs: number;
}

/** Sources a human submits through the UI (engine `is_manual`). */
const MANUAL_SOURCES: ReadonlySet<OrderIntentRequest['source']> = new Set<
  OrderIntentRequest['source']
>(['MANUAL_TICKET', 'CHART', 'QUICK_TRADE']);

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
  /** Account holding the position. */
  accountId: string;
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
  private readonly accountSpecs: readonly DemoAccountSpec[];
  private readonly accountEquity = new Map<string, Decimal>();
  private readonly liveArming = new Map<string, LiveArming>();
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
    this.accountSpecs = options.liveRehearsal
      ? [...DEMO_ACCOUNTS, DEMO_LIVE_REHEARSAL_ACCOUNT]
      : DEMO_ACCOUNTS;
    for (const spec of this.accountSpecs) {
      this.accountEquity.set(
        spec.accountId,
        spec.accountId === DEMO_ACCOUNT_ID
          ? this.startingEquity
          : Decimal.parse(spec.startingEquity)
      );
    }
  }

  /**
   * Tick size of an instrument at a price (Taiwan ladder, else tick size).
   * @param instrument Instrument.
   * @param price Price.
   * @return Tick.
   */
  private tickAt(instrument: Instrument | null, price: Decimal): Decimal {
    let tick = Decimal.parse(instrument?.tick_size ?? '0.01');
    const ladder = [...(instrument?.tick_ladder ?? [])].sort((a, b) =>
      Decimal.parse(a.min_price).cmp(Decimal.parse(b.min_price))
    );
    for (const step of ladder) {
      if (price.cmp(Decimal.parse(step.min_price)) >= 0)
        tick = Decimal.parse(step.tick_size);
    }
    return tick;
  }

  /**
   * Engine order rules for an account and instrument: order types the
   * account lists, DAY only for exchange sessions, intraday odd lots
   * (limit DAY, when `odd_lot`), outside RTH (when `outside_rth`), orders
   * while the exchange is closed (market orders queue as DAY only when
   * `queue_market_when_closed`; IOC/FOK need a session) and long-only cash
   * accounts.
   * @param request Normalized request.
   * @param account Account.
   * @param instrument Instrument.
   * @return Failing or warning checks (empty when everything passes).
   */
  private securityChecks(
    request: OrderIntentRequest,
    account: DemoAccountSpec,
    instrument: Instrument
  ): RiskCheckResult[] {
    const results: RiskCheckResult[] = [];
    const fail = (
      check: string,
      domain: RiskCheckResult['domain'],
      message: string,
      code: ErrorCode,
      outcome: RiskCheckResult['outcome'] = 'FAIL'
    ) =>
      results.push({
        check,
        domain,
        outcome,
        message,
        code: outcome === 'FAIL' ? code : null,
        observed: null,
        limit: null,
      });
    const caps = account.capabilities;
    if (!caps.order_types.includes(request.order_type))
      fail(
        'size.order_type',
        'SIZE',
        `${account.label} does not offer ${request.order_type.replace('_', '-')} orders.`,
        'UNSUPPORTED_ORDER_TYPE'
      );
    const calendar = demoCalendar(instrument.calendar ?? '24x7');
    if (calendar.continuous) {
      if (request.time_in_force === 'DAY')
        fail(
          'market.time_in_force',
          'MARKET_STATUS',
          'DAY applies to exchange sessions; 24x7 markets use GTC, IOC or FOK.',
          'UNSUPPORTED_ORDER_TYPE'
        );
      return results;
    }
    if (!this.isSecurity(instrument)) return results;
    const lot =
      instrument.board_lot && Decimal.isValid(instrument.board_lot)
        ? Decimal.parse(instrument.board_lot)
        : null;
    const qty = Decimal.isValid(request.quantity_base)
      ? Decimal.parse(request.quantity_base)
      : Decimal.zero();
    if (lot && !qty.isZero() && qty.cmp(lot) < 0) {
      if (!caps.odd_lot)
        fail(
          'size.board_lot',
          'SIZE',
          'This account cannot send intraday odd-lot orders.',
          'INVALID_LOT_SIZE'
        );
      else if (request.order_type !== 'limit')
        fail(
          'size.board_lot',
          'SIZE',
          'Intraday odd-lot orders must be limit orders.',
          'UNSUPPORTED_ORDER_TYPE'
        );
      else if (request.time_in_force !== 'DAY')
        fail(
          'size.board_lot',
          'SIZE',
          'Intraday odd-lot orders are ROD (DAY) only.',
          'UNSUPPORTED_ORDER_TYPE'
        );
    }
    if (request.outside_rth && !caps.outside_rth)
      fail(
        'market.outside_rth',
        'MARKET_STATUS',
        'This account does not accept outside-RTH orders.',
        'UNSUPPORTED_ORDER_TYPE'
      );
    if (request.side === 'sell' && caps.short_selling === false) {
      const held =
        this.positions.get(request.instrument_id)?.qty ?? Decimal.zero();
      if (qty.cmp(held) > 0)
        fail(
          'exposure.short_selling',
          'EXPOSURE',
          `Long-only cash account: sells are limited to the ${held.toString()} shares held.`,
          'RISK_LIMIT'
        );
    }
    const now = this.now();
    if (!calendar.isOpen(now)) {
      if (request.order_type === 'market') {
        if (!caps.queue_market_when_closed)
          fail(
            'market.session',
            'MARKET_STATUS',
            'Market closed; market orders are rejected until the next open.',
            'VENUE_UNAVAILABLE'
          );
        else if (request.time_in_force !== 'DAY')
          fail(
            'market.session',
            'MARKET_STATUS',
            'Market closed; submit the market order as DAY to queue it for the next open.',
            'UNSUPPORTED_ORDER_TYPE'
          );
        else
          fail(
            'market.session',
            'MARKET_STATUS',
            'Market closed; the market order queues as DAY for the next open.',
            'UNSUPPORTED_ORDER_TYPE',
            'WARN'
          );
      } else if (
        request.time_in_force === 'IOC' ||
        request.time_in_force === 'FOK'
      ) {
        fail(
          'market.session',
          'MARKET_STATUS',
          `Market closed; ${request.time_in_force} orders need an open session.`,
          'UNSUPPORTED_ORDER_TYPE'
        );
      } else {
        fail(
          'market.session',
          'MARKET_STATUS',
          request.outside_rth
            ? 'Outside regular hours; the order may fill in extended hours.'
            : 'Market closed; the order rests until the next open.',
          'UNSUPPORTED_ORDER_TYPE',
          'WARN'
        );
      }
    }
    return results;
  }

  /**
   * Whether an instrument is a cash security (stock or ETF).
   * @param instrument Instrument.
   * @return True for stocks and ETFs.
   */
  private isSecurity(instrument: Instrument | null): boolean {
    const type = instrument?.key.market_type;
    return type === 'stock' || type === 'etf';
  }

  /**
   * Finds one of this broker's accounts.
   * @param accountId Account id.
   * @return Spec or null.
   */
  private spec(accountId: string): DemoAccountSpec | null {
    return this.accountSpecs.find(a => a.accountId === accountId) ?? null;
  }

  /**
   * End of an account's unexpired, unrevoked LIVE arming.
   * @param accountId Account id.
   * @return End time, or null when not armed.
   */
  private armedUntil(accountId: string): number | null {
    const arming = this.liveArming.get(accountId);
    return arming && arming.expiresAtMs > this.now()
      ? arming.expiresAtMs
      : null;
  }

  /**
   * Whether a reduce-only request can only shrink the current position
   * (engine `RiskContext.risk_reducing`).
   * @param request Request.
   * @return True for an opposite-side order no larger than the position.
   */
  private riskReducing(request: OrderIntentRequest): boolean {
    if (!request.reduce_only) return false;
    const book = this.positions.get(request.instrument_id);
    if (!book || book.qty.isZero()) return false;
    const opposite = book.qty.isNegative() ? 'buy' : 'sell';
    const qty = Decimal.isValid(request.quantity_base)
      ? Decimal.parse(request.quantity_base)
      : null;
    return (
      request.side === opposite && qty !== null && qty.cmp(book.qty.abs()) <= 0
    );
  }

  /**
   * Mode check of a LIVE request (engine `mode.allowed`).
   * @param request Request.
   * @param account Account, if known.
   * @return Check result.
   */
  private liveModeCheck(
    request: OrderIntentRequest,
    account: DemoAccountSpec | null
  ): RiskCheckResult {
    const result = (
      outcome: RiskCheckResult['outcome'],
      message: string,
      code: ErrorCode | null = null
    ): RiskCheckResult => ({
      check: 'mode.permitted',
      domain: 'MODE',
      outcome,
      message,
      code,
      observed: null,
      limit: null,
    });
    if (!account || !account.modes.includes('LIVE'))
      return result(
        'FAIL',
        'Live trading is not available in DEMO mode.',
        'LIVE_TRADING_DISABLED'
      );
    if (account.capabilities.status !== 'AVAILABLE')
      return result(
        'FAIL',
        'The venue adapter cannot trade for this account.',
        'TRADE_MODE_DISABLED'
      );
    if (request.source === 'STRATEGY')
      return result(
        'FAIL',
        'AUTO_LIVE needs an enabled deployment with valid consent.',
        'TRADE_MODE_DISABLED'
      );
    if (!MANUAL_SOURCES.has(request.source))
      return result('PASS', 'Live trading is permitted for this scope.');
    const until = this.armedUntil(account.accountId);
    if (until !== null)
      return result(
        'PASS',
        `Live is armed until ${new Date(until).toISOString().slice(11, 16)} UTC (DEMO rehearsal: simulated, nothing reaches a broker).`
      );
    if (this.riskReducing(request))
      return result(
        'WARN',
        'Live trading is not armed; risk-reducing order allowed.'
      );
    return result(
      'FAIL',
      'Live trading is not armed for this account. Arm it with your step-up code first.',
      'LIVE_NOT_ARMED'
    );
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
   * @param accountId Account the event belongs to.
   */
  private record(
    action: string,
    entityType: string,
    entityId: string,
    payload: Record<string, unknown>,
    accountId: string = DEMO_ACCOUNT_ID
  ): void {
    const prevHash = this.audit[this.audit.length - 1]?.hash ?? 'genesis';
    const base = {
      event_id: this.nextId('demo-audit'),
      tenant_id: DEMO_TENANT_ID,
      account_id: accountId,
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
    const lot = Decimal.parse(instrument.lot_size);
    const round = (value: string | null | undefined) =>
      value && Decimal.isValid(value)
        ? Decimal.parse(value)
            .roundToStep(this.tickAt(instrument, Decimal.parse(value)))
            .toString()
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
    const account = this.spec(request.account_id);
    if (request.mode === 'LIVE') {
      checks.push(this.liveModeCheck(request, account));
    } else {
      add(
        'mode.permitted',
        'MODE',
        'PASS',
        `${request.mode.toLowerCase()} mode is enabled for this account.`
      );
    }
    if (account)
      add('identity.account', 'IDENTITY', 'PASS', 'Account belongs to you.');
    else
      add(
        'identity.account',
        'IDENTITY',
        'FAIL',
        'Unknown account.',
        'FORBIDDEN'
      );
    if (account && account.accountId !== DEMO_ACCOUNT_ID) {
      const connected =
        account.status === 'CONNECTED' &&
        account.capabilities.status === 'AVAILABLE';
      if (!connected)
        add(
          'account.connected',
          'CONNECTIVITY',
          'FAIL',
          account.capabilities.notes[0] ??
            'The broker account is not connected.',
          'TRADE_MODE_DISABLED'
        );
      else if (request.mode !== 'LIVE' && !account.modes.includes(request.mode))
        add(
          'account.mode',
          'MODE',
          'FAIL',
          `${request.mode.toLowerCase()} orders are not enabled for this account.`,
          'TRADE_MODE_DISABLED'
        );
      else
        add(
          'account.connected',
          'CONNECTIVITY',
          'PASS',
          `${account.label} is connected (simulated).`
        );
    }
    if (account && instrument) {
      const problem = demoRoutingProblem(account, instrument);
      if (problem)
        add('account.routing', 'ACCOUNT', 'FAIL', problem, 'UNSUPPORTED');
      for (const check of this.securityChecks(request, account, instrument))
        add(
          check.check,
          check.domain,
          check.outcome,
          check.message,
          check.code,
          check.observed,
          check.limit
        );
    }
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
      const boardLot =
        instrument.board_lot && Decimal.isValid(instrument.board_lot)
          ? Decimal.parse(instrument.board_lot)
          : null;
      if (
        boardLot &&
        !qty.isZero() &&
        !qty.isMultipleOf(boardLot) &&
        qty.cmp(boardLot) >= 0
      ) {
        add(
          'size.board_lot',
          'SIZE',
          'FAIL',
          `${instrument.listing_exchange ?? 'TWSE'} orders are whole board lots of ${boardLot.toString()} shares or odd lots below ${boardLot.toString()} shares.`,
          'INVALID_LOT_SIZE',
          qty.toString(),
          boardLot.toString()
        );
      }
      const offLadder = [request.limit_price, request.trigger_price].find(
        price =>
          price &&
          Decimal.isValid(price) &&
          !Decimal.parse(price).isMultipleOf(
            this.tickAt(instrument, Decimal.parse(price))
          )
      );
      if (offLadder) {
        add(
          'size.tick',
          'SIZE',
          'FAIL',
          `${offLadder} is not on the tick ladder.`,
          'INVALID_TICK_SIZE',
          offLadder
        );
      } else if (
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
    const cash = this.isSecurity(instrument);
    const margin =
      cash || leverage.isZero() ? notional : notional.div(leverage);
    const maxNotional =
      account && account.accountId !== DEMO_ACCOUNT_ID
        ? Decimal.parse(account.maxNotional)
        : MAX_NOTIONAL;
    if (notional.cmp(maxNotional) > 0)
      add(
        'exposure.max_notional',
        'EXPOSURE',
        'FAIL',
        'Order exceeds the account notional limit.',
        'RISK_LIMIT',
        notional.toFixed(2),
        maxNotional.toString()
      );
    else
      add(
        'exposure.max_notional',
        'EXPOSURE',
        'PASS',
        'Within the account notional limit.',
        null,
        notional.toFixed(2),
        maxNotional.toString()
      );
    const available = this.availableMargin(request.account_id);
    if (!request.reduce_only && margin.cmp(available) > 0)
      add(
        'account.margin',
        'ACCOUNT',
        'FAIL',
        cash
          ? 'Not enough cash in the account.'
          : 'Not enough available margin.',
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
   * @param accountId Account.
   * @return Available margin.
   */
  private availableMargin(accountId: string): Decimal {
    let used = Decimal.zero();
    for (const [id, book] of this.positions) {
      if (book.accountId !== accountId) continue;
      const mark = this.mark(id);
      if (mark) used = used.add(book.qty.abs().mul(Decimal.parse(mark)));
    }
    return this.equity(accountId).sub(used);
  }

  /**
   * Computes equity including realized and unrealized PnL.
   * @param accountId Account.
   * @return Equity.
   */
  private equity(accountId: string): Decimal {
    let equity = this.accountEquity.get(accountId) ?? Decimal.zero();
    for (const [id, book] of this.positions) {
      if (book.accountId !== accountId) continue;
      equity = equity.add(book.realized);
      const mark = this.mark(id);
      if (mark && !book.qty.isZero())
        equity = equity.add(Decimal.parse(mark).sub(book.entry).mul(book.qty));
    }
    for (const fill of this.fills) {
      if (fill.account_id === accountId)
        equity = equity.sub(Decimal.parse(fill.fee));
    }
    return equity;
  }

  /**
   * Lists accounts.
   * @return The crypto paper account first, then the broker accounts.
   */
  async listAccounts(): Promise<AccountList> {
    return {
      accounts: this.accountSpecs.map(spec => this.summary(spec.accountId)),
    };
  }

  /**
   * Fetches the account summary.
   * @param accountId Account id.
   * @return Summary.
   */
  async getAccountSummary(accountId: string): Promise<AccountSummary> {
    if (!this.spec(accountId))
      throw apiError(404, 'NOT_FOUND', 'Unknown account.');
    return this.summary(accountId);
  }

  /**
   * Arms manual LIVE orders on an account (engine
   * `POST /v1/accounts/{account_id}/live-consent`): refuses paper and
   * unverified accounts, needs the risk acknowledgement, a lifetime of at
   * most `DEMO_LIVE_ARM_MAX_MS` and `DEMO_STEP_UP_CODE`. Every outcome is
   * audited.
   * @param accountId Account id.
   * @param request Step-up code, acknowledgement and lifetime.
   * @return Summary with `live_armed_until_ms`.
   */
  async grantAccountLiveConsent(
    accountId: string,
    request: AccountLiveConsentRequest
  ): Promise<AccountSummary> {
    const spec = this.spec(accountId);
    if (!spec) throw apiError(404, 'NOT_FOUND', 'Unknown account.');
    const duration = request.duration_ms ?? DEMO_LIVE_ARM_MAX_MS;
    const ack: unknown = request.acknowledge_risk;
    let problem: ApiError | null = null;
    if (spec.venue === 'mock')
      problem = apiError(
        403,
        'TRADE_MODE_DISABLED',
        'Paper accounts never trade LIVE; arm a verified venue or broker account.'
      );
    else if (!spec.modes.includes('LIVE'))
      problem = apiError(
        403,
        'TRADE_MODE_DISABLED',
        'This account is not enabled for LIVE; verify the account first.'
      );
    else if (ack !== true)
      problem = apiError(
        422,
        'VALIDATION_FAILED',
        'Risk acknowledgement is required.'
      );
    else if (
      !Number.isInteger(duration) ||
      duration <= 0 ||
      duration > DEMO_LIVE_ARM_MAX_MS
    )
      problem = apiError(
        422,
        'VALIDATION_FAILED',
        `Live arming lasts at most ${DEMO_LIVE_ARM_MAX_MS / 3_600_000} hours.`
      );
    else if (request.step_up_code.trim() !== DEMO_STEP_UP_CODE)
      problem = apiError(
        403,
        'STEP_UP_REQUIRED',
        'Step-up verification failed.'
      );
    else if (spec.capabilities.status !== 'AVAILABLE')
      problem = apiError(
        403,
        'LIVE_TRADING_DISABLED',
        `${spec.label} trading is ${spec.capabilities.status}.`
      );
    if (problem) {
      this.record(
        'live_arming.denied',
        'account',
        accountId,
        {code: problem.body.code, reason: problem.body.message},
        accountId
      );
      throw problem;
    }
    const now = this.now();
    const arming: LiveArming = {grantedAtMs: now, expiresAtMs: now + duration};
    this.liveArming.set(accountId, arming);
    this.record(
      'live_arming.granted',
      'account',
      accountId,
      {
        granted_at_ms: arming.grantedAtMs,
        expires_at_ms: arming.expiresAtMs,
        granted_by: DEMO_USER_ID,
        acknowledge_risk: true,
      },
      accountId
    );
    const summary = this.summary(accountId);
    this.emit({type: 'account.updated', payload: summary});
    return summary;
  }

  /**
   * Disarms LIVE on an account (engine `DELETE .../live-consent`).
   * Idempotent; every call is audited.
   * @param accountId Account id.
   * @return Summary with `live_armed_until_ms` null.
   */
  async revokeAccountLiveConsent(accountId: string): Promise<AccountSummary> {
    if (!this.spec(accountId))
      throw apiError(404, 'NOT_FOUND', 'Unknown account.');
    const until = this.armedUntil(accountId);
    this.liveArming.delete(accountId);
    this.record(
      'live_arming.revoked',
      'account',
      accountId,
      {was_armed_until_ms: until, revoked_by: DEMO_USER_ID},
      accountId
    );
    const summary = this.summary(accountId);
    this.emit({type: 'account.updated', payload: summary});
    return summary;
  }

  /**
   * Builds an account summary.
   * @param accountId Account id (defaults to the crypto paper account).
   * @return Summary.
   */
  private summary(accountId: string = DEMO_ACCOUNT_ID): AccountSummary {
    const spec = this.spec(accountId) ?? DEMO_ACCOUNTS[0];
    if (!spec) throw apiError(404, 'NOT_FOUND', 'Unknown account.');
    const connected = spec.status === 'CONNECTED';
    return {
      account_id: spec.accountId,
      tenant_id: DEMO_TENANT_ID,
      venue: spec.venue,
      label: spec.label,
      status: spec.status,
      modes: [...spec.modes],
      equity: connected ? this.equity(spec.accountId).toFixed(2) : null,
      available_margin: connected
        ? this.availableMargin(spec.accountId).toFixed(2)
        : null,
      currency: spec.currency,
      settled_cash: connected
        ? this.availableMargin(spec.accountId).toFixed(2)
        : null,
      unsettled_cash: connected ? '0.00' : null,
      positions: this.positionList().filter(
        p => p.account_id === spec.accountId
      ),
      open_orders: [...this.orders.values()].filter(
        o => !TERMINAL.has(o.state) && o.account_id === spec.accountId
      ),
      capabilities: spec.capabilities,
      health: spec.health,
      stale: false,
      last_sync_ms: connected ? this.now() : null,
      live_armed_until_ms: this.armedUntil(spec.accountId),
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
    const tick = this.tickAt(instrument, price);
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
      accountId: order.account_id,
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
      fee_asset: this.spec(order.account_id)?.currency ?? 'USD',
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
    // A flat book is re-opened by whichever account trades next.
    const next: PositionBook = {
      ...book,
      accountId: book.qty.isZero() ? order.account_id : book.accountId,
    };
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
    this.emit({type: 'account.updated', payload: this.summary(book.accountId)});
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
    const liquidation =
      book.qty.isZero() || this.isSecurity(instrument)
        ? null
        : (book.qty.isNegative()
            ? book.entry.mul(Decimal.parse('1.5'))
            : book.entry.mul(Decimal.parse('0.5'))
          )
            .roundToStep(tick)
            .toString();
    return {
      position_id: `demo-pos-${instrumentId}`,
      account_id: book.accountId,
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
      mode: this.spec(book.accountId)?.modes.includes('LIVE')
        ? 'LIVE'
        : 'PAPER',
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
    if (
      order.mode === 'LIVE' &&
      !order.reduce_only &&
      this.armedUntil(order.account_id) === null
    )
      throw apiError(
        403,
        'LIVE_NOT_ARMED',
        'Live trading is not armed for this account. Arm it with your step-up code first.'
      );
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
    if (state.engaged && state.flatten_positions) {
      for (const position of this.positionList()) {
        if (!this.inScope(state, position.account_id, null)) continue;
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
    if (!this.spec(request.account_id))
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
