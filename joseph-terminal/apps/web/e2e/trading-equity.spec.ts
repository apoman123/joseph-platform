/**
 * @fileoverview End-to-end (DEMO): the equity order ticket validates TWSE
 * board and odd lots, the tick ladder, the daily price limit and DAY time
 * in force; US shares route to the IBKR account with DAY/GTC and the
 * outside-RTH flag; market-hours notices follow the exchange calendar; a
 * published rate has no ticket; the Accounts tab shows broker connection
 * and credential states (Cathay awaiting its API SDK).
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

test('validates TWSE lots, ladder and price limit; routes US shares to IBKR', async ({
  page,
}) => {
  await loadTerminal(page);
  await pick(page, '9901', /9901/);
  await openPanel(page, 'Order ticket');
  const t = ticket(page);
  await expect(t.getByRole('combobox', {name: 'Account'})).toHaveValue(
    'acct-sinopac-demo'
  );
  await expect(t.getByTestId('account-status')).toContainText('Connected');
  await expect(t.getByTestId('market-hours')).toContainText(
    'TWSE is closed now. The next session opens Mon 30 Nov 09:00 Taipei.'
  );

  // Board lots: quantity in 張, sent as shares.
  const lots = t.getByRole('textbox', {
    name: 'Quantity (張, 1,000 shares each)',
  });
  await lots.fill('2');
  await expect(t.getByText('= 2 張 (2,000 shares)')).toBeVisible();
  const limit = t.getByRole('textbox', {name: 'Limit price (TWD)'});
  await limit.fill('585.4');
  await expect(
    t.getByText('Rounded to the tick ladder (1 at this price): 585 TWD.')
  ).toBeVisible();
  await limit.fill('9999');
  await expect(t.getByText(/outside today's price limit/)).toBeVisible();
  const tif = t.getByRole('combobox', {name: 'Time in force'});
  await expect(tif.locator('option')).toHaveText(['DAY · Day (this session)']);
  await expect(t.getByTestId('outside-rth')).toHaveCount(0);
  await expect(t.getByRole('textbox', {name: 'Leverage'})).toHaveCount(0);

  // Odd lots are fewer shares than a board lot.
  await t.getByRole('button', {name: 'Odd lot (零股)'}).click();
  const shares = t.getByRole('textbox', {name: 'Quantity (shares)'});
  await shares.fill('1500');
  await expect(t.getByText(/odd-lot order is 1 to 999 shares/)).toBeVisible();
  await shares.fill('350');
  await limit.fill('585');
  await expect(t.getByText('= 350 shares (odd lot)')).toBeVisible();
  await t.getByRole('button', {name: /Preview order|Preview again/}).click();
  await expect(t.getByTestId('confirm-order')).toHaveText(
    'Place paper limit buy 350 shares (odd lot) 9901 at 585 TWD'
  );
  await expect(t.getByRole('button', {name: 'Live'})).toBeDisabled();
  await page.waitForTimeout(300);
  await page.screenshot({path: `${SHOTS}/38-equity-ticket-twse.png`});

  // US shares go to Interactive Brokers with DAY/GTC and outside RTH.
  await pick(page, 'ACME', /ACME/);
  await expect(t.getByRole('combobox', {name: 'Account'})).toHaveValue(
    'acct-ibkr-demo'
  );
  await expect(
    t.getByRole('combobox', {name: 'Time in force'}).locator('option')
  ).toHaveText(['DAY · Day (this session)', 'GTC · Good till canceled']);
  const rth = t.getByTestId('outside-rth');
  await expect(rth).toBeEnabled();
  await expect(t.getByTestId('market-hours')).toContainText(
    'NYSE / Nasdaq is closed now. The next session opens Mon 30 Nov 09:30 New York.'
  );
  await rth.check();
  await expect(t.getByTestId('market-hours')).toContainText(
    'Outside RTH is on'
  );
  await t.getByRole('textbox', {name: 'Quantity (shares)'}).fill('100');
  await t.getByRole('textbox', {name: 'Limit price (USD)'}).fill('182.4');
  await t.getByRole('button', {name: /Preview order|Preview again/}).click();
  await expect(t.getByTestId('ticket-preview')).toContainText('Cash required');
  await expect(t.getByTestId('confirm-order')).toHaveText(
    'Place paper limit buy 100 shares ACME at 182.40 USD'
  );
  await page.waitForTimeout(300);
  await page.screenshot({path: `${SHOTS}/39-equity-ticket-ibkr.png`});
  // A TWSE broker account does not trade US listings.
  await t
    .getByRole('combobox', {name: 'Account'})
    .selectOption('acct-sinopac-demo');
  await expect(
    t.getByText('SinoPac Securities (Shioaji) accounts trade TWSE listings.')
  ).toBeVisible();
  await expect(t.getByTestId('outside-rth')).toHaveCount(0);
});

test('a published rate has no ticket and broker accounts show their state', async ({
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
  await expect(table.getByTestId('account-row-acct-ibkr-demo')).toContainText(
    'Connected'
  );
  await expect(table.getByTestId('account-row-acct-ibkr-demo')).toContainText(
    'Interactive Brokers'
  );
  const live = table.getByTestId('account-row-acct-sinopac-live-demo');
  await expect(live).toContainText('Disconnected');
  await expect(live).toContainText(
    'Credentials missing on the execution engine'
  );
  const cathay = table.getByTestId('account-row-acct-cathay-demo');
  await expect(cathay).toContainText('Awaiting API SDK');
  await expect(cathay).toContainText('No trading API yet');
  await page.waitForTimeout(300);
  await page.screenshot({path: `${SHOTS}/40-broker-accounts.png`});
});
