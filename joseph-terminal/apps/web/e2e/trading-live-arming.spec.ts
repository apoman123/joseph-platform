/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview End-to-end (DEMO): server-side LIVE arming rehearsed on the
 * opt-in simulated IBKR live account (`?demo-live-rehearsal=1`). Live opens
 * the arm step; a wrong step-up code is refused; the DEMO code arms the
 * account until a shown time; a LIVE preview is then allowed; Disarm and an
 * expired arming both return the ticket to paper and Live needs arming
 * again. Without the opt-in no DEMO account offers LIVE.
 */

import {expect, test, type Page} from '@playwright/test';
import {openPanel, ticket} from './trading-helpers';

const SHOTS = 'e2e/screenshots';

/** Saturday 2026-11-28 04:00 UTC (Friday 23:00 New York): NYSE closed. */
const SATURDAY = new Date('2026-11-28T04:00:00Z');

/** The opt-in DEMO live rehearsal account. */
const REHEARSAL = 'acct-ibkr-live-rehearsal-demo';

/** Step-up code the DEMO broker accepts. */
const DEMO_CODE = '123456';

/**
 * Loads the terminal with clean storage, optionally with the rehearsal.
 * @param page Page.
 * @param rehearsal Whether to opt in to the LIVE rehearsal account.
 */
async function load(page: Page, rehearsal = true): Promise<void> {
  const url = rehearsal ? '/?demo-live-rehearsal=1' : '/';
  await page.goto(url);
  await page.evaluate(() => {
    try {
      window.localStorage.clear();
    } catch {
      // Storage may be unavailable; the app degrades to memory.
    }
  });
  await page.reload();
  await expect(
    page
      .getByRole('tab', {name: 'Order ticket', exact: true})
      .or(page.getByRole('button', {name: 'Order ticket', exact: true}))
      .first()
  ).toBeVisible();
}

/**
 * Picks ACME and routes the ticket to the rehearsal account.
 * @param page Page.
 */
async function rehearsalTicket(page: Page): Promise<void> {
  await page.getByTestId('instrument-selector').click();
  await page.getByLabel('Search instruments').fill('ACME');
  await page.getByRole('option', {name: /ACME/}).first().click();
  await expect(page.getByText('Loading candles…')).toHaveCount(0);
  await openPanel(page, 'Order ticket');
  const account = ticket(page).getByRole('combobox', {name: 'Account'});
  await expect(account).toHaveValue('acct-paper-demo');
  await account.selectOption(REHEARSAL);
}

/**
 * Arms LIVE from the open arm step.
 * @param page Page.
 * @param duration Lifetime label.
 */
async function arm(page: Page, duration: string): Promise<void> {
  const panel = ticket(page).getByTestId('arm-live');
  await panel.getByRole('checkbox').check();
  await panel.getByRole('combobox', {name: 'Arm for'}).selectOption(duration);
  await panel.getByRole('textbox', {name: 'Step-up code'}).fill(DEMO_CODE);
  await panel.getByTestId('arm-live-confirm').click();
  await expect(ticket(page).getByTestId('live-armed')).toBeVisible();
}

test.beforeEach(async ({page}) => {
  await page.clock.install({time: SATURDAY});
});

test('arms LIVE with a step-up code, shows until when and disarms', async ({
  page,
}) => {
  await load(page);
  await rehearsalTicket(page);
  const t = ticket(page);
  await expect(t.getByTestId('account-status')).toBeVisible();
  await t.getByRole('button', {name: 'Live'}).click();
  const panel = t.getByTestId('arm-live');
  await expect(panel).toContainText(
    'Live orders from this ticket go to Interactive Brokers account "IBKR live rehearsal (demo)" with real money.'
  );
  await expect(panel).toContainText(`accepts the step-up code ${DEMO_CODE}`);
  const confirm = panel.getByTestId('arm-live-confirm');
  await expect(confirm).toBeDisabled();
  await panel.getByRole('checkbox').check();
  await panel.getByRole('textbox', {name: 'Step-up code'}).fill('000000');
  await expect(confirm).toHaveText('Arm live for 1 hour');
  await confirm.click();
  await expect(panel.getByRole('alert')).toContainText(
    'The execution engine did not accept the step-up code.'
  );
  await expect(t.getByTestId('mode-badge')).toContainText('Paper');
  await arm(page, '15 minutes');
  await expect(t.getByTestId('live-armed-until')).toHaveText(
    'IBKR live rehearsal (demo) until 04:15 UTC'
  );
  await expect(t.getByTestId('mode-badge')).toContainText('Live money');
  await t.getByRole('textbox', {name: 'Quantity (shares)'}).fill('10');
  await t.getByRole('textbox', {name: 'Limit price (USD)'}).fill('182.4');
  await t.getByRole('button', {name: /Preview order|Preview again/}).click();
  const preview = t.getByTestId('ticket-preview');
  await expect(preview).toContainText('Live is armed until 04:15 UTC');
  await expect(t.getByTestId('confirm-order')).toHaveText('Review LIVE order…');
  await expect(t.getByTestId('confirm-order')).toBeEnabled();
  await page.waitForTimeout(300);
  await page.screenshot({path: `${SHOTS}/42-live-armed-ticket.png`});
  await t.getByRole('button', {name: 'Disarm'}).click();
  await expect(t.getByTestId('mode-badge')).toContainText('Paper');
  await expect(t.getByTestId('live-armed')).toHaveCount(0);
  await expect(
    page.getByText(/Live trading disarmed for IBKR live/)
  ).toBeVisible();
  await t.getByRole('button', {name: 'Live'}).click();
  await expect(t.getByTestId('arm-live')).toBeVisible();
});

test('an expired arming returns the ticket to paper', async ({page}) => {
  await load(page);
  await rehearsalTicket(page);
  const t = ticket(page);
  await t.getByRole('button', {name: 'Live'}).click();
  await arm(page, '15 minutes');
  await expect(t.getByTestId('mode-badge')).toContainText('Live money');
  await page.clock.fastForward('16:00');
  await expect(t.getByTestId('mode-badge')).toContainText('Paper');
  await expect(t.getByTestId('live-armed')).toHaveCount(0);
  await expect(
    page.getByText(
      'Live trading was disarmed; the ticket is back in paper mode.'
    )
  ).toBeVisible();
  await t.getByRole('button', {name: 'Live'}).click();
  await expect(t.getByTestId('arm-live')).toBeVisible();
});

test('without the opt-in no DEMO account offers LIVE', async ({page}) => {
  await load(page, false);
  await openPanel(page, 'Order ticket');
  const account = ticket(page).getByRole('combobox', {name: 'Account'});
  await expect(account.locator(`option[value="${REHEARSAL}"]`)).toHaveCount(0);
  await expect(ticket(page).getByRole('button', {name: 'Live'})).toBeDisabled();
});
