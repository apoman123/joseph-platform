/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview End-to-end: the kill switch blocks new opening orders and
 * is released explicitly (DEMO mode).
 */

import {expect, test} from '@playwright/test';
import {loadTerminal, openPanel, previewOrder, ticket} from './trading-helpers';

test('kill switch blocks a new opening order until released', async ({
  page,
}) => {
  await loadTerminal(page);
  await openPanel(page, 'Risk controls');
  await page
    .getByRole('textbox', {name: 'Reason (recorded in the audit log)'})
    .fill('e2e drill');
  await page.getByRole('button', {name: 'Review kill switch'}).click();
  const consequences = page.getByTestId('kill-switch-consequences');
  await expect(consequences).toContainText('New opening orders are blocked');
  await expect(consequences).toContainText('Open orders stay working.');
  await page.getByRole('button', {name: 'Engage global kill switch'}).click();
  await expect(page.getByTestId('engaged-switch')).toContainText('e2e drill');

  await previewOrder(page, {
    type: 'Limit',
    side: 'Buy',
    quantity: '0.01',
    limitPrice: '1000',
  });
  await expect(ticket(page).getByTestId('ticket-preview')).toContainText(
    'Kill switch engaged'
  );
  await expect(ticket(page).getByTestId('confirm-order')).toBeDisabled();

  await openPanel(page, 'Risk controls');
  await page.getByRole('button', {name: 'Release global kill switch'}).click();
  await page
    .getByRole('dialog')
    .getByRole('button', {name: 'Release global kill switch'})
    .click();
  await expect(page.getByTestId('engaged-switch')).toHaveCount(0);

  await openPanel(page, 'Audit');
  await expect(page.getByTestId('audit-chain')).toHaveText('Chain verified');
});
