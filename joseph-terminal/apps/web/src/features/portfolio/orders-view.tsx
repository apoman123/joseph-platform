/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Open orders (with cancel) and order history. Cancel is a
 * request: the order shows "Cancel pending" until the venue confirms, and
 * orders with an unknown outcome are explained, never shown as canceled.
 */

'use client';
import {
  cancelGuard,
  formatUtcDateTime,
  groupThousands,
  isOpenOrder,
  orderTypeName,
} from '@joseph/chart-trading';
import type {Order} from '@joseph/contracts';
import {Fragment, useState, type ReactNode} from 'react';
import {useStore, type WebFeatureContext} from '../../shell/react-bindings';
import {errorMessage} from '../trading/ui/hooks';
import {
  ConfirmDialog,
  EmptyState,
  InstrumentLabel,
  ModeBadge,
  Notice,
  OrderStateBadge,
  SideText,
} from '../trading/ui/primitives';
import type {PortfolioRuntime} from './portfolio-store';
import styles from './portfolio.module.css';

/** Props of the orders view. */
export interface OrdersViewProps {
  /** Feature context. */
  ctx: WebFeatureContext;
  /** Portfolio runtime. */
  runtime: PortfolioRuntime;
  /** Open orders or finished history. */
  mode: 'open' | 'history';
}

/**
 * Formats an optional decimal string with thousands separators, without
 * passing it through a float.
 * @param value Decimal string or null.
 * @return Text, or `—` when missing.
 */
function num(value: string | null | undefined): string {
  return value ? groupThousands(value) : '—';
}

/**
 * Describes an order in one line for confirmations.
 * @param order Order.
 * @return Text such as `buy limit 0.01 at 94,000`.
 */
export function describeOrder(order: Order): string {
  const price = order.limit_price ?? order.trigger_price;
  return `${order.side} ${orderTypeName(order.order_type)} ${order.quantity_base}${price ? ` at ${groupThousands(price)}` : ''}`;
}

/**
 * Renders open orders (with a confirmed cancel) or order history.
 * @param props Feature context, portfolio runtime and which list to show.
 * @return Table element, or an empty state once loaded without orders.
 */
export function OrdersView({ctx, runtime, mode}: OrdersViewProps): ReactNode {
  const state = useStore(runtime.state);
  const [pending, setPending] = useState<Order | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const orders = state.orders.filter(o =>
    mode === 'open' ? isOpenOrder(o) : !isOpenOrder(o)
  );
  const cancel = async (order: Order) => {
    setBusy(true);
    setError(null);
    try {
      await ctx.clients.execution.cancelOrder(order.order_id);
      ctx.notifier.notify(
        'Cancel requested. The order is canceled once the venue confirms.',
        'info'
      );
      setPending(null);
      void runtime.refresh();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };
  if (state.loaded && orders.length === 0) {
    return (
      <EmptyState
        title={mode === 'open' ? 'No open orders' : 'No finished orders yet'}
      >
        {mode === 'open'
          ? 'Orders you place from the ticket or the chart appear here with their venue state.'
          : 'Filled, canceled and rejected orders are listed here.'}
      </EmptyState>
    );
  }
  return (
    <div className={styles.view}>
      {state.error && (
        <Notice tone="danger">
          Orders could not be refreshed: {state.error}
        </Notice>
      )}
      {error && <Notice tone="danger">{error}</Notice>}
      <table
        className="j-table"
        aria-label={mode === 'open' ? 'Open orders' : 'Order history'}
      >
        <thead>
          <tr>
            <th>Created</th>
            <th>Instrument</th>
            <th>Side</th>
            <th>Type</th>
            <th className="j-num">Qty</th>
            <th className="j-num">Filled</th>
            <th className="j-num">Price</th>
            <th>State</th>
            <th>Mode</th>
            {mode === 'open' && (
              <th>
                <span className={styles.srOnly}>Actions</span>
              </th>
            )}
          </tr>
        </thead>
        <tbody>
          {orders.map(order => {
            const guard = cancelGuard(order);
            return (
              <Fragment key={order.order_id}>
                <tr
                  data-testid={`order-${order.order_id}`}
                  data-state={order.state}
                >
                  <td className="j-tabular">
                    {formatUtcDateTime(order.created_at_ms)}
                  </td>
                  <td>
                    <InstrumentLabel instrumentId={order.instrument_id} />
                  </td>
                  <td>
                    <SideText side={order.side} />
                    {/* SL is the attached stop-loss, TP the take-profit. */}
                    {order.role !== 'PRIMARY' && (
                      <span className="j-muted">
                        {' '}
                        {order.role === 'STOP_LOSS'
                          ? 'SL'
                          : order.role === 'TAKE_PROFIT'
                            ? 'TP'
                            : 'close'}
                      </span>
                    )}
                  </td>
                  <td>
                    {orderTypeName(order.order_type)}
                    {order.reduce_only && (
                      <span className="j-muted"> reduce</span>
                    )}
                  </td>
                  <td className="j-num">{num(order.quantity_base)}</td>
                  <td className="j-num">{num(order.filled_base)}</td>
                  {/* The fill price once filled, else the order's price. */}
                  <td className="j-num">
                    {num(
                      order.avg_fill_price ??
                        order.limit_price ??
                        order.trigger_price
                    )}
                  </td>
                  <td>
                    <OrderStateBadge state={order.state} />
                  </td>
                  <td>
                    <ModeBadge mode={order.mode} />
                  </td>
                  {mode === 'open' && (
                    <td>
                      <button
                        type="button"
                        className="j-btn j-btn--sm"
                        disabled={!guard.allowed}
                        title={guard.reason ?? undefined}
                        onClick={() => void setPending(order)}
                      >
                        Cancel
                      </button>
                    </td>
                  )}
                </tr>
                {order.state === 'UNKNOWN_RECONCILING' && (
                  <tr className={styles.note}>
                    <td colSpan={10}>{guard.reason}</td>
                  </tr>
                )}
              </Fragment>
            );
          })}
        </tbody>
      </table>
      {pending && (
        <ConfirmDialog
          title="Cancel this order?"
          confirmLabel={`Cancel ${describeOrder(pending)}`}
          tone="ask"
          busy={busy}
          onCancel={() => void setPending(null)}
          onConfirm={() => void cancel(pending)}
        >
          <p>
            <InstrumentLabel instrumentId={pending.instrument_id} />{' '}
            {describeOrder(pending)}.
          </p>
          <p>
            Canceling does not undo fills that already happened. The venue
            decides the final state.
          </p>
        </ConfirmDialog>
      )}
    </div>
  );
}
