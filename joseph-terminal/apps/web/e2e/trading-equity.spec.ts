/**
 * @fileoverview End-to-end (DEMO): stocks route to the paper or broker
 * account listing their exchange (`tradable_venues`); the equity ticket
 * validates TWSE board lots and intraday odd lots (limit DAY only), the tick
 * ladder, the daily price limit, the account's order types and time in
 * force, and market orders while the exchange is closed; US shares on IBKR
 * offer DAY/GTC/IOC and outside RTH; a published rate has no ticket; the
 * Accounts tab shows routing, server-side credential state, settled cash
 * and Cathay awaiting its API SDK.
 */
import {expect, test, type Page} from '@playwright/test';
import {loadTerminal, openPanel, ticket} from './trading-helpers';

const SHOTS = 'e2e/screenshots';

/** Saturday 2026-11-28 12:00 Taipei (Friday 23:00 New York): all closed. */
const SATURDAY = new Date('2026-11-28T04:00:00Z');

/**
 * Picks an instrument from the selector of the active chart.
 * @param page Page.
 * @param query Search text.
 * @param option Option name pattern.
 */
async function pick(page: Page, query: string, option: RegExp): Promise<void> {
  await page.getByTestId('instrument-selector').click();
  await page.getByLabel('Search instruments').fill(query);
  await page.getByRole('option', {name: option}).first().click();
  await expect(page.getByText('Loading candles…')).toHaveCount(0);
}

test.beforeEach(async ({page}) => {
  await page.clock.setFixedTime(SATURDAY);
});

test('validates TWSE lots, ladder, limits and closed-market orders', async ({
  page,
}) => {
  await loadTerminal(page);
  await pick(page, '9901', /9901/);
  await openPanel(page, 'Order ticket');
  const t = ticket(page);
  const account = t.getByRole('combobox', {name: 'Account'});
  // TWSE routes to the TWD paper account; switch to the SinoPac simulation.
  await expect(account).toHaveValue('acct-twd-paper-demo');
  await account.selectOption('acct-sinopac-demo');
  await expect(t.getByTestId('account-status')).toContainText(
    'Credentials configured on the server'
  );
  await expect(t.getByTestId('account-cash')).toContainText(
    'Settled cash 5,000,000.00 TWD'
  );
  await expect(t.getByTestId('market-hours')).toContainText(
    'TWSE is closed now. The next session opens Mon 30 Nov 09:00 Taipei.'
  );
  // SinoPac lists no stop orders and DAY/IOC/FOK only.
  await expect(t.getByRole('button', {name: 'Stop'})).toHaveCount(0);
  await expect(
    t.getByRole('combobox', {name: 'Time in force'}).locator('option')
  ).toHaveText([
    'DAY · Day (this session)',
    'IOC · Immediate or cancel',
    'FOK · Fill or kill',
  ]);

  // Board lots: quantity in 張, sent as shares.
  await t
    .getByRole('textbox', {name: 'Quantity (張, 1,000 shares each)'})
    .fill('2');
  await expect(t.getByText('= 2 張 (2,000 shares)')).toBeVisible();
  const limit = t.getByRole('textbox', {name: 'Limit price (TWD)'});
  await limit.fill('585.4');
  await expect(
    t.getByText('Rounded to the tick ladder (1 at this price): 585 TWD.')
  ).toBeVisible();
  await limit.fill('9999');
  await expect(t.getByText(/outside today's price limit/)).toBeVisible();
  await expect(t.getByTestId('outside-rth')).toHaveCount(0);
  await expect(t.getByRole('textbox', {name: 'Leverage'})).toHaveCount(0);

  // SinoPac does not queue market orders while TWSE is closed.
  await t
    .getByRole('group', {name: 'Order type'})
    .getByRole('button', {name: 'Market'})
    .click();
  await expect(t.getByTestId('order-type-error')).toHaveText(
    'The market is closed: market orders are rejected until the next open (Mon 30 Nov 09:00 Taipei). Use a limit order.'
  );

  // Intraday odd lots are limit DAY only and fewer shares than a lot.
  await t.getByRole('button', {name: 'Odd lot (零股)'}).click();
  await expect(t.getByTestId('lot-hint')).toHaveText(
    'Intraday odd lots are limit orders, DAY (ROD) only.'
  );
  await expect(
    t.getByRole('group', {name: 'Order type'}).getByRole('button', {
      name: 'Market',
    })
  ).toBeDisabled();
  await expect(
    t.getByRole('combobox', {name: 'Time in force'}).locator('option')
  ).toHaveText(['DAY · Day (this session)']);
  const shares = t.getByRole('textbox', {name: 'Quantity (shares)'});
  await shares.fill('1500');
  await expect(t.getByText(/odd-lot order is 1 to 999 shares/)).toBeVisible();
  await shares.fill('350');
  await t.getByRole('textbox', {name: 'Limit price (TWD)'}).fill('585');
  await expect(t.getByText('= 350 shares (odd lot)')).toBeVisible();
  await t.getByRole('button', {name: /Preview order|Preview again/}).click();
  await expect(t.getByTestId('ticket-preview')).toContainText(
    'Market closed; the order rests until the next open.'
  );
  await expect(t.getByTestId('confirm-order')).toHaveText(
    'Place paper limit buy 350 shares (odd lot) 9901 at 585 TWD'
  );
  await expect(t.getByRole('button', {name: 'Live'})).toBeDisabled();
  await page.waitForTimeout(300);
  await page.screenshot({path: `${SHOTS}/38-equity-ticket-twse.png`});
});

test('routes US shares to the paper or IBKR account with DAY and outside RTH', async ({
  page,
}) => {
  await loadTerminal(page);
  await pick(page, 'ACME', /ACME/);
  await openPanel(page, 'Order ticket');
  const t = ticket(page);
  const account = t.getByRole('combobox', {name: 'Account'});
  // The USD paper account trades crypto, Nasdaq, NYSE and Cboe BZX.
  await expect(account).toHaveValue('acct-paper-demo');
  await account.selectOption('acct-ibkr-demo');
  await expect(
    t.getByRole('combobox', {name: 'Time in force'}).locator('option')
  ).toHaveText([
    'DAY · Day (this session)',
    'GTC · Good till canceled',
    'IOC · Immediate or cancel',
  ]);
  await expect(t.getByTestId('market-hours')).toContainText(
    'NYSE is closed now. The next session opens Mon 30 Nov 09:30 New York.'
  );
  // IBKR queues DAY market orders placed while closed.
  await t
    .getByRole('group', {name: 'Order type'})
    .getByRole('button', {name: 'Market'})
    .click();
  await expect(t.getByTestId('market-hours')).toContainText(
    'A DAY market order placed now queues for the next open.'
  );
  await expect(t.getByTestId('order-type-error')).toHaveCount(0);
  await t
    .getByRole('group', {name: 'Order type'})
    .getByRole('button', {name: 'Limit'})
    .click();
  const rth = t.getByTestId('outside-rth');
  await expect(rth).toBeEnabled();
  await rth.check();
  await expect(t.getByTestId('market-hours')).toContainText(
    'Outside RTH is on'
  );
  await t.getByRole('textbox', {name: 'Quantity (shares)'}).fill('100');
  await t.getByRole('textbox', {name: 'Limit price (USD)'}).fill('182.4');
  await t.getByRole('button', {name: /Preview order|Preview again/}).click();
  await expect(t.getByTestId('ticket-preview')).toContainText('Cash required');
  await expect(t.getByTestId('ticket-preview')).toContainText('extended hours');
  await expect(t.getByTestId('confirm-order')).toHaveText(
    'Place paper limit buy 100 shares ACME at 182.40 USD'
  );
  await page.waitForTimeout(300);
  await page.screenshot({path: `${SHOTS}/39-equity-ticket-ibkr.png`});
  // A Taiwan broker account does not route US listings.
  await account.selectOption('acct-sinopac-demo');
  await expect(
    t.getByText(
      'SinoPac Securities (Shioaji) accounts route TWSE and TPEx listings only.'
    )
  ).toBeVisible();
  await expect(t.getByTestId('outside-rth')).toHaveCount(0);
});

test('a published rate has no ticket and accounts show their state', async ({
  page,
}) => {
  await loadTerminal(page);
  await pick(page, 'UST10Y', /UST10Y/);
  await openPanel(page, 'Order ticket');
  await expect(ticket(page).getByTestId('rate-no-ticket')).toContainText(
    'US 10-Year Treasury yield (demo) is a published rate, not a tradable instrument'
  );
  await expect(ticket(page).getByTestId('confirm-order')).toHaveCount(0);

  await openPanel(page, 'Accounts');
  const table = page.getByRole('table', {name: 'Accounts'});
  await expect(table.getByTestId('account-row-acct-paper-demo')).toContainText(
    'Crypto, Nasdaq, NYSE, Cboe BZX'
  );
  await expect(
    table.getByTestId('account-row-acct-twd-paper-demo')
  ).toContainText('3,000,000.00 TWD');
  const ibkr = table.getByTestId('account-row-acct-ibkr-demo');
  await expect(ibkr).toContainText('Connected');
  await expect(ibkr).toContainText('Credentials configured on the server');
  const live = table.getByTestId('account-row-acct-sinopac-live-demo');
  await expect(live).toContainText('Disconnected');
  await expect(live).toContainText('Credentials not configured on the server');
  const cathay = table.getByTestId('account-row-acct-cathay-demo');
  await expect(cathay).toContainText('Awaiting API SDK');
  await expect(cathay).toContainText('No trading API yet');
  await page.waitForTimeout(300);
  await page.screenshot({path: `${SHOTS}/40-broker-accounts.png`});
});
