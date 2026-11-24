/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Component tests for the equity order ticket (routing by
 * `tradable_venues`, TWSE board and intraday odd lots, tick ladder, price
 * limits, time in force and order types from the account's capabilities,
 * IBKR outside-RTH), the rate no-ticket rule, arming LIVE and the LIVE
 * confirmation, and the Accounts tab.
 */

import {
  DEMO_IBKR_ACCOUNT_ID,
  DEMO_LIVE_REHEARSAL_ACCOUNT_ID,
  DEMO_SINOPAC_ACCOUNT_ID,
  DEMO_STEP_UP_CODE,
  DEMO_TWD_ACCOUNT_ID,
  DemoExecutionClient,
} from '@joseph/mock-provider';
import {act, render, screen, waitFor, within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {afterEach, describe, expect, it, vi} from 'vitest';
import {PORTFOLIO_FEATURE} from '../portfolio';
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
 * DEMO broker with the LIVE rehearsal account (simulated IBKR live).
 * @return Broker.
 */
function rehearsalBroker(): DemoExecutionClient {
  return new DemoExecutionClient({
    liveRehearsal: true,
    ackDelayMs: 0,
    fillDelayMs: 0,
    partialFills: false,
  });
}

/**
 * Formats an armed-until time like the ticket does.
 * @param ms Time (UTC ms).
 * @return `HH:MM` in UTC.
 */
function hhmm(ms: number): string {
  return new Date(ms).toISOString().slice(11, 16);
}

/**
 * Renders the ticket on an instrument and waits for the routed account,
 * then optionally switches to another account.
 * @param instrumentId Instrument.
 * @param routed Account id the ticket must route to first.
 * @param options Broker to use and account id to switch to.
 * @return Rendered feature.
 */
async function renderOn(
  instrumentId: string,
  routed: string,
  options: {broker?: DemoExecutionClient; select?: string} = {}
): Promise<RenderedFeature> {
  const ctx = createFakeContext({
    selection: {instrumentId},
    ...(options.broker ? {broker: options.broker} : {}),
  });
  rendered = await renderFeature(
    TRADING_FEATURE,
    'side-panel',
    'trading.ticket',
    ctx
  );
  const picker = () => screen.getByRole('combobox', {name: 'Account'});
  await waitFor(() => expect(picker()).toHaveValue(routed));
  if (options.select) {
    await userEvent.setup().selectOptions(picker(), options.select);
    await waitFor(() => expect(picker()).toHaveValue(options.select));
  }
  return rendered;
}

/**
 * Returns the texts of the time-in-force options.
 * @return Option texts.
 */
function tifOptions(): Array<string | null> {
  const tif = screen.getByRole('combobox', {name: 'Time in force'});
  return within(tif)
    .getAllByRole('option')
    .map(o => o.textContent);
}

describe('equity order ticket', () => {
  it('routes TWSE to the TWD paper account and validates lots and prices', async () => {
    await renderOn('mock:stock:9901', DEMO_TWD_ACCOUNT_ID, {
      select: DEMO_SINOPAC_ACCOUNT_ID,
    });
    const user = userEvent.setup();
    expect(screen.getByTestId('account-status')).toHaveTextContent(
      /SinoPac Securities \(Shioaji\) \(simulation\).*Connected.*Credentials configured on the server/
    );
    expect(screen.getByTestId('account-cash')).toHaveTextContent(
      'Settled cash 5,000,000.00 TWD'
    );
    // SinoPac has no stop orders.
    expect(screen.queryByRole('button', {name: 'Stop'})).toBeNull();
    expect(tifOptions()).toEqual([
      'DAY · Day (this session)',
      'IOC · Immediate or cancel',
      'FOK · Fill or kill',
    ]);
    const quantity = await screen.findByRole('textbox', {
      name: 'Quantity (張, 1,000 shares each)',
    });
    await user.type(quantity, '2');
    expect(screen.getByText('= 2 張 (2,000 shares)')).toBeInTheDocument();
    const limit = screen.getByRole('textbox', {name: 'Limit price (TWD)'});
    await user.type(limit, '585.4');
    expect(
      screen.getByText('Rounded to the tick ladder (1 at this price): 585 TWD.')
    ).toBeInTheDocument();
    await user.clear(limit);
    await user.type(limit, '700');
    expect(
      await screen.findByText(/outside today's price limit 527 TWD – 643 TWD/)
    ).toBeInTheDocument();
    expect(screen.queryByRole('textbox', {name: 'Leverage'})).toBeNull();
    expect(screen.queryByTestId('outside-rth')).toBeNull();
    // Intraday odd lots: limit DAY only.
    await user.click(screen.getByRole('button', {name: 'Odd lot (零股)'}));
    expect(screen.getByTestId('lot-hint')).toHaveTextContent(
      'Intraday odd lots are limit orders, DAY (ROD) only.'
    );
    expect(tifOptions()).toEqual(['DAY · Day (this session)']);
    expect(screen.getByRole('button', {name: 'Market'})).toBeDisabled();
    const shares = screen.getByRole('textbox', {name: 'Quantity (shares)'});
    await user.type(shares, '1500');
    expect(
      screen.getByText(/odd-lot order is 1 to 999 shares/)
    ).toBeInTheDocument();
  });

  it('offers board lots only where odd lots are not available', async () => {
    await renderOn('mock:stock:9901', DEMO_TWD_ACCOUNT_ID, {
      select: 'acct-cathay-demo',
    });
    expect(screen.getByRole('button', {name: 'Odd lot (零股)'})).toBeDisabled();
    expect(screen.getByTestId('lot-hint')).toHaveTextContent(
      'This account trades board lots only.'
    );
  });

  it('places a US share order through IBKR with DAY, GTC or IOC', async () => {
    const {ctx} = await renderOn('mock:stock:ACME', 'acct-paper-demo', {
      select: DEMO_IBKR_ACCOUNT_ID,
    });
    const user = userEvent.setup();
    expect(tifOptions()).toHaveLength(3);
    await user.selectOptions(
      screen.getByRole('combobox', {name: 'Time in force'}),
      'GTC'
    );
    const rth = screen.getByTestId('outside-rth');
    expect(rth).toBeEnabled();
    await user.click(rth);
    await user.type(
      screen.getByRole('textbox', {name: 'Quantity (shares)'}),
      '100'
    );
    await user.type(
      screen.getByRole('textbox', {name: 'Limit price (USD)'}),
      '182.4'
    );
    await user.click(screen.getByRole('button', {name: 'Preview order'}));
    await screen.findByTestId('ticket-preview');
    const confirm = screen.getByTestId('confirm-order');
    expect(confirm).toHaveTextContent(
      'Place paper limit buy 100 shares ACME at 182.40 USD'
    );
    expect(screen.getByTestId('ticket-preview')).toHaveTextContent(
      'Cash required'
    );
    await user.click(confirm);
    await screen.findByTestId('ticket-result');
    const [order] = (await ctx.broker.listOrders()).orders;
    expect(order).toMatchObject({
      account_id: DEMO_IBKR_ACCOUNT_ID,
      instrument_id: 'mock:stock:ACME',
      quantity_base: '100',
      time_in_force: 'GTC',
    });
  });

  it('shows no ticket and no chart trading menu for a published rate', async () => {
    const ctx = createFakeContext({
      selection: {instrumentId: 'mock:rate:UST10Y'},
    });
    rendered = await renderFeature(
      TRADING_FEATURE,
      'side-panel',
      'trading.ticket',
      ctx
    );
    expect(await screen.findByTestId('rate-no-ticket')).toHaveTextContent(
      /UST10Y is a published rate, not a tradable instrument/
    );
    expect(screen.queryByTestId('confirm-order')).toBeNull();
    await act(async () => {
      ctx.events.emit('chart.contextMenu', {
        chartId: 'chart-1',
        instrumentId: 'mock:rate:UST10Y',
        price: 4.2,
        timeMs: null,
      });
      // `openMenu` is asynchronous; let it settle before rendering the menu.
      await new Promise(resolve => setTimeout(resolve, 10));
    });
    const Menu = ctx.contributions('chart-context-menu')[0]?.view;
    if (!Menu) throw new Error('no menu');
    const {container} = render(<Menu ctx={ctx} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe('live trading', () => {
  it('arms LIVE on the server, then confirms a LIVE order in a dedicated step', async () => {
    const broker = rehearsalBroker();
    const grant = vi.spyOn(broker, 'grantAccountLiveConsent');
    const revoke = vi.spyOn(broker, 'revokeAccountLiveConsent');
    await renderOn('mock:stock:ACME', 'acct-paper-demo', {
      broker,
      select: DEMO_LIVE_REHEARSAL_ACCOUNT_ID,
    });
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', {name: 'Live'}));
    const arm = screen.getByTestId('arm-live');
    expect(arm).toHaveTextContent(
      'Live orders from this ticket go to Interactive Brokers account "IBKR live rehearsal (demo)" with real money.'
    );
    expect(arm).toHaveTextContent(
      `the simulated execution engine accepts the step-up code ${DEMO_STEP_UP_CODE}`
    );
    const armButton = screen.getByTestId('arm-live-confirm');
    expect(armButton).toBeDisabled();
    expect(armButton).toHaveTextContent('Arm live for 1 hour');
    await user.click(within(arm).getByRole('checkbox'));
    const code = within(arm).getByRole('textbox', {name: 'Step-up code'});
    // Five digits are too short to send; six digits that are not the DEMO
    // code are sent and refused by the simulated engine.
    await user.type(code, '12345');
    expect(armButton).toBeDisabled();
    await user.type(code, '9');
    await user.click(armButton);
    expect(await within(arm).findByRole('alert')).toHaveTextContent(
      'The execution engine did not accept the step-up code.'
    );
    expect(screen.getByTestId('mode-badge')).toHaveTextContent('Paper');
    await user.clear(code);
    await user.type(code, DEMO_STEP_UP_CODE);
    await user.selectOptions(
      within(arm).getByRole('combobox', {name: 'Arm for'}),
      '4 hours'
    );
    expect(armButton).toHaveTextContent('Arm live for 4 hours');
    await user.click(armButton);
    expect(await screen.findByTestId('live-armed')).toHaveTextContent(
      'Live armed'
    );
    expect(grant).toHaveBeenLastCalledWith(DEMO_LIVE_REHEARSAL_ACCOUNT_ID, {
      step_up_code: DEMO_STEP_UP_CODE,
      acknowledge_risk: true,
      duration_ms: 4 * 3_600_000,
    });
    const until = (
      await broker.getAccountSummary(DEMO_LIVE_REHEARSAL_ACCOUNT_ID)
    ).live_armed_until_ms;
    expect(until).not.toBeNull();
    expect(screen.getByTestId('live-armed-until')).toHaveTextContent(
      `IBKR live rehearsal (demo) until ${hhmm(until ?? 0)} UTC`
    );
    expect(screen.getByTestId('mode-badge')).toHaveTextContent('Live money');
    await user.type(
      screen.getByRole('textbox', {name: 'Quantity (shares)'}),
      '100'
    );
    await user.type(
      screen.getByRole('textbox', {name: 'Limit price (USD)'}),
      '182.4'
    );
    await user.click(screen.getByRole('button', {name: 'Preview order'}));
    await screen.findByTestId('ticket-preview');
    const review = screen.getByTestId('confirm-order');
    expect(review).toHaveTextContent('Review LIVE order…');
    await user.click(review);
    const dialog = screen.getByRole('dialog');
    const facts = within(dialog).getByTestId('live-confirmation');
    expect(facts).toHaveTextContent(/LIVE money/);
    expect(facts).toHaveTextContent(/Interactive Brokers/);
    expect(facts).toHaveTextContent(/IBKR live rehearsal \(demo\)/);
    expect(facts).toHaveTextContent(/100 shares/);
    expect(facts).toHaveTextContent(/182\.40 USD/);
    expect(facts).toHaveTextContent(/DAY/);
    const send = within(dialog).getByRole('button', {
      name: 'Send LIVE buy 100 shares ACME at 182.40 USD',
    });
    expect(send).toBeDisabled();
    await user.click(
      within(dialog).getByRole('checkbox', {
        name: 'Send this order with real money',
      })
    );
    await user.click(send);
    await waitFor(
      async () =>
        void expect(
          (await broker.listOrders(DEMO_LIVE_REHEARSAL_ACCOUNT_ID)).orders
        ).toHaveLength(1)
    );
    const {orders} = await broker.listOrders(DEMO_LIVE_REHEARSAL_ACCOUNT_ID);
    expect(orders[0]).toMatchObject({mode: 'LIVE', time_in_force: 'DAY'});
    await user.click(screen.getByRole('button', {name: 'New order'}));
    await user.click(screen.getByRole('button', {name: 'Disarm'}));
    expect(screen.getByTestId('mode-badge')).toHaveTextContent('Paper');
    await waitFor(
      () =>
        void expect(revoke).toHaveBeenCalledWith(DEMO_LIVE_REHEARSAL_ACCOUNT_ID)
    );
    expect(
      (await broker.getAccountSummary(DEMO_LIVE_REHEARSAL_ACCOUNT_ID))
        .live_armed_until_ms
    ).toBeNull();
    expect(screen.queryByTestId('live-armed')).toBeNull();
    await user.click(screen.getByRole('button', {name: 'Live'}));
    expect(screen.getByTestId('arm-live')).toBeInTheDocument();
  });

  it('uses the server arming and asks to arm again on LIVE_NOT_ARMED', async () => {
    const broker = rehearsalBroker();
    await broker.grantAccountLiveConsent(DEMO_LIVE_REHEARSAL_ACCOUNT_ID, {
      step_up_code: DEMO_STEP_UP_CODE,
      acknowledge_risk: true,
      duration_ms: 3_600_000,
    });
    await renderOn('mock:stock:ACME', 'acct-paper-demo', {
      broker,
      select: DEMO_LIVE_REHEARSAL_ACCOUNT_ID,
    });
    const user = userEvent.setup();
    // Armed on the server already: Live needs no new step-up.
    await user.click(screen.getByRole('button', {name: 'Live'}));
    expect(screen.queryByTestId('arm-live')).toBeNull();
    expect(await screen.findByTestId('live-armed')).toBeInTheDocument();
    // Disarmed elsewhere; the ticket still shows the last poll.
    await broker.revokeAccountLiveConsent(DEMO_LIVE_REHEARSAL_ACCOUNT_ID);
    await user.type(
      screen.getByRole('textbox', {name: 'Quantity (shares)'}),
      '10'
    );
    await user.type(
      screen.getByRole('textbox', {name: 'Limit price (USD)'}),
      '182.4'
    );
    // The preview's LIVE_NOT_ARMED answer drops the ticket to paper and
    // reopens the arming step with the reason.
    await user.click(screen.getByRole('button', {name: 'Preview order'}));
    expect(await screen.findByTestId('arm-live-reason')).toHaveTextContent(
      'live trading is not armed for this account'
    );
    expect(screen.getByTestId('mode-badge')).toHaveTextContent('Paper');
    expect(screen.queryByTestId('live-armed')).toBeNull();
    const arm = screen.getByTestId('arm-live');
    await user.click(within(arm).getByRole('checkbox'));
    await user.type(
      within(arm).getByRole('textbox', {name: 'Step-up code'}),
      DEMO_STEP_UP_CODE
    );
    await user.click(screen.getByTestId('arm-live-confirm'));
    expect(await screen.findByTestId('live-armed')).toBeInTheDocument();
    expect(screen.queryByTestId('arm-live')).toBeNull();
    expect(screen.getByTestId('mode-badge')).toHaveTextContent('Live money');
  });

  it('keeps LIVE off for DEMO accounts and explains broker states', async () => {
    await renderOn('mock:stock:9901', DEMO_TWD_ACCOUNT_ID, {
      select: 'acct-cathay-demo',
    });
    expect(screen.getByRole('button', {name: 'Live'})).toBeDisabled();
    expect(screen.getByTestId('live-disabled-reason')).toHaveTextContent(
      /awaiting its broker API SDK/
    );
    expect(screen.getByTestId('account-status')).toHaveTextContent(
      'Awaiting API SDK'
    );
  });
});

describe('accounts tab', () => {
  it('lists accounts with routing, server credentials and cash', async () => {
    rendered = await renderFeature(
      PORTFOLIO_FEATURE,
      'bottom-panel',
      'portfolio.accounts'
    );
    const usd = await screen.findByTestId('account-row-acct-paper-demo');
    expect(usd).toHaveTextContent('Crypto, Nasdaq, NYSE, Cboe BZX');
    expect(usd).toHaveTextContent('Simulation');
    const twd = screen.getByTestId(`account-row-${DEMO_TWD_ACCOUNT_ID}`);
    expect(twd).toHaveTextContent('TWSE');
    expect(twd).toHaveTextContent('3,000,000.00 TWD');
    const ibkr = screen.getByTestId(`account-row-${DEMO_IBKR_ACCOUNT_ID}`);
    expect(ibkr).toHaveTextContent('Interactive Brokers');
    expect(ibkr).toHaveTextContent('Nasdaq, NYSE, Cboe BZX');
    expect(ibkr).toHaveTextContent('Connected');
    expect(ibkr).toHaveTextContent('Credentials configured on the server');
    expect(ibkr).toHaveTextContent('Paper, Shadow');
    const live = screen.getByTestId('account-row-acct-sinopac-live-demo');
    expect(live).toHaveTextContent('Disconnected');
    expect(live).toHaveTextContent('Credentials not configured on the server');
    const cathay = screen.getByTestId('account-row-acct-cathay-demo');
    expect(cathay).toHaveTextContent('Awaiting API SDK');
    expect(cathay).toHaveTextContent('No trading API yet');
    expect(screen.getByText(/Awaiting the Cathay API SDK/)).toBeInTheDocument();
    expect(
      screen.getByText(
        /Broker credentials are configured on the execution server/
      )
    ).toBeInTheDocument();
  });
});
