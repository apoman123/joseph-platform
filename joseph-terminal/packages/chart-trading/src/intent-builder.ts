/**
 * @fileoverview Builds contract `OrderIntentRequest`s from ticket inputs.
 *
 * Quantities are floored to the lot size (rounding never increases
 * exposure) and prices are rounded to the tick size, all with exact
 * `Decimal` arithmetic. The server re-validates everything; these checks
 * exist so the user sees problems before previewing.
 */
import {
  Decimal,
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
} from '@joseph/contracts';
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
  /** Time in force. */
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
> & {key: Pick<Instrument['key'], 'canonical_base' | 'quote'>};

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
 * @return Request (or null) and issues.
 */
export function buildIntentRequest(
  inputs: TicketInputs,
  instrument: InstrumentPrecision | null,
  markPrice: Decimal | null
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
