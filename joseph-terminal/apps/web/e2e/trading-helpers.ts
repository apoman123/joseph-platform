/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Shared helpers for the trading browser tests (DEMO mode):
 * loading the terminal on clean storage, opening panels and previewing a
 * paper order. Panels are opened by their slot contribution titles, which
 * the shell renders as tabs (falling back to buttons with the same name).
 */

import {expect, type Locator, type Page} from '@playwright/test';

/**
 * Opens a side-panel or bottom-panel contribution by its title.
 * @param page Playwright page.
 * @param title Contribution title, e.g. `Order ticket`.
 * @return Promise resolving when the tab is selected.
 */
export async function openPanel(page: Page, title: string): Promise<void> {
  const tab = page.getByRole('tab', {name: title, exact: true});
  if ((await tab.count()) > 0) {
    await tab.first().click();
    return;
  }
  await page.getByRole('button', {name: title, exact: true}).first().click();
}

/**
 * Loads the terminal in DEMO mode on clean storage and waits for the shell.
 * @param page Playwright page.
 * @return Promise resolving when the order ticket feature is available.
 */
export async function loadTerminal(page: Page): Promise<void> {
  // Storage can be cleared only from a page on the app's origin, so the
  // first load only provides the origin; the reload starts clean.
  await page.goto('/');
  await page.evaluate(() => {
    try {
      window.localStorage.clear();
    } catch {
      // Storage may be unavailable; the app then keeps state in memory.
    }
  });
  await page.reload();
  // The tab appears once the trading feature has activated.
  await expect(
    page
      .getByRole('tab', {name: 'Order ticket', exact: true})
      .or(page.getByRole('button', {name: 'Order ticket', exact: true}))
      .first()
  ).toBeVisible();
}

/**
 * Returns the order ticket region.
 * @param page Playwright page.
 * @return Ticket locator.
 */
export function ticket(page: Page): Locator {
  return page.getByTestId('order-ticket');
}

/**
 * Fills and previews a perpetual order in the ticket's current mode; the
 * lot-size hint and the `Limit price` label are the perpetual ticket's.
 * @param page Playwright page.
 * @param options Order type, side, quantity and optional limit price.
 * @return Promise resolving when the preview is shown.
 */
export async function previewOrder(
  page: Page,
  options: {
    type: 'Market' | 'Limit';
    side: 'Buy' | 'Sell';
    quantity: string;
    limitPrice?: string;
  }
): Promise<void> {
  await openPanel(page, 'Order ticket');
  const t = ticket(page);
  // The lot-size hint appears once the instrument's precision has loaded;
  // before that the quantity cannot be sized.
  await expect(t.getByText(/Lot size/)).toBeVisible();
  await t.getByRole('button', {name: options.side, exact: true}).click();
  await t
    .getByRole('group', {name: 'Order type'})
    .getByRole('button', {name: options.type})
    .click();
  await t.getByRole('textbox', {name: /Quantity/}).fill(options.quantity);
  if (options.limitPrice) {
    await t
      .getByRole('textbox', {name: 'Limit price'})
      .fill(options.limitPrice);
  }
  await t.getByRole('button', {name: /Preview order|Preview again/}).click();
  await expect(t.getByTestId('ticket-preview')).toBeVisible();
}
