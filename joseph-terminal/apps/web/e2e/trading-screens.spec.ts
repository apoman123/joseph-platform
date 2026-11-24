/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Captures review screenshots of the trading and strategy
 * panels (DEMO mode) into `e2e/screenshots/`.
 */

import {expect, test} from '@playwright/test';
import {loadTerminal, openPanel, previewOrder} from './trading-helpers';

test('order ticket preview, positions and risk checks', async ({page}) => {
  await loadTerminal(page);
  await previewOrder(page, {type: 'Market', side: 'Buy', quantity: '0.01'});
  await expect(page.getByTestId('confirm-order')).toBeVisible();
  await page.screenshot({path: 'e2e/screenshots/06-order-preview.png'});
});

test('strategy builder with the RSI reversion template', async ({page}) => {
  await loadTerminal(page);
  await openPanel(page, 'Strategy');
  await page
    .getByRole('combobox', {name: 'Start from a template'})
    .selectOption({label: 'RSI reversion (short)'});
  await expect(
    page.locator('[data-testid^="node-"]').getByText('RSI crosses below 70')
  ).toBeVisible();
  await page.screenshot({path: 'e2e/screenshots/07-strategy-builder.png'});
});

test('screener and alerts panels', async ({page}) => {
  await loadTerminal(page);
  await openPanel(page, 'Screener');
  await openPanel(page, 'Alerts');
  await page.screenshot({path: 'e2e/screenshots/08-screener-alerts.png'});
});

test('notification subscriptions panel', async ({page}) => {
  await loadTerminal(page);
  await openPanel(page, 'Notifications');
  await expect(page.getByTestId('notifications-panel')).toBeVisible();
  await page.screenshot({path: 'e2e/screenshots/11-notifications.png'});
});
