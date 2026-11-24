/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview End-to-end: paper order lifecycle through the ticket, the
 * Orders and Positions tabs, close and cancel (DEMO mode).
 */

import {expect, test} from '@playwright/test';
import {loadTerminal, openPanel, previewOrder, ticket} from './trading-helpers';

test.describe('paper trading', () => {
  test('market order fills into a position that can be closed', async ({
    page,
  }) => {
    await loadTerminal(page);
    await previewOrder(page, {type: 'Market', side: 'Buy', quantity: '0.01'});
    const confirm = ticket(page).getByTestId('confirm-order');
    await expect(confirm).toHaveText(/^Place paper market buy 0\.010 BTC/);
    await confirm.click();
    await expect(ticket(page).getByTestId('ticket-result')).toContainText(
      /submitting/i
    );
    await expect(ticket(page).getByTestId('ticket-result')).not.toContainText(
      /filled/i
    );

    await openPanel(page, 'Positions');
    const position = page.getByTestId('position-mock:perp:BTCUSD');
    await expect(position).toBeVisible({timeout: 10_000});
    // Fills arrive in parts; close only once the whole order has filled so
    // the close covers the full position (closing mid-fill correctly leaves
    // the late remainder open, as a real venue would).
    await expect(position.locator('td').nth(2)).toHaveText(/^0\.010?$/, {
      timeout: 10_000,
    });
    await position.getByRole('button', {name: 'Close'}).click();
    const dialog = page.getByRole('dialog');
    const closeButton = dialog.getByRole('button', {
      name: /Close 100% of long/,
    });
    await expect(closeButton).toBeEnabled();
    await closeButton.click();
    await expect(position).toHaveCount(0, {timeout: 10_000});

    // The DEMO broker fills marketable orders in two parts, so the open and
    // the close each produce two executions (plus the header row).
    await openPanel(page, 'Executions');
    await expect(
      page.getByRole('table', {name: 'Executions'}).getByRole('row')
    ).toHaveCount(5, {timeout: 10_000});
  });

  test('resting limit order can be canceled and moves to history', async ({
    page,
  }) => {
    await loadTerminal(page);
    await previewOrder(page, {
      type: 'Limit',
      side: 'Buy',
      quantity: '0.01',
      limitPrice: '1000',
    });
    await ticket(page).getByTestId('confirm-order').click();
    await expect(ticket(page).getByTestId('ticket-result')).toBeVisible();

    await openPanel(page, 'Orders');
    const row = page
      .getByRole('table', {name: 'Open orders'})
      .getByRole('row', {name: /Working/});
    await expect(row).toBeVisible({timeout: 10_000});
    await row.getByRole('button', {name: 'Cancel'}).click();
    await page
      .getByRole('dialog')
      .getByRole('button', {name: /^Cancel buy limit/})
      .click();
    await openPanel(page, 'Order history');
    await expect(
      page.getByRole('table', {name: 'Order history'}).getByText('Canceled')
    ).toBeVisible({timeout: 10_000});
  });

  test('live mode cannot be selected and double submit creates one order', async ({
    page,
  }) => {
    await loadTerminal(page);
    await openPanel(page, 'Order ticket');
    await expect(
      ticket(page).getByRole('button', {name: 'Live'})
    ).toBeDisabled();
    await previewOrder(page, {
      type: 'Limit',
      side: 'Buy',
      quantity: '0.01',
      limitPrice: '1000',
    });
    await ticket(page).getByTestId('confirm-order').dblclick();
    await openPanel(page, 'Orders');
    await expect(
      page
        .getByRole('table', {name: 'Open orders'})
        .getByRole('row', {name: /Working|Submitting/})
    ).toHaveCount(1, {timeout: 10_000});
  });
});
