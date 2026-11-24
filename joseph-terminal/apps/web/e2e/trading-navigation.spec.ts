/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview End-to-end: screener rows and alerts focus exact venue
 * instruments on the chart (DEMO mode).
 */

import {expect, test} from '@playwright/test';
import {loadTerminal, openPanel, ticket} from './trading-helpers';

test('clicking a screener row selects that exact instrument', async ({
  page,
}) => {
  await loadTerminal(page);
  await openPanel(page, 'Screener');
  const rows = page
    .getByRole('table', {name: 'Qualification results'})
    .locator('tbody tr[data-testid^="screener-row-"]');
  await expect(rows.first()).toBeVisible({timeout: 10_000});
  const testId = (await rows.last().getAttribute('data-testid')) ?? '';
  const instrumentId = testId.replace('screener-row-', '');
  await rows.last().click();
  await openPanel(page, 'Order ticket');
  // The ticket's instrument label carries the full instrument id as title.
  await expect(
    ticket(page).locator(`[title="${instrumentId}"]`).first()
  ).toBeVisible();
});

test('an alert focuses its instrument on the chart', async ({page}) => {
  await loadTerminal(page);
  await openPanel(page, 'Alerts');
  const row = page.getByTestId('signal-demo-signal-2');
  await expect(row).toBeVisible({timeout: 10_000});
  await row.getByRole('button', {name: 'Entry blocked'}).click();
  await expect(page.getByRole('list', {name: 'Why this signal'})).toContainText(
    'Open interest snapshot missing'
  );
  await row.getByRole('button', {name: 'Show on chart'}).click();
  await openPanel(page, 'Order ticket');
  await expect(
    ticket(page).locator('[title="mock:perp:ETHUSD"]').first()
  ).toBeVisible();
});
