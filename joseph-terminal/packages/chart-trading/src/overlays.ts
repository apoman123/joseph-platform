/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Overlay view models for orders, positions and ticket drafts.
 *
 * Only declarative `OverlayLayer`s are produced; the chart host renders
 * them. Unknown-outcome orders are rendered as unknown — never as
 * canceled — and user-authored drafts use the `user` tone. Draft lines show
 * the ticket's unconfirmed inputs: dragging one only edits the ticket, and
 * dragging a working order only proposes an amendment the user must confirm.
 */

import type {OverlayLayer, PriceLineOverlay} from '@joseph/chart-core';
import {
  Decimal,
  type Order,
  type OrderState,
  type Position,
} from '@joseph/contracts';
import {formatStep} from './format';
import {
  needsLimitPrice,
  needsTriggerPrice,
  type TicketInputs,
} from './intent-builder';

/** Layer id for venue orders. */
export const ORDERS_LAYER_ID = 'chart-trading:orders';
/** Layer id for positions. */
export const POSITIONS_LAYER_ID = 'chart-trading:positions';
/** Layer id for the ticket draft. */
export const DRAFT_LAYER_ID = 'chart-trading:draft';

/** Order states that still rest at, or are in flight to, the venue. */
export const OPEN_ORDER_STATES: ReadonlySet<OrderState> = new Set<OrderState>([
  'VALIDATED',
  'RESERVED',
  'SUBMITTING',
  'ACKNOWLEDGED',
  'PARTIALLY_FILLED',
  'CANCEL_PENDING',
  'UNKNOWN_RECONCILING',
]);

/**
 * Returns whether an order is open (non-terminal).
 * @param order Order.
 * @return True when the order is in one of `OPEN_ORDER_STATES`.
 */
export function isOpenOrder(order: Order): boolean {
  return OPEN_ORDER_STATES.has(order.state);
}

/** Human labels for order states. */
const STATE_LABELS: Readonly<Record<OrderState, string>> = {
  DRAFT: 'Draft',
  VALIDATED: 'Validated',
  RESERVED: 'Reserved',
  SUBMITTING: 'Submitting',
  ACKNOWLEDGED: 'Working',
  PARTIALLY_FILLED: 'Partially filled',
  FILLED: 'Filled',
  REJECTED: 'Rejected',
  CANCEL_PENDING: 'Cancel pending',
  CANCELED: 'Canceled',
  EXPIRED: 'Expired',
  FAILED: 'Failed',
  UNKNOWN_RECONCILING: 'Unknown, reconciling',
};

/**
 * Returns the user-facing label of an order state.
 * @param state Order state.
 * @return Label such as `Unknown, reconciling`.
 */
export function orderStateLabel(state: OrderState): string {
  return STATE_LABELS[state];
}

/** Precision needed to label lines. */
export interface LabelPrecision {
  /** Tick size as a decimal string. */
  tickSize: string;
  /** Lot size as a decimal string. */
  lotSize: string;
}

/**
 * Builds the order-lines layer for one instrument.
 * @param orders Orders of the account (any instrument).
 * @param instrumentId Instrument shown on the chart.
 * @param precision Tick and lot sizes for labels.
 * @param amendable Whether the venue supports amending (enables dragging).
 * @return Overlay layer with one line per open order of the instrument at
 *     its limit price, or its trigger price when it has no limit.
 */
export function buildOrderLayer(
  orders: readonly Order[],
  instrumentId: string,
  precision: LabelPrecision,
  amendable: boolean
): OverlayLayer {
  const models: PriceLineOverlay[] = [];
  for (const order of orders) {
    if (order.instrument_id !== instrumentId || !isOpenOrder(order)) continue;
    const price = order.limit_price ?? order.trigger_price;
    if (!price) continue;
    const unknown = order.state === 'UNKNOWN_RECONCILING';
    const pending =
      unknown ||
      order.state === 'SUBMITTING' ||
      order.state === 'CANCEL_PENDING' ||
      order.state === 'RESERVED' ||
      order.state === 'VALIDATED';
    // SL and TP label the stop-loss and take-profit children of a bracket.
    const role =
      order.role === 'STOP_LOSS'
        ? 'SL'
        : order.role === 'TAKE_PROFIT'
          ? 'TP'
          : order.side === 'buy'
            ? 'Buy'
            : 'Sell';
    const qty = formatStep(order.quantity_base, precision.lotSize);
    const filled =
      order.state === 'PARTIALLY_FILLED'
        ? ` (${formatStep(order.filled_base, precision.lotSize)} filled)`
        : '';
    const tone = unknown
      ? 'warning'
      : order.role === 'STOP_LOSS'
        ? 'danger'
        : order.role === 'TAKE_PROFIT'
          ? 'neutral'
          : order.side === 'buy'
            ? 'bid'
            : 'ask';
    models.push({
      kind: 'price-line',
      id: order.order_id,
      price,
      tone,
      lineStyle: pending ? 'dashed' : 'solid',
      label: `${role} ${qty}${filled} · ${orderStateLabel(order.state)}`,
      draggable:
        amendable &&
        (order.state === 'ACKNOWLEDGED' || order.state === 'PARTIALLY_FILLED'),
      pending,
    });
  }
  return {layerId: ORDERS_LAYER_ID, zIndex: 40, visible: true, models};
}

/**
 * Builds the position layer (entry and liquidation lines).
 * @param positions Positions of the account.
 * @param instrumentId Instrument shown on the chart.
 * @param precision Tick and lot sizes for labels.
 * @return Overlay layer with entry and estimated liquidation lines of the
 *     instrument's non-flat positions.
 */
export function buildPositionLayer(
  positions: readonly Position[],
  instrumentId: string,
  precision: LabelPrecision
): OverlayLayer {
  const models: PriceLineOverlay[] = [];
  for (const position of positions) {
    if (position.instrument_id !== instrumentId || position.side === 'flat') {
      continue;
    }
    const side = position.side === 'long' ? 'Long' : 'Short';
    const qty = formatStep(position.quantity_base, precision.lotSize);
    if (position.entry_price) {
      models.push({
        kind: 'price-line',
        id: `${position.position_id}:entry`,
        price: position.entry_price,
        tone: position.side === 'long' ? 'bid' : 'ask',
        lineStyle: 'solid',
        label: `${side} ${qty}${position.stale ? ' · stale' : ''}`,
      });
    }
    if (position.liquidation_price) {
      models.push({
        kind: 'price-line',
        id: `${position.position_id}:liq`,
        price: position.liquidation_price,
        tone: 'danger',
        lineStyle: 'dotted',
        label: 'Liquidation (estimate)',
      });
    }
  }
  return {layerId: POSITIONS_LAYER_ID, zIndex: 35, visible: true, models};
}

/** Ids of the draft lines. */
export type DraftLineId =
  'draft-limit' | 'draft-trigger' | 'draft-sl' | 'draft-tp';

/** Ticket input field edited by each draft line. */
export const DRAFT_LINE_FIELDS: Readonly<
  Record<DraftLineId, 'limitPrice' | 'triggerPrice' | 'stopLoss' | 'takeProfit'>
> = {
  'draft-limit': 'limitPrice',
  'draft-trigger': 'triggerPrice',
  'draft-sl': 'stopLoss',
  'draft-tp': 'takeProfit',
};

/**
 * Builds the draggable draft layer for the order ticket (user tone). The
 * lines mirror unconfirmed ticket inputs; they are not orders.
 * @param inputs Ticket inputs.
 * @return Overlay layer with a line per valid non-zero price the order type
 *     uses (empty when nothing is priced).
 */
export function buildDraftLayer(inputs: TicketInputs): OverlayLayer {
  const models: PriceLineOverlay[] = [];
  const add = (id: DraftLineId, value: string, label: string) => {
    const text = value.trim().replace(/,/g, '');
    if (!Decimal.isValid(text) || Decimal.parse(text).isZero()) return;
    models.push({
      kind: 'price-line',
      id,
      price: Decimal.parse(text).toString(),
      tone: 'user',
      lineStyle: 'dashed',
      label,
      draggable: true,
    });
  };
  const side = inputs.side === 'buy' ? 'Buy' : 'Sell';
  if (needsLimitPrice(inputs.orderType)) {
    add('draft-limit', inputs.limitPrice, `Draft ${side.toLowerCase()} limit`);
  }
  if (needsTriggerPrice(inputs.orderType)) {
    add(
      'draft-trigger',
      inputs.triggerPrice,
      `Draft ${side.toLowerCase()} trigger`
    );
  }
  add('draft-sl', inputs.stopLoss, 'Draft stop-loss');
  add('draft-tp', inputs.takeProfit, 'Draft take-profit');
  return {layerId: DRAFT_LAYER_ID, zIndex: 45, visible: true, models};
}

/**
 * Interpretation of a dragged overlay: `draft` edits a ticket field, `amend`
 * proposes a new price for a working order (still to be confirmed), and
 * `ignore` does nothing.
 */
export type DragIntent =
  | {
      kind: 'draft';
      field: 'limitPrice' | 'triggerPrice' | 'stopLoss' | 'takeProfit';
      price: string;
    }
  | {
      kind: 'amend';
      order: Order;
      field: 'limit_price' | 'trigger_price';
      from: string;
      price: string;
    }
  | {kind: 'ignore'};

/**
 * Interprets a drag-end on a chart-trading layer.
 * @param layerId Dragged layer id.
 * @param modelId Dragged model id.
 * @param price Price where the drag ended, a float from the chart's price
 *     scale.
 * @param tickSize Tick size used to round the price.
 * @param orders Current orders (for amend lookups).
 * @return What the drag means; amendments still require confirmation.
 */
export function interpretDrag(
  layerId: string,
  modelId: string,
  price: number,
  tickSize: string,
  orders: readonly Order[]
): DragIntent {
  if (!Number.isFinite(price) || price <= 0) return {kind: 'ignore'};
  // The float becomes an exact tick-rounded decimal string before it can
  // reach a ticket field or an amendment.
  const rounded = Decimal.fromNumber(price)
    .roundToStep(Decimal.parse(tickSize))
    .toString();
  if (layerId === DRAFT_LAYER_ID && modelId in DRAFT_LINE_FIELDS) {
    return {
      kind: 'draft',
      // Draft models carry only `DraftLineId`s (see buildDraftLayer), and
      // `in` does not narrow a string to the key union.
      field: DRAFT_LINE_FIELDS[modelId as DraftLineId],
      price: rounded,
    };
  }
  if (layerId === ORDERS_LAYER_ID) {
    const order = orders.find(o => o.order_id === modelId);
    if (!order || !isOpenOrder(order)) return {kind: 'ignore'};
    // The line sits at the limit price when there is one (see
    // buildOrderLayer), so dragging a stop-limit moves its limit.
    const field = order.limit_price ? 'limit_price' : 'trigger_price';
    const from = order.limit_price ?? order.trigger_price;
    if (!from || Decimal.parse(from).eq(Decimal.parse(rounded))) {
      return {kind: 'ignore'};
    }
    return {kind: 'amend', order, field, from, price: rounded};
  }
  return {kind: 'ignore'};
}
