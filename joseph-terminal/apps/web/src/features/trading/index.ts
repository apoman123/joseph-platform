/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Trading feature: order ticket, chart order/position/draft
 * layers, chart context menu and drag-to-amend. Every action becomes an
 * `OrderIntentRequest` previewed and confirmed through the execution
 * engine; nothing here can sign or send a venue order.
 */

import {
  buildDraftLayer,
  buildOrderLayer,
  buildPositionLayer,
  interpretDrag,
} from '@joseph/chart-trading';
import type {OverlayLayer} from '@joseph/chart-core';
import type {Disposer} from '@joseph/feature-registry';
import {createElement} from 'react';
import type {
  WebFeature,
  WebSlotProps,
  WebView,
} from '../../shell/react-bindings';
import {OrderTicket} from './order-ticket';
import {TradingContextMenu} from './trading-context-menu';
import {createTradingRuntime, type TradingRuntime} from './trading-runtime';
import {derived} from './ui/hooks';

/**
 * Wraps the ticket component as a slot view bound to a runtime.
 * @param runtime Trading runtime.
 * @return Slot view.
 */
function ticketView(runtime: TradingRuntime): WebView {
  /**
   * Renders the order ticket with the slot's context.
   * @param props Slot props.
   * @return Element.
   */
  function TicketSlot(props: WebSlotProps) {
    return createElement(OrderTicket, {ctx: props.ctx, runtime});
  }
  return TicketSlot;
}

/**
 * Wraps the context menu as a slot view bound to a runtime.
 * @param runtime Trading runtime.
 * @return Slot view.
 */
function menuView(runtime: TradingRuntime): WebView {
  /**
   * Renders the chart's trading menu items with the slot's context.
   * @param props Slot props.
   * @return Element.
   */
  function MenuSlot(props: WebSlotProps) {
    return createElement(TradingContextMenu, {ctx: props.ctx, runtime});
  }
  return MenuSlot;
}

/**
 * Returns the tick and lot sizes for chart labels.
 * @param runtime Trading runtime.
 * @param instrumentId Instrument id.
 * @return Cached precision, or `0.01`/`0.001` until it has loaded.
 */
function labelPrecision(
  runtime: TradingRuntime,
  instrumentId: string
): {tickSize: string; lotSize: string} {
  const precision = runtime.precisionFor(instrumentId);
  return {
    tickSize: precision?.tick_size ?? '0.01',
    lotSize: precision?.lot_size ?? '0.001',
  };
}

/** The trading feature. */
export const TRADING_FEATURE: WebFeature = {
  manifest: {
    id: 'trading',
    version: '1.0.0',
    owner: 'terminal-trading',
    displayName: 'Order ticket and chart trading',
    description:
      'Preview and confirm order intents, show orders and positions on charts, and amend by dragging.',
    uiSlots: ['side-panel', 'chart-overlay', 'chart-context-menu'],
    requiredCapabilities: ['execution'],
    permissions: ['market:read', 'orders:read', 'orders:intent'],
    settingsSchemaVersion: 1,
    contractVersion: 1,
    removal: {
      notes:
        'Removing the feature only removes UI. Orders and positions live in the execution engine and stay visible through the portfolio feature.',
    },
  },
  activate(ctx) {
    const runtime = createTradingRuntime(ctx);
    const disposers: Disposer[] = [() => void runtime.dispose()];
    disposers.push(
      ctx.slots.contribute('side-panel', {
        id: 'trading.ticket',
        title: 'Order ticket',
        order: 40,
        icon: 'ticket',
        shortcut: 'Alt+B / Alt+S',
        view: ticketView(runtime),
      }),
      ctx.slots.contribute('chart-context-menu', {
        id: 'trading.context-menu',
        title: 'Trade at price',
        order: 10,
        view: menuView(runtime),
      })
    );
    disposers.push(
      ctx.chartLayers.register(chart =>
        derived<OverlayLayer | null>([runtime.orders], () =>
          buildOrderLayer(
            runtime.orders.get(),
            chart.instrumentId,
            labelPrecision(runtime, chart.instrumentId),
            /* amendable= */ true
          )
        )
      ),
      ctx.chartLayers.register(chart =>
        derived<OverlayLayer | null>([runtime.positions], () =>
          buildPositionLayer(
            runtime.positions.get(),
            chart.instrumentId,
            labelPrecision(runtime, chart.instrumentId)
          )
        )
      ),
      ctx.chartLayers.register(chart =>
        derived<OverlayLayer | null>([runtime.controller], () => {
          const {inputs} = runtime.controller.get();
          return inputs.instrumentId === chart.instrumentId
            ? buildDraftLayer(inputs)
            : null;
        })
      )
    );
    // Bar replay shows history, so trading from the chart is off there.
    disposers.push(
      ctx.events.on('chart.contextMenu', event => {
        if (ctx.selection.get().replay) return;
        void runtime.openMenu(event.chartId, event.instrumentId, event.price);
      }),
      ctx.events.on('ticket.open', event => {
        if (ctx.selection.get().instrumentId !== event.instrumentId) {
          ctx.select({instrumentId: event.instrumentId});
        }
        const inputs = runtime.controller.get().inputs;
        runtime.controller.dispatch({
          type: 'replaceInputs',
          inputs: {
            ...inputs,
            instrumentId: event.instrumentId,
            side: event.side ?? inputs.side,
            orderType: event.orderType ?? inputs.orderType,
            limitPrice:
              event.orderType === 'limit' && event.price
                ? event.price
                : inputs.limitPrice,
            triggerPrice:
              event.orderType === 'stop_market' && event.price
                ? event.price
                : inputs.triggerPrice,
          },
        });
      }),
      ctx.events.on('chart.overlayDragEnd', event => {
        if (ctx.selection.get().replay) return;
        // Snap to the dragged order's tick; a ticket draft line uses the
        // ticket instrument's tick.
        const precision = runtime.precisionFor(
          runtime.controller.get().inputs.instrumentId
        );
        const orders = runtime.orders.get();
        const order = orders.find(o => o.order_id === event.modelId);
        const tick =
          runtime.precisionFor(
            order?.instrument_id ?? runtime.controller.get().inputs.instrumentId
          )?.tick_size ??
          precision?.tick_size ??
          '0.01';
        const intent = interpretDrag(
          event.layerId,
          event.modelId,
          event.price,
          tick,
          orders
        );
        if (intent.kind === 'draft') {
          runtime.controller.edit({[intent.field]: intent.price});
        } else if (intent.kind === 'amend') {
          runtime.amend.set({
            order: intent.order,
            field: intent.field,
            from: intent.from,
            price: intent.price,
          });
        }
      })
    );
    disposers.push(
      ctx.commands.register({
        id: 'trading.buy',
        title: 'New buy order',
        run: () => void runtime.controller.edit({side: 'buy'}),
      }),
      ctx.commands.register({
        id: 'trading.sell',
        title: 'New sell order',
        run: () => void runtime.controller.edit({side: 'sell'}),
      }),
      ctx.hotkeys.bind('alt+b', 'trading.buy'),
      ctx.hotkeys.bind('alt+s', 'trading.sell')
    );
    // Undo in reverse order of setup; `splice` empties the list so a second
    // call does nothing.
    return () => {
      for (const dispose of disposers.splice(0).reverse()) {
        dispose();
      }
    };
  },
};
