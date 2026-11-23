/**
 * @fileoverview Component tests for the equity order ticket (routing by
 * `tradable_venues`, TWSE board and intraday odd lots, tick ladder, price
 * limits, time in force and order types from the account's capabilities,
 * IBKR outside-RTH), the rate no-ticket rule, arming LIVE and the LIVE
 * confirmation, and the Accounts tab.
 */
import {
  DEMO_IBKR_ACCOUNT_ID,
  DEMO_SINOPAC_ACCOUNT_ID,
  DEMO_TWD_ACCOUNT_ID,
  DemoExecutionClient,
} from '@joseph/mock-provider';
import type {
  AccountList,
  OrderIntentRequest,
  OrderPreview,
  SubmitIntentResponse,
} from '@joseph/contracts';
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

/** DEMO broker that rehearses LIVE on the IBKR account (tests only). */
class LiveRehearsalBroker extends DemoExecutionClient {
  readonly submitted: OrderIntentRequest[] = [];

  /**
   * Lists accounts with LIVE permitted on IBKR.
   * @return Accounts.
   */
  override async listAccounts(): Promise<AccountList> {
    const list = await super.listAccounts();
    return {
      accounts: list.accounts.map(a =>
        a.account_id === DEMO_IBKR_ACCOUNT_ID
          ? {...a, modes: [...a.modes, 'LIVE' as const]}
          : a
      ),
    };
  }

  /**
   * Previews as paper and reports the requested mode.
   * @param request Request.
   * @return Allowed preview.
   */
  override async previewOrder(
    request: OrderIntentRequest
  ): Promise<OrderPreview> {
    const preview = await super.previewOrder({...request, mode: 'PAPER'});
    return {
      ...preview,
      allowed: true,
      normalized: {...preview.normalized, mode: request.mode},
    };
  }

  /**
   * Records the request and fills it as paper.
   * @param request Request.
   * @param key Idempotency key.
   * @return Submission.
   */
  override async submitIntent(
    request: OrderIntentRequest,
    key: string
  ): Promise<SubmitIntentResponse> {
    this.submitted.push(request);
    return super.submitIntent(
      {...request, mode: 'PAPER', preview_token: null},
      key
    );
  }
}

/** Rehearsal broker that also verifies a step-up code. */
class StepUpBroker extends LiveRehearsalBroker {
  readonly grantAccountLiveConsent = vi.fn(async () => ({
    expires_at_ms: Date.now() + 60_000,
  }));
}

/**
 * Renders the ticket on an instrument and waits for the routed account,
 * then optionally switches to another account.
 * @param instrumentId Instrument.
 * @param routed Expected routed account.
 * @param options Broker and account to switch to.
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
 * Texts of the time-in-force options.
 * @return Option texts.
 */
function tifOptions(): (string | null)[] {
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
      await new Promise(resolve => setTimeout(resolve, 10));
    });
    const Menu = ctx.contributions('chart-context-menu')[0]?.view;
    if (!Menu) throw new Error('no menu');
    const {container} = render(<Menu ctx={ctx} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe('live trading', () => {
  it('arms LIVE, then confirms a LIVE order in a dedicated step', async () => {
    const broker = new LiveRehearsalBroker({
      ackDelayMs: 0,
      fillDelayMs: 0,
      partialFills: false,
    });
    await renderOn('mock:stock:ACME', 'acct-paper-demo', {
      broker,
      select: DEMO_IBKR_ACCOUNT_ID,
    });
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', {name: 'Live'}));
    const arm = screen.getByTestId('arm-live');
    expect(arm).toHaveTextContent(
      'Live orders from this ticket go to Interactive Brokers account "IBKR paper (demo)" with real money.'
    );
    expect(
      within(arm).queryByRole('textbox', {name: 'Step-up code'})
    ).toBeNull();
    const armButton = screen.getByTestId('arm-live-confirm');
    expect(armButton).toBeDisabled();
    await user.click(within(arm).getByRole('checkbox'));
    await user.click(armButton);
    expect(await screen.findByTestId('live-armed')).toHaveTextContent(
      'Live armed'
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
    expect(facts).toHaveTextContent(/IBKR paper \(demo\)/);
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
    await waitFor(() => expect(broker.submitted).toHaveLength(1));
    expect(broker.submitted[0]).toMatchObject({
      mode: 'LIVE',
      time_in_force: 'DAY',
    });
    await user.click(screen.getByRole('button', {name: 'New order'}));
    await user.click(screen.getByRole('button', {name: 'Disarm'}));
    expect(screen.getByTestId('mode-badge')).toHaveTextContent('Paper');
  });

  it('sends a step-up code when the engine verifies live consent', async () => {
    const broker = new StepUpBroker({ackDelayMs: 0, fillDelayMs: 0});
    await renderOn('mock:stock:ACME', 'acct-paper-demo', {
      broker,
      select: DEMO_IBKR_ACCOUNT_ID,
    });
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', {name: 'Live'}));
    const arm = screen.getByTestId('arm-live');
    await user.click(within(arm).getByRole('checkbox'));
    const code = within(arm).getByRole('textbox', {name: 'Step-up code'});
    await user.type(code, '12345');
    expect(screen.getByTestId('arm-live-confirm')).toBeDisabled();
    await user.type(code, '6');
    await user.click(screen.getByTestId('arm-live-confirm'));
    expect(await screen.findByTestId('live-armed')).toBeInTheDocument();
    expect(broker.grantAccountLiveConsent).toHaveBeenCalledWith(
      DEMO_IBKR_ACCOUNT_ID,
      {step_up_code: '123456', acknowledge_risk: true, duration_ms: 900_000}
    );
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
    expect(usd).toHaveTextContent('Crypto, Nasdaq, NYSE');
    expect(usd).toHaveTextContent('Simulation');
    const twd = screen.getByTestId(`account-row-${DEMO_TWD_ACCOUNT_ID}`);
    expect(twd).toHaveTextContent('TWSE');
    expect(twd).toHaveTextContent('3,000,000.00 TWD');
    const ibkr = screen.getByTestId(`account-row-${DEMO_IBKR_ACCOUNT_ID}`);
    expect(ibkr).toHaveTextContent('Interactive Brokers');
    expect(ibkr).toHaveTextContent('Nasdaq, NYSE');
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
