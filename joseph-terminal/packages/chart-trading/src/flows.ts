/**
 * @fileoverview Chart context menu, cancel/close/reverse and kill-switch
 * request models.
 *
 * Close is always reduce-only and can never flip a position. Reverse is two
 * explicit, separately confirmed steps (close → confirmed flat → open)
 * because no venue atomicity is assumed.
 */
import {
  Decimal,
  type ClosePositionRequest,
  type KillSwitchRequest,
  type KillSwitchScope,
  type Order,
  type OrderSide,
  type OrderType,
  type Position,
} from '@joseph/contracts';
import {formatStep} from './format';
import {defaultTicketInputs, type TicketInputs} from './intent-builder';
import {isOpenOrder} from './overlays';

/** One entry of the chart trading context menu. */
export interface ContextMenuItem {
  /** Stable id. */
  id: string;
  /** Menu label, e.g. `Buy limit at 94,980.0`. */
  label: string;
  /** Side. */
  side: OrderSide;
  /** Order type. */
  orderType: Extract<OrderType, 'limit' | 'stop_market'>;
  /** Tick-rounded price. */
  price: string;
  /** Optional hint, e.g. that a limit would fill immediately. */
  hint: string | null;
}

/**
 * Builds the context menu for a right-click at a chart price.
 * @param price Display price under the pointer.
 * @param tickSize Instrument tick size.
 * @param mark Current mark price, if known (used for hints).
 * @return Menu items (empty when the price is invalid).
 */
export function buildContextMenu(
  price: number,
  tickSize: string,
  mark: Decimal | null
): ContextMenuItem[] {
  if (!Number.isFinite(price) || price <= 0) return [];
  const rounded = Decimal.fromNumber(price).roundToStep(
    Decimal.parse(tickSize)
  );
  const text = rounded.toString();
  const shown = formatStep(text, tickSize);
  const above = mark ? rounded.cmp(mark) > 0 : null;
  const hint = (
    side: OrderSide,
    type: 'limit' | 'stop_market'
  ): string | null => {
    if (above === null) return null;
    if (type === 'limit') {
      if (side === 'buy' && above)
        return 'Above the mark: fills immediately as a taker.';
      if (side === 'sell' && !above)
        return 'Below the mark: fills immediately as a taker.';
    } else {
      if (side === 'buy' && !above)
        return 'Below the mark: would trigger immediately.';
      if (side === 'sell' && above)
        return 'Above the mark: would trigger immediately.';
    }
    return null;
  };
  return [
    {
      id: 'buy-limit',
      label: `Buy limit at ${shown}`,
      side: 'buy',
      orderType: 'limit',
      price: text,
      hint: hint('buy', 'limit'),
    },
    {
      id: 'sell-limit',
      label: `Sell limit at ${shown}`,
      side: 'sell',
      orderType: 'limit',
      price: text,
      hint: hint('sell', 'limit'),
    },
    {
      id: 'buy-stop',
      label: `Buy stop at ${shown}`,
      side: 'buy',
      orderType: 'stop_market',
      price: text,
      hint: hint('buy', 'stop_market'),
    },
    {
      id: 'sell-stop',
      label: `Sell stop at ${shown}`,
      side: 'sell',
      orderType: 'stop_market',
      price: text,
      hint: hint('sell', 'stop_market'),
    },
  ];
}

/**
 * Converts a context-menu item into ticket inputs.
 * @param item Selected item.
 * @param base Current ticket inputs (account, mode, quantity are kept).
 * @return Prefilled ticket inputs.
 */
export function ticketFromMenuItem(
  item: ContextMenuItem,
  base: TicketInputs
): TicketInputs {
  return {
    ...base,
    side: item.side,
    orderType: item.orderType,
    limitPrice: item.orderType === 'limit' ? item.price : '',
    triggerPrice: item.orderType === 'stop_market' ? item.price : '',
    source: 'CHART',
  };
}

/** Whether an order can be canceled from the UI, and why not. */
export interface CancelGuard {
  /** Cancel permitted. */
  allowed: boolean;
  /** Explanation when not permitted. */
  reason: string | null;
}

/**
 * Decides whether the UI may request cancellation of an order.
 * @param order Order.
 * @return Guard result.
 */
export function cancelGuard(order: Order): CancelGuard {
  if (!isOpenOrder(order)) {
    return {allowed: false, reason: 'The order is already final.'};
  }
  if (order.state === 'UNKNOWN_RECONCILING') {
    return {
      allowed: false,
      reason:
        'The venue has not confirmed this order yet. Joseph is reconciling by client order id; cancel becomes available once the real state is known. It has not been canceled.',
    };
  }
  if (order.state === 'CANCEL_PENDING') {
    return {
      allowed: false,
      reason: 'A cancel request is already pending at the venue.',
    };
  }
  if (
    order.state === 'SUBMITTING' ||
    order.state === 'RESERVED' ||
    order.state === 'VALIDATED'
  ) {
    return {
      allowed: false,
      reason: 'Wait for the venue to acknowledge the order before canceling.',
    };
  }
  return {allowed: true, reason: null};
}

/** A validated close request plus its plain-language summary. */
export interface ClosePlan {
  /** Contract request. */
  request: ClosePositionRequest;
  /** Quantity that will be closed (floored to the lot). */
  quantity: Decimal;
  /** Summary such as `Close 50% of long 0.020 BTC with a reduce-only market sell`. */
  summary: string;
}

/**
 * Builds a reduce-only close request for part or all of a position.
 * @param position Position to close.
 * @param fraction Fraction in (0, 1] as a decimal string.
 * @param orderType Market or limit.
 * @param lotSize Lot size.
 * @param base Base asset symbol for the summary.
 * @param limitPrice Limit price for limit closes.
 * @return Plan, or an error message.
 */
export function planClose(
  position: Position,
  fraction: string,
  orderType: 'market' | 'limit',
  lotSize: string,
  base: string,
  limitPrice: string | null = null
): ClosePlan | {error: string} {
  if (position.side === 'flat')
    return {error: 'There is no open position to close.'};
  if (!Decimal.isValid(fraction))
    return {error: 'Choose how much of the position to close.'};
  const part = Decimal.parse(fraction);
  if (part.isZero() || part.isNegative() || part.cmp(Decimal.parse('1')) > 0) {
    return {error: 'The close fraction must be between 0 and 100%.'};
  }
  const quantity = Decimal.parse(position.quantity_base)
    .mul(part)
    .floorToStep(Decimal.parse(lotSize));
  if (quantity.isZero())
    return {error: `That fraction is smaller than one lot (${lotSize}).`};
  if (orderType === 'limit' && (!limitPrice || !Decimal.isValid(limitPrice))) {
    return {error: 'Enter a limit price for a limit close.'};
  }
  const pct = part.mul(Decimal.parse('100')).toFixed(0);
  const side = position.side === 'long' ? 'sell' : 'buy';
  return {
    request: {
      fraction: part.toString(),
      order_type: orderType,
      limit_price: orderType === 'limit' ? limitPrice : null,
      preview_token: null,
    },
    quantity,
    summary: `Close ${pct}% of ${position.side} ${formatStep(position.quantity_base, lotSize)} ${base} with a reduce-only ${orderType} ${side}`,
  };
}

/** Reverse flow step. */
export type ReverseStep =
  'CLOSE' | 'AWAITING_FLAT' | 'OPEN' | 'DONE' | 'ABORTED';

/** Two-step reverse plan state. */
export interface ReversePlan {
  /** Position being reversed. */
  positionId: string;
  /** Instrument. */
  instrumentId: string;
  /** Original side. */
  fromSide: 'long' | 'short';
  /** Quantity to reopen on the opposite side. */
  quantity: string;
  /** Current step. */
  step: ReverseStep;
  /** Explanation shown to the user. */
  message: string;
}

/**
 * Starts a reverse plan. Nothing is sent until each step is confirmed.
 * @param position Position to reverse.
 * @return Plan at step CLOSE.
 */
export function startReverse(
  position: Position
): ReversePlan | {error: string} {
  if (position.side === 'flat')
    return {error: 'There is no position to reverse.'};
  return {
    positionId: position.position_id,
    instrumentId: position.instrument_id,
    fromSide: position.side,
    quantity: position.quantity_base,
    step: 'CLOSE',
    message:
      'Step 1 of 2: close the current position with a reduce-only order.',
  };
}

/**
 * Advances the reverse plan after the close was submitted.
 * @param plan Plan at step CLOSE.
 * @return Plan awaiting a confirmed flat position.
 */
export function reverseCloseSubmitted(plan: ReversePlan): ReversePlan {
  if (plan.step !== 'CLOSE') return plan;
  return {
    ...plan,
    step: 'AWAITING_FLAT',
    message: 'Waiting for the venue to confirm the position is flat.',
  };
}

/**
 * Advances the reverse plan when a position update arrives.
 * @param plan Plan.
 * @param position Latest position for the instrument, or null if none.
 * @return Plan at OPEN once flat is confirmed; unchanged otherwise.
 */
export function reversePositionUpdated(
  plan: ReversePlan,
  position: Position | null
): ReversePlan {
  if (plan.step !== 'AWAITING_FLAT') return plan;
  const flat =
    position === null ||
    position.side === 'flat' ||
    Decimal.parse(position.quantity_base).isZero();
  if (!flat) return plan;
  return {
    ...plan,
    step: 'OPEN',
    message:
      'Step 2 of 2: the position is flat. Preview and confirm the opposite order.',
  };
}

/**
 * Builds ticket inputs for the opening step of a reverse.
 * @param plan Plan at step OPEN.
 * @param accountId Account id.
 * @return Ticket inputs for a market order on the opposite side.
 */
export function reverseOpenTicket(
  plan: ReversePlan,
  accountId: string
): TicketInputs {
  return {
    ...defaultTicketInputs(plan.instrumentId, accountId),
    side: plan.fromSide === 'long' ? 'sell' : 'buy',
    orderType: 'market',
    quantity: plan.quantity,
  };
}

/** Kill-switch form values. */
export interface KillSwitchForm {
  /** Scope. */
  scope: KillSwitchScope;
  /** Account or strategy id for scoped switches. */
  targetId: string | null;
  /** Engage (true) or release (false). */
  engage: boolean;
  /** Also cancel open orders (separate explicit choice). */
  cancelOpenOrders: boolean;
  /** Also flatten positions with reduce-only market orders. */
  flattenPositions: boolean;
  /** Reason recorded in the audit log. */
  reason: string;
}

/** Validated kill-switch request plus the exact consequences. */
export interface KillSwitchPlan {
  /** Contract request. */
  request: KillSwitchRequest;
  /** Bullet list describing exactly what will happen. */
  consequences: string[];
}

/**
 * Validates a kill-switch form.
 * @param form Form values.
 * @return Plan, or an error message.
 */
export function planKillSwitch(
  form: KillSwitchForm
): KillSwitchPlan | {error: string} {
  const reason = form.reason.trim();
  if (!reason)
    return {error: 'Enter a reason; it is recorded in the audit log.'};
  if (form.scope !== 'GLOBAL' && !form.targetId) {
    return {
      error: `Choose the ${form.scope.toLowerCase()} this switch applies to.`,
    };
  }
  const target =
    form.scope === 'GLOBAL'
      ? 'all accounts and strategies'
      : `${form.scope.toLowerCase()} ${form.targetId ?? ''}`;
  const consequences: string[] = [];
  if (form.engage) {
    consequences.push(`New opening orders are blocked for ${target}.`);
    consequences.push(
      'Risk-reducing orders (reduce-only closes) stay allowed.'
    );
    consequences.push(
      form.cancelOpenOrders
        ? 'Open orders will be canceled.'
        : 'Open orders stay working.'
    );
    consequences.push(
      form.flattenPositions
        ? 'Positions will be closed with reduce-only market orders.'
        : 'Positions stay open.'
    );
  } else {
    consequences.push(`New opening orders are allowed again for ${target}.`);
    consequences.push('Strategies stay paused until you resume them.');
  }
  return {
    request: {
      scope: form.scope,
      target_id: form.scope === 'GLOBAL' ? null : form.targetId,
      engage: form.engage,
      cancel_open_orders: form.engage && form.cancelOpenOrders,
      flatten_positions: form.engage && form.flattenPositions,
      reason,
    },
    consequences,
  };
}
