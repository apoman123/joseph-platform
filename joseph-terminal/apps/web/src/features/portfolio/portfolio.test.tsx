/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Component tests for the portfolio feature.
 */

import type {ExecutionClient, OrderIntentRequest} from '@joseph/contracts';
import {screen, waitFor, within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {afterEach, describe, expect, it} from 'vitest';
import {
  createFakeContext,
  renderFeature,
  type FakeContext,
  type RenderedFeature,
} from '../trading/testing/fake-context';
import {PORTFOLIO_FEATURE} from './index';

let rendered: RenderedFeature | null = null;

afterEach(() => {
  rendered?.dispose();
  rendered = null;
});

/**
 * Builds a paper limit buy of 0.01 DEMO BTCUSD at 90,000.
 * @param patch Field overrides.
 * @return Request.
 */
function request(patch: Partial<OrderIntentRequest> = {}): OrderIntentRequest {
  return {
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
    ...patch,
  };
}

/**
 * Opens a filled long position on the DEMO broker and waits until the
 * broker lists it.
 * @param ctx Fake context.
 */
async function openLong(ctx: FakeContext): Promise<void> {
  // A fresh idempotency key, so a second call opens a second order.
  await ctx.broker.submitIntent(
    request({order_type: 'market', limit_price: null}),
    `open-${Math.random()}`
  );
  await waitFor(
    async () =>
      void expect((await ctx.broker.listPositions()).positions).toHaveLength(1)
  );
}

describe('portfolio', () => {
  it('cancels an open order after confirmation', async () => {
    const ctx = createFakeContext();
    await ctx.broker.submitIntent(request(), 'resting');
    await waitFor(
      async () =>
        void expect((await ctx.broker.listOrders()).orders[0]?.state).toBe(
          'ACKNOWLEDGED'
        )
    );
    rendered = await renderFeature(
      PORTFOLIO_FEATURE,
      'bottom-panel',
      'portfolio.orders',
      ctx
    );
    const user = userEvent.setup();
    const row = await screen.findByRole('row', {name: /Working/});
    await user.click(within(row).getByRole('button', {name: 'Cancel'}));
    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByRole('button', {name: 'Cancel'})).toHaveFocus();
    await user.click(
      within(dialog).getByRole('button', {
        name: 'Cancel buy limit 0.01 at 90,000',
      })
    );
    await waitFor(
      async () =>
        void expect((await ctx.broker.listOrders()).orders[0]?.state).toBe(
          'CANCELED'
        )
    );
    expect(
      ctx.notifications.some(n => /once the venue confirms/.test(n.message))
    ).toBe(true);
  });

  it('explains unknown orders and never offers cancel', async () => {
    const ctx = createFakeContext();
    const unknown = {
      order_id: 'u1',
      intent_id: 'i',
      tenant_id: 't',
      account_id: 'acct-paper-demo',
      instrument_id: 'mock:perp:BTCUSD',
      client_order_id: 'c',
      mode: 'PAPER',
      role: 'PRIMARY',
      side: 'buy',
      order_type: 'limit',
      time_in_force: 'GTC',
      quantity_base: '0.01',
      filled_base: '0',
      limit_price: '90000',
      reduce_only: false,
      state: 'UNKNOWN_RECONCILING',
      created_at_ms: 1,
      updated_at_ms: 1,
      version: 1,
    } as const;
    const execution: ExecutionClient = Object.assign(
      Object.create(ctx.broker) as ExecutionClient,
      {
        listOrders: async () => ({orders: [unknown]}),
      }
    );
    const custom = createFakeContext({clients: {execution}});
    rendered = await renderFeature(
      PORTFOLIO_FEATURE,
      'bottom-panel',
      'portfolio.orders',
      custom
    );
    const row = await screen.findByTestId('order-u1');
    expect(within(row).getByText('Unknown, reconciling')).toBeInTheDocument();
    expect(within(row).getByRole('button', {name: 'Cancel'})).toBeDisabled();
    expect(screen.getByText(/It has not been canceled/)).toBeInTheDocument();
  });

  it('closes a position with a previewed reduce-only order', async () => {
    const ctx = createFakeContext();
    await openLong(ctx);
    rendered = await renderFeature(
      PORTFOLIO_FEATURE,
      'bottom-panel',
      'portfolio.positions',
      ctx
    );
    const user = userEvent.setup();
    const row = await screen.findByTestId('position-mock:perp:BTCUSD');
    await user.click(within(row).getByRole('button', {name: 'Close'}));
    const confirm = await screen.findByRole('button', {
      name: 'Close 100% of long 0.01 BTCUSD with a reduce-only market sell',
    });
    await waitFor(() => expect(confirm).toBeEnabled());
    await user.click(confirm);
    await waitFor(
      async () =>
        void expect((await ctx.broker.listPositions()).positions).toHaveLength(
          0
        )
    );
  });

  it('reverses in two separately confirmed steps', async () => {
    const ctx = createFakeContext();
    await openLong(ctx);
    rendered = await renderFeature(
      PORTFOLIO_FEATURE,
      'bottom-panel',
      'portfolio.positions',
      ctx
    );
    const user = userEvent.setup();
    await user.click(
      within(await screen.findByTestId('position-mock:perp:BTCUSD')).getByRole(
        'button',
        {name: 'Reverse'}
      )
    );
    const step1 = await screen.findByRole('button', {
      name: 'Step 1: close long 0.01',
    });
    await waitFor(() => expect(step1).toBeEnabled());
    await user.click(step1);
    const step2 = await screen.findByRole(
      'button',
      {name: 'Step 2: open short 0.01'},
      {timeout: 5000}
    );
    await waitFor(() => expect(step2).toBeEnabled());
    await user.click(step2);
    await waitFor(async () => {
      const {positions} = await ctx.broker.listPositions();
      expect(positions[0]).toMatchObject({
        side: 'short',
        quantity_base: '0.01',
      });
    });
  });

  it('engages the kill switch only after reviewing explicit consequences', async () => {
    const ctx = createFakeContext();
    rendered = await renderFeature(
      PORTFOLIO_FEATURE,
      'bottom-panel',
      'portfolio.risk',
      ctx
    );
    const user = userEvent.setup();
    await user.click(
      await screen.findByRole('button', {name: 'Review kill switch'})
    );
    expect(
      screen.getByText('Enter a reason; it is recorded in the audit log.')
    ).toBeInTheDocument();
    await user.type(
      screen.getByRole('textbox', {name: 'Reason (recorded in the audit log)'}),
      'volatility'
    );
    await user.click(screen.getByRole('button', {name: 'Review kill switch'}));
    const consequences = screen.getByTestId('kill-switch-consequences');
    expect(consequences).toHaveTextContent('Open orders stay working.');
    expect(consequences).toHaveTextContent('Positions stay open.');
    await user.click(
      screen.getByRole('button', {name: 'Engage global kill switch'})
    );
    expect(await screen.findByTestId('engaged-switch')).toHaveTextContent(
      'volatility'
    );
    const preview = await ctx.broker.previewOrder(request());
    expect(preview.allowed).toBe(false);
  });

  it('shows executions and a verified audit chain', async () => {
    const ctx = createFakeContext();
    await openLong(ctx);
    rendered = await renderFeature(
      PORTFOLIO_FEATURE,
      'bottom-panel',
      'portfolio.audit',
      ctx
    );
    expect(await screen.findByTestId('audit-chain')).toHaveTextContent(
      'Chain verified'
    );
    expect(
      screen.getByRole('cell', {name: 'order.submit'})
    ).toBeInTheDocument();
    rendered.unmount();
    rendered.dispose();
    rendered = await renderFeature(
      PORTFOLIO_FEATURE,
      'bottom-panel',
      'portfolio.fills',
      ctx
    );
    expect(
      await screen.findByRole('table', {name: 'Executions'})
    ).toBeInTheDocument();
  });

  it('contributes seven bottom-panel tabs and disposes them', async () => {
    const ctx = createFakeContext();
    const dispose = await PORTFOLIO_FEATURE.activate(ctx);
    expect(ctx.contributions('bottom-panel').map(c => c.title)).toEqual([
      'Positions',
      'Orders',
      'Executions',
      'Order history',
      'Audit',
      'Risk controls',
      'Accounts',
    ]);
    dispose();
    expect(ctx.contributions('bottom-panel')).toHaveLength(0);
  });
});
