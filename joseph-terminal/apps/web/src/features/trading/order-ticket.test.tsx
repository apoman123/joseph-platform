/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Component tests for the order ticket and chart trading.
 */

import type {OverlayLayer} from '@joseph/chart-core';
import {act, screen, waitFor, within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {afterEach, describe, expect, it} from 'vitest';
import {TRADING_FEATURE} from './index';
import {
  createFakeContext,
  renderFeature,
  type RenderedFeature,
} from './testing/fake-context';

let rendered: RenderedFeature | null = null;

afterEach(() => {
  rendered?.dispose();
  rendered = null;
});

/**
 * Renders the ticket and waits for account and instrument metadata.
 * @return Rendered feature.
 */
async function renderTicket(): Promise<RenderedFeature> {
  rendered = await renderFeature(
    TRADING_FEATURE,
    'side-panel',
    'trading.ticket'
  );
  await screen.findByText('Lot size 0.001, minimum 0.001');
  await waitFor(() =>
    expect(screen.getByRole('combobox', {name: 'Account'})).toHaveValue(
      'acct-paper-demo'
    )
  );
  return rendered;
}

/**
 * Fills a valid limit buy.
 * @param price Limit price.
 */
async function fillLimitBuy(price = '95100'): Promise<void> {
  const user = userEvent.setup();
  await user.type(
    screen.getByRole('textbox', {name: 'Quantity (BTC)'}),
    '0.01'
  );
  await user.type(screen.getByRole('textbox', {name: 'Limit price'}), price);
}

describe('order ticket', () => {
  it('previews then confirms with a label naming the exact action', async () => {
    const {ctx} = await renderTicket();
    const user = userEvent.setup();
    await fillLimitBuy();
    expect(screen.getByTestId('confirm-order')).toBeDisabled();
    await user.click(screen.getByRole('button', {name: 'Preview order'}));
    const preview = await screen.findByTestId('ticket-preview');
    expect(
      within(preview).getByRole('list', {name: 'Risk checks'})
    ).toBeInTheDocument();
    const confirm = screen.getByTestId('confirm-order');
    expect(confirm).toHaveTextContent(
      'Place paper limit buy 0.010 BTC at 95,100.0'
    );
    await user.click(confirm);
    const result = await screen.findByTestId('ticket-result');
    expect(result).toHaveTextContent(/submitting/i);
    expect(result).not.toHaveTextContent(/filled/i);
    expect((await ctx.broker.listOrders()).orders).toHaveLength(1);
  });

  it('cannot submit twice on a double click', async () => {
    const {ctx} = await renderTicket();
    const user = userEvent.setup();
    await fillLimitBuy();
    await user.click(screen.getByRole('button', {name: 'Preview order'}));
    await screen.findByTestId('ticket-preview');
    await user.dblClick(screen.getByTestId('confirm-order'));
    await screen.findByTestId('ticket-result');
    expect(
      (await ctx.broker.listOrders()).orders.filter(o => o.role === 'PRIMARY')
    ).toHaveLength(1);
  });

  it('disables live mode with the server reason', async () => {
    await renderTicket();
    expect(screen.getByRole('button', {name: 'Live'})).toBeDisabled();
    expect(screen.getByTestId('live-disabled-reason')).toHaveTextContent(
      /Live trading is not enabled/
    );
    expect(screen.getByTestId('mode-badge')).toHaveTextContent('Paper');
  });

  it('requires a new preview after any edit', async () => {
    await renderTicket();
    const user = userEvent.setup();
    await fillLimitBuy();
    await user.click(screen.getByRole('button', {name: 'Preview order'}));
    await screen.findByTestId('ticket-preview');
    await user.type(screen.getByRole('textbox', {name: 'Quantity (BTC)'}), '5');
    expect(screen.queryByTestId('ticket-preview')).not.toBeInTheDocument();
    expect(screen.getByTestId('confirm-order')).toBeDisabled();
  });

  it('shows a blocked preview when the kill switch is engaged', async () => {
    const {ctx} = await renderTicket();
    await ctx.broker.setKillSwitch({
      scope: 'GLOBAL',
      engage: true,
      cancel_open_orders: false,
      flatten_positions: false,
      reason: 'test',
    });
    const user = userEvent.setup();
    await fillLimitBuy();
    await user.click(screen.getByRole('button', {name: 'Preview order'}));
    const preview = await screen.findByTestId('ticket-preview');
    expect(preview).toHaveTextContent(/Kill switch engaged/);
    expect(screen.getByTestId('confirm-order')).toBeDisabled();
  });

  it('prefills from the chart context menu', async () => {
    const ctx = createFakeContext();
    rendered = await renderFeature(
      TRADING_FEATURE,
      'side-panel',
      'trading.ticket',
      ctx
    );
    await screen.findByText('Lot size 0.001, minimum 0.001');
    act(
      () =>
        void ctx.events.emit('chart.contextMenu', {
          chartId: 'chart-1',
          instrumentId: 'mock:perp:BTCUSD',
          price: 94980.04,
          timeMs: null,
        })
    );
    const menuContribution = ctx.contributions('chart-context-menu')[0];
    if (!menuContribution) throw new Error('no menu');
    const Menu = menuContribution.view;
    const {unmount} = await import('@testing-library/react').then(m =>
      m.render(<Menu ctx={ctx} />)
    );
    const item = await screen.findByRole('menuitem', {
      name: /Sell limit at 94,980.0/,
    });
    await userEvent.setup().click(item);
    expect(screen.getByRole('textbox', {name: 'Limit price'})).toHaveValue(
      '94980'
    );
    expect(screen.getByRole('button', {name: 'Sell'})).toHaveAttribute(
      'aria-pressed',
      'true'
    );
    unmount();
  });

  it('publishes order lines for the chart instrument', async () => {
    const {ctx} = await renderTicket();
    expect(ctx.layerProviders).toHaveLength(3);
    const ordersLayer = ctx.layerProviders[0]?.({
      chartId: 'c',
      instrumentId: 'mock:perp:BTCUSD',
      timeframe: '5m',
    });
    if (!ordersLayer) throw new Error('no layer');
    let latest: OverlayLayer | null = null;
    const off = ordersLayer.subscribe(layer => {
      latest = layer;
    });
    await ctx.broker.submitIntent(
      {
        account_id: 'acct-paper-demo',
        instrument_id: 'mock:perp:BTCUSD',
        side: 'buy',
        position_effect: 'open',
        order_type: 'limit',
        quantity_base: '0.01',
        limit_price: '90000',
        time_in_force: 'GTC',
        reduce_only: false,
        mode: 'PAPER',
        source: 'MANUAL_TICKET',
      },
      'layer-test'
    );
    await waitFor(() => void expect(latest?.models.length).toBe(1));
    off();
  });

  it('turns a dragged order line into an amend confirmation', async () => {
    const {ctx} = await renderTicket();
    const response = await ctx.broker.submitIntent(
      {
        account_id: 'acct-paper-demo',
        instrument_id: 'mock:perp:BTCUSD',
        side: 'buy',
        position_effect: 'open',
        order_type: 'limit',
        quantity_base: '0.01',
        limit_price: '90000',
        time_in_force: 'GTC',
        reduce_only: false,
        mode: 'PAPER',
        source: 'MANUAL_TICKET',
      },
      'amend-test'
    );
    const orderId = response.orders[0]?.order_id ?? '';
    await waitFor(
      async () =>
        void expect((await ctx.broker.listOrders()).orders[0]?.state).toBe(
          'ACKNOWLEDGED'
        )
    );
    await waitFor(() => void expect(ctx.layerProviders.length).toBe(3));
    // Let the runtime see the acknowledged order.
    await act(async () => {
      await new Promise(resolve => setTimeout(resolve, 20));
    });
    const runtimeOrders = await ctx.broker.listOrders();
    expect(runtimeOrders.orders).toHaveLength(1);
    await waitFor(() => {
      act(
        () =>
          void ctx.events.emit('chart.overlayDragEnd', {
            chartId: 'c',
            layerId: 'chart-trading:orders',
            modelId: orderId,
            price: 91000.04,
          })
      );
      expect(screen.getByTestId('amend-panel')).toBeInTheDocument();
    });
    await userEvent
      .setup()
      .click(screen.getByRole('button', {name: 'Move order to 91,000.0'}));
    await waitFor(
      async () =>
        void expect(
          (await ctx.broker.listOrders()).orders[0]?.limit_price
        ).toBe('91000')
    );
  });
});
