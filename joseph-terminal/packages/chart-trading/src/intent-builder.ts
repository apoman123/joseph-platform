/**
 * @fileoverview Builds contract `OrderIntentRequest`s from ticket inputs.
 *
 * Quantities are floored to the lot size (rounding never increases
 * exposure) and prices are rounded to the tick size, all with exact
 * `Decimal` arithmetic. The server re-validates everything; these checks
 * exist so the user sees problems before previewing.
 *
 * Exchange-listed stocks and ETFs follow the cash-equity rules of
 * `equity.ts` (whole shares or TWSE board/odd lots, tick ladders, daily
 * price limits, DAY/GTC, no leverage); published rates cannot be traded.
 */
import {
  Decimal,
  parseInstrumentId,
  type AccountSummary,
  type Instrument,
  type IntentSource,
  type MarginMode,
  type OrderIntentRequest,
  type OrderMode,
  type OrderSide,
  type OrderType,
  type PositionEffect,
  type PriceBasis,
  type TakeProfitSpec,
  type TimeInForce,
  type BarCalendar,
} from '@joseph/contracts';
import {
  accountInstrumentMismatch,
  boardLotOf,
  closedMarketProblem,
  equityTimeInForce,
  heldShares,
  formatEquityPrice,
  formatShares,
  isCashEquityId,
  isOnTickLadder,
  isRateInstrumentId,
  outsideRthSupport,
  parseEquityQuantity,
  priceLimitBand,
  roundToTickLadder,
  tickAt,
  withOutsideRth,
  withinPriceLimit,
} from './equity';
import {formatStep} from './format';

/** Raw ticket inputs as typed by the user (strings keep exactness). */
export interface TicketInputs {
  /** Canonical instrument id. */
  instrumentId: string;
  /** Selected trading account, or null when none is selected. */
  accountId: string | null;
  /** Buy or sell. */
  side: OrderSide;
  /** Order type. */
  orderType: OrderType;
  /** Base quantity text. */
  quantity: string;
  /** Limit price text (limit and stop-limit). */
  limitPrice: string;
  /** Trigger price text (stop orders). */
  triggerPrice: string;
  /** Price used to trigger stop orders. */
  triggerBasis: PriceBasis;
  /** Time in force (`DAY` only for exchange-session securities). */
  timeInForce: TimeInForce;
  /** Reduce-only flag. */
  reduceOnly: boolean;
  /** Leverage text, empty for venue default. */
  leverage: string;
  /** Margin mode. */
  marginMode: MarginMode;
  /** Stop-loss trigger text, empty for none. */
  stopLoss: string;
  /** Take-profit trigger text, empty for none. */
  takeProfit: string;
  /** Order mode; LIVE only when the account permits it. */
  mode: OrderMode;
  /** Position effect. */
  positionEffect: PositionEffect;
  /** Origin of the intent. */
  source: IntentSource;
  /** Cash equities with a board lot: whole lots (張) or odd-lot shares. */
  lotMode?: 'board' | 'odd';
  /** IBKR: allow fills outside regular trading hours. */
  outsideRth?: boolean;
}

/** Severity of a ticket issue. */
export type IssueSeverity = 'error' | 'warning';

/** A problem found in the ticket inputs. */
export interface TicketIssue {
  /** Input field the issue belongs to. */
  field: keyof TicketInputs | 'notional';
  /** Error blocks previewing; warning is informational. */
  severity: IssueSeverity;
  /** Plain-language explanation including how to fix it. */
  message: string;
}

/** Result of building an intent. */
export interface BuildResult {
  /** The request, or null when blocking errors exist. */
  request: OrderIntentRequest | null;
  /** Problems found (errors and warnings). */
  issues: TicketIssue[];
  /** Normalized quantity actually sent, when parseable. */
  quantity: Decimal | null;
  /** Normalized limit price, when applicable. */
  limitPrice: Decimal | null;
  /** Normalized trigger price, when applicable. */
  triggerPrice: Decimal | null;
}

/** Instrument precision subset needed for rounding. */
export type InstrumentPrecision = Pick<
  Instrument,
  | 'instrument_id'
  | 'tick_size'
  | 'lot_size'
  | 'min_qty'
  | 'max_qty'
  | 'min_notional'
  | 'max_leverage'
  | 'display_symbol'
  | 'status'
> &
  Partial<
    Pick<
      Instrument,
      | 'tick_ladder'
      | 'price_limit_pct'
      | 'board_lot'
      | 'calendar'
      | 'listing_exchange'
      | 'name'
      | 'local_name'
    >
  > & {key: Pick<Instrument['key'], 'canonical_base' | 'quote'>};

/** Account and market context of cash-equity orders. */
export interface TicketContext {
  /** Selected account (broker rules: time in force, outside RTH). */
  account?: AccountSummary | null;
  /** Previous session close, the reference of daily price limits. */
  reference?: Decimal | null;
  /** Session calendar of the instrument (closed-market rules). */
  calendar?: BarCalendar | null;
  /** Clock for the closed-market rules (UTC ms). */
  nowMs?: number;
}

/**
 * Creates default ticket inputs.
 * @param instrumentId Canonical instrument id.
 * @param accountId Account id or null.
 * @return Default inputs (paper, limit buy, GTC).
 */
export function defaultTicketInputs(
  instrumentId: string,
  accountId: string | null
): TicketInputs {
  return {
    instrumentId,
    accountId,
    side: 'buy',
    orderType: 'limit',
    quantity: '',
    limitPrice: '',
    triggerPrice: '',
    triggerBasis: 'mark',
    timeInForce: 'GTC',
    reduceOnly: false,
    leverage: '',
    marginMode: 'isolated',
    stopLoss: '',
    takeProfit: '',
    mode: 'PAPER',
    positionEffect: 'open',
    source: 'MANUAL_TICKET',
  };
}

/**
 * Parses optional decimal text.
 * @param text User text.
 * @return Decimal, null when empty, or undefined when invalid.
 */
function parseOptional(text: string): Decimal | null | undefined {
  const trimmed = text.trim().replace(/,/g, '');
  if (trimmed === '') return null;
  return Decimal.isValid(trimmed) ? Decimal.parse(trimmed) : undefined;
}

/**
 * Returns whether an order type needs a limit price.
 * @param type Order type.
 * @return True for limit and stop-limit.
 */
export function needsLimitPrice(type: OrderType): boolean {
  return type === 'limit' || type === 'stop_limit';
}

/**
 * Returns whether an order type needs a trigger price.
 * @param type Order type.
 * @return True for stop-market and stop-limit.
 */
export function needsTriggerPrice(type: OrderType): boolean {
  return type === 'stop_market' || type === 'stop_limit';
}

/**
 * Validates and normalizes ticket inputs into an intent request.
 * @param inputs Raw inputs.
 * @param instrument Instrument precision, or null while loading.
 * @param markPrice Reference mark price for notional checks, if known.
 * @param context Account and previous close (cash equities).
 * @return Request (or null) and issues.
 */
export function buildIntentRequest(
  inputs: TicketInputs,
  instrument: InstrumentPrecision | null,
  markPrice: Decimal | null,
  context: TicketContext = {}
): BuildResult {
  if (isRateInstrumentId(inputs.instrumentId)) {
    return {
      request: null,
      issues: [
        {
          field: 'instrumentId',
          severity: 'error',
          message: 'Rates are published values and cannot be traded.',
        },
      ],
      quantity: null,
      limitPrice: null,
      triggerPrice: null,
    };
  }
  if (isCashEquityId(inputs.instrumentId)) {
    return buildEquityIntentRequest(inputs, instrument, markPrice, context);
  }
  const issues: TicketIssue[] = [];
  const error = (field: TicketIssue['field'], message: string) =>
    issues.push({field, severity: 'error', message});
  const warn = (field: TicketIssue['field'], message: string) =>
    issues.push({field, severity: 'warning', message});
  const empty: BuildResult = {
    request: null,
    issues,
    quantity: null,
    limitPrice: null,
    triggerPrice: null,
  };
  if (!instrument) {
    error('instrumentId', 'Instrument details are still loading.');
    return empty;
  }
  if (instrument.status !== 'TRADING') {
    error(
      'instrumentId',
      `This instrument is ${instrument.status.toLowerCase()} at the venue; new orders are blocked.`
    );
  }
  if (!inputs.accountId) {
    error('accountId', 'Choose a trading account.');
  }
  const tick = Decimal.parse(instrument.tick_size);
  const lot = Decimal.parse(instrument.lot_size);

  const rawQty = parseOptional(inputs.quantity);
  let quantity: Decimal | null = null;
  if (rawQty === null) {
    error('quantity', 'Enter a quantity.');
  } else if (rawQty === undefined || rawQty.isNegative()) {
    error('quantity', 'Quantity must be a positive number.');
  } else {
    quantity = rawQty.floorToStep(lot);
    if (!quantity.eq(rawQty)) {
      warn(
        'quantity',
        `Rounded down to the lot size ${instrument.lot_size}: ${formatStep(quantity.toString(), instrument.lot_size)}.`
      );
    }
    if (quantity.isZero()) {
      error(
        'quantity',
        `Quantity rounds to zero. The lot size is ${instrument.lot_size}.`
      );
    } else if (quantity.cmp(Decimal.parse(instrument.min_qty)) < 0) {
      error('quantity', `Minimum quantity is ${instrument.min_qty}.`);
    }
    if (
      instrument.max_qty &&
      quantity.cmp(Decimal.parse(instrument.max_qty)) > 0
    ) {
      error('quantity', `Maximum quantity is ${instrument.max_qty}.`);
    }
  }

  const price = (
    text: string,
    field: 'limitPrice' | 'triggerPrice',
    label: string
  ): Decimal | null => {
    const parsed = parseOptional(text);
    if (parsed === null) {
      error(field, `Enter a ${label}.`);
      return null;
    }
    if (parsed === undefined || parsed.isNegative() || parsed.isZero()) {
      error(field, `${capitalize(label)} must be a positive number.`);
      return null;
    }
    const rounded = parsed.roundToStep(tick);
    if (!rounded.eq(parsed)) {
      warn(
        field,
        `Rounded to the tick size ${instrument.tick_size}: ${formatStep(rounded.toString(), instrument.tick_size)}.`
      );
    }
    return rounded;
  };
  const limitPrice = needsLimitPrice(inputs.orderType)
    ? price(inputs.limitPrice, 'limitPrice', 'limit price')
    : null;
  const triggerPrice = needsTriggerPrice(inputs.orderType)
    ? price(inputs.triggerPrice, 'triggerPrice', 'trigger price')
    : null;

  const leverage = parseOptional(inputs.leverage);
  if (leverage === undefined || (leverage !== null && leverage.isZero())) {
    error('leverage', 'Leverage must be a positive number.');
  } else if (
    leverage &&
    instrument.max_leverage &&
    leverage.cmp(Decimal.parse(instrument.max_leverage)) > 0
  ) {
    error('leverage', `Maximum leverage is ${instrument.max_leverage}x.`);
  }

  const reference = limitPrice ?? triggerPrice ?? markPrice;
  if (quantity && reference && instrument.min_notional) {
    const notional = quantity.mul(reference);
    if (notional.cmp(Decimal.parse(instrument.min_notional)) < 0) {
      error(
        'notional',
        `Order value ${notional.toFixed(2)} is below the venue minimum of ${instrument.min_notional} ${instrument.key.quote}.`
      );
    }
  }

  const stopLoss = parseOptional(inputs.stopLoss);
  const takeProfit = parseOptional(inputs.takeProfit);
  const entry = reference;
  if (stopLoss === undefined) error('stopLoss', 'Stop-loss must be a number.');
  if (takeProfit === undefined) {
    error('takeProfit', 'Take-profit must be a number.');
  }
  const roundedStop = stopLoss ? stopLoss.roundToStep(tick) : null;
  const roundedTarget = takeProfit ? takeProfit.roundToStep(tick) : null;
  if (entry && roundedStop) {
    const wrongSide =
      inputs.side === 'buy'
        ? roundedStop.cmp(entry) >= 0
        : roundedStop.cmp(entry) <= 0;
    if (wrongSide) {
      error(
        'stopLoss',
        inputs.side === 'buy'
          ? 'A buy stop-loss must be below the entry price.'
          : 'A sell stop-loss must be above the entry price.'
      );
    }
  }
  if (entry && roundedTarget) {
    const wrongSide =
      inputs.side === 'buy'
        ? roundedTarget.cmp(entry) <= 0
        : roundedTarget.cmp(entry) >= 0;
    if (wrongSide) {
      error(
        'takeProfit',
        inputs.side === 'buy'
          ? 'A buy take-profit must be above the entry price.'
          : 'A sell take-profit must be below the entry price.'
      );
    }
  }
  if (inputs.reduceOnly && (roundedStop || roundedTarget)) {
    warn(
      'reduceOnly',
      'Reduce-only orders ignore attached stop-loss and take-profit.'
    );
  }

  if (issues.some(issue => issue.severity === 'error') || !quantity) {
    return {...empty, quantity, limitPrice, triggerPrice};
  }
  const bracket =
    !inputs.reduceOnly && (roundedStop || roundedTarget)
      ? {
          stop_loss: roundedStop
            ? {trigger: roundedStop.toString(), basis: 'mark' as const}
            : null,
          take_profit: roundedTarget
            ? ([{trigger: roundedTarget.toString(), fraction: '1'}] as [
                TakeProfitSpec,
              ])
            : ([] as []),
        }
      : null;
  const request: OrderIntentRequest = {
    account_id: inputs.accountId ?? '',
    instrument_id: instrument.instrument_id,
    side: inputs.side,
    position_effect: inputs.reduceOnly ? 'reduce' : inputs.positionEffect,
    order_type: inputs.orderType,
    quantity_base: quantity.toString(),
    limit_price: limitPrice ? limitPrice.toString() : null,
    trigger_price: triggerPrice ? triggerPrice.toString() : null,
    trigger_basis: triggerPrice ? inputs.triggerBasis : null,
    time_in_force: inputs.orderType === 'market' ? 'IOC' : inputs.timeInForce,
    reduce_only: inputs.reduceOnly,
    margin_mode: inputs.marginMode,
    leverage: leverage ? leverage.toString() : null,
    bracket,
    mode: inputs.mode,
    source: inputs.source,
    strategy_version_id: null,
    signal_id: null,
    expires_at_ms: null,
    preview_token: null,
  };
  return {request, issues, quantity, limitPrice, triggerPrice};
}

/**
 * Validates and normalizes a cash-equity order (stock or ETF): whole
 * shares or TWSE board/odd lots, tick-ladder prices inside the daily price
 * limit, broker time in force and IBKR's outside-RTH flag. No leverage,
 * reduce-only, margin or attached stop/target.
 * @param inputs Raw inputs.
 * @param instrument Instrument (with ladder, limit and board lot), or null.
 * @param markPrice Reference mark/last price, if known.
 * @param context Account and previous close.
 * @return Request (or null) and issues.
 */
export function buildEquityIntentRequest(
  inputs: TicketInputs,
  instrument: InstrumentPrecision | null,
  markPrice: Decimal | null,
  context: TicketContext = {}
): BuildResult {
  const issues: TicketIssue[] = [];
  const error = (field: TicketIssue['field'], message: string) =>
    issues.push({field, severity: 'error', message});
  const warn = (field: TicketIssue['field'], message: string) =>
    issues.push({field, severity: 'warning', message});
  const empty: BuildResult = {
    request: null,
    issues,
    quantity: null,
    limitPrice: null,
    triggerPrice: null,
  };
  if (!instrument) {
    error('instrumentId', 'Instrument details are still loading.');
    return empty;
  }
  if (instrument.status !== 'TRADING') {
    error(
      'instrumentId',
      `This instrument is ${instrument.status.toLowerCase()} at the exchange; new orders are blocked.`
    );
  }
  const account = context.account ?? null;
  if (!inputs.accountId) error('accountId', 'Choose a broker account.');
  if (account) {
    const mismatch = accountInstrumentMismatch(
      account,
      instrument.instrument_id,
      instrument
    );
    if (mismatch) error('accountId', mismatch);
  }
  const hasBoardLot = boardLotOf(instrument) !== null;
  const oddLot = hasBoardLot && inputs.lotMode === 'odd';
  const parsed = parseEquityQuantity(
    instrument,
    oddLot ? 'odd' : 'board',
    inputs.quantity
  );
  const quantity = parsed.shares;
  if (parsed.error) error('quantity', parsed.error);
  if (quantity) {
    if (quantity.cmp(Decimal.parse(instrument.min_qty)) < 0) {
      error('quantity', `Minimum quantity is ${instrument.min_qty} shares.`);
    }
    if (
      instrument.max_qty &&
      quantity.cmp(Decimal.parse(instrument.max_qty)) > 0
    ) {
      error('quantity', `Maximum quantity is ${instrument.max_qty} shares.`);
    }
    if (
      account &&
      inputs.side === 'sell' &&
      account.capabilities.short_selling === false
    ) {
      const held = heldShares(account, instrument.instrument_id);
      if (quantity.cmp(held) > 0) {
        error(
          'quantity',
          `This cash account cannot sell short: it holds ${held.isZero() ? 'no shares' : formatShares(instrument, held)}.`
        );
      }
    }
  }
  if (oddLot) {
    if (account && account.capabilities.odd_lot !== true) {
      error('lotMode', 'This account cannot send intraday odd-lot orders.');
    }
    if (inputs.orderType !== 'limit') {
      error('orderType', 'Intraday odd-lot orders are limit orders only.');
    }
  }
  if (account && !account.capabilities.order_types.includes(inputs.orderType)) {
    error(
      'orderType',
      `This account does not offer ${ORDER_TYPE_NAMES[inputs.orderType]} orders.`
    );
  }
  const band = priceLimitBand(instrument, context.reference ?? null);
  const price = (
    text: string,
    field: 'limitPrice' | 'triggerPrice',
    label: string
  ): Decimal | null => {
    const raw = text.trim().replace(/,/g, '');
    if (raw === '') {
      error(field, `Enter a ${label}.`);
      return null;
    }
    if (!Decimal.isValid(raw) || Decimal.parse(raw).cmp(Decimal.zero()) <= 0) {
      error(field, `${capitalize(label)} must be a positive number.`);
      return null;
    }
    const parsedPrice = Decimal.parse(raw);
    let rounded = parsedPrice;
    if (!isOnTickLadder(instrument, parsedPrice)) {
      rounded = roundToTickLadder(instrument, parsedPrice);
      const ladder = (instrument.tick_ladder ?? []).length > 0;
      warn(
        field,
        `Rounded to the ${ladder ? 'tick ladder' : 'tick size'} (${tickAt(instrument, rounded).toString()} at this price): ${formatEquityPrice(instrument, rounded)}.`
      );
    }
    if (band && !withinPriceLimit(band, rounded)) {
      error(
        field,
        `${capitalize(label)} ${formatEquityPrice(instrument, rounded)} is outside today's price limit ${formatEquityPrice(instrument, band.lower)} – ${formatEquityPrice(instrument, band.upper)} (±${band.pct.toString()}% of the ${formatEquityPrice(instrument, band.reference)} reference close).`
      );
    }
    return rounded;
  };
  const limitPrice = needsLimitPrice(inputs.orderType)
    ? price(inputs.limitPrice, 'limitPrice', 'limit price')
    : null;
  const triggerPrice = needsTriggerPrice(inputs.orderType)
    ? price(inputs.triggerPrice, 'triggerPrice', 'trigger price')
    : null;

  const tif = equityTimeInForce(account, oddLot ? 'odd' : 'board');
  if (oddLot && inputs.timeInForce !== 'DAY') {
    error('timeInForce', 'Intraday odd-lot orders are DAY (ROD) only.');
  } else if (account && !tif.options.includes(inputs.timeInForce)) {
    error(
      'timeInForce',
      tif.options.length > 0
        ? `This account accepts ${tif.options.join(', ')} for stocks and ETFs.`
        : (tif.note ?? 'This account lists no time in force.')
    );
  }
  const rth = outsideRthSupport(account);
  if (inputs.outsideRth && rth.shown && !rth.enabled && rth.reason) {
    error('outsideRth', rth.reason);
  }
  if (
    inputs.outsideRth &&
    rth.shown &&
    (inputs.orderType === 'market' || inputs.orderType === 'stop_market')
  ) {
    error(
      'outsideRth',
      'Outside regular hours applies to limit and stop-limit orders.'
    );
  }
  if (context.calendar) {
    const closed = closedMarketProblem(
      context.calendar,
      context.nowMs ?? Date.now(),
      account,
      {orderType: inputs.orderType, timeInForce: inputs.timeInForce}
    );
    if (closed) error('orderType', closed);
  }

  const reference = limitPrice ?? triggerPrice ?? markPrice;
  if (quantity && reference && instrument.min_notional) {
    const notional = quantity.mul(reference);
    if (notional.cmp(Decimal.parse(instrument.min_notional)) < 0) {
      error(
        'notional',
        `Order value ${notional.toFixed(2)} is below the minimum of ${instrument.min_notional} ${instrument.key.quote}.`
      );
    }
  }

  if (issues.some(issue => issue.severity === 'error') || !quantity) {
    return {...empty, quantity, limitPrice, triggerPrice};
  }
  const request = withOutsideRth(
    {
      account_id: inputs.accountId ?? '',
      instrument_id: instrument.instrument_id,
      side: inputs.side,
      position_effect: inputs.positionEffect,
      order_type: inputs.orderType,
      quantity_base: quantity.toString(),
      limit_price: limitPrice ? limitPrice.toString() : null,
      trigger_price: triggerPrice ? triggerPrice.toString() : null,
      trigger_basis: triggerPrice ? 'last' : null,
      time_in_force: inputs.timeInForce,
      reduce_only: false,
      leverage: null,
      bracket: null,
      mode: inputs.mode,
      source: inputs.source,
      strategy_version_id: null,
      signal_id: null,
      expires_at_ms: null,
      preview_token: null,
    },
    account,
    inputs.outsideRth === true
  );
  return {request, issues, quantity, limitPrice, triggerPrice};
}

/**
 * Capitalizes the first letter.
 * @param text Text.
 * @return Capitalized text.
 */
function capitalize(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** Human names for order types. */
const ORDER_TYPE_NAMES: Readonly<Record<OrderType, string>> = {
  market: 'market',
  limit: 'limit',
  stop_market: 'stop-market',
  stop_limit: 'stop-limit',
};

/**
 * Returns the human name of an order type.
 * @param type Order type.
 * @return Lowercase name such as `stop-market`.
 */
export function orderTypeName(type: OrderType): string {
  return ORDER_TYPE_NAMES[type];
}

/**
 * Builds the confirm-button label naming the exact action.
 * @param request Normalized request (from the preview).
 * @param instrument Instrument precision.
 * @return Text such as `Place paper limit buy 0.010 BTC at 95,000.0`.
 */
export function describeAction(
  request: OrderIntentRequest,
  instrument: InstrumentPrecision
): string {
  if (isCashEquityId(request.instrument_id)) {
    return describeEquityAction(request, instrument);
  }
  const mode = request.mode.toLowerCase();
  const qty = formatStep(request.quantity_base, instrument.lot_size);
  const base = instrument.key.canonical_base;
  const verb = request.reduce_only ? `${request.side} to reduce` : request.side;
  let text = `Place ${mode} ${ORDER_TYPE_NAMES[request.order_type]} ${verb} ${qty} ${base}`;
  if (request.trigger_price) {
    text += ` triggered at ${formatStep(request.trigger_price, instrument.tick_size)}`;
  }
  if (request.limit_price) {
    text += ` at ${formatStep(request.limit_price, instrument.tick_size)}`;
  }
  return text;
}

/**
 * Confirm label of a cash-equity order, e.g. `Place paper limit buy
 * 2 張 (2,000 shares) 9901 at 585 TWD`.
 * @param request Normalized request.
 * @param instrument Instrument.
 * @return Text.
 */
export function describeEquityAction(
  request: OrderIntentRequest,
  instrument: InstrumentPrecision
): string {
  const mode = request.mode.toLowerCase();
  const symbol =
    instrument.display_symbol ||
    parseInstrumentId(request.instrument_id).venueMarketId;
  const qty = formatShares(instrument, Decimal.parse(request.quantity_base));
  let text = `Place ${mode} ${ORDER_TYPE_NAMES[request.order_type]} ${request.side} ${qty} ${symbol}`;
  if (request.trigger_price) {
    text += ` triggered at ${formatEquityPrice(instrument, Decimal.parse(request.trigger_price))}`;
  }
  if (request.limit_price) {
    text += ` at ${formatEquityPrice(instrument, Decimal.parse(request.limit_price))}`;
  }
  return text;
}
