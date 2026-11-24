/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Shared helpers for the F6 trading end-to-end specs (DEMO
 * mode). Panels are opened by their slot contribution titles, which the
 * shell renders as tabs (falling back to buttons with the same name).
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
 * Loads the terminal in DEMO mode and waits for the shell.
 * @param page Playwright page.
 * @return Promise resolving when the order ticket feature is available.
 */
export async function loadTerminal(page: Page): Promise<void> {
  await page.goto('/');
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
 * Returns the order ticket region.
 * @param page Playwright page.
 * @return Ticket locator.
 */
export function ticket(page: Page): Locator {
  return page.getByTestId('order-ticket');
}

/**
 * Fills and previews a paper order in the ticket.
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
