/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview End-to-end: built-in Python strategy → inputs → DEMO
 * backtest → paper activation, with automatic live trading visibly locked
 * (DEMO mode).
 */

import {expect, test} from '@playwright/test';
import {loadTerminal, openPanel} from './trading-helpers';

test('backtests and activates a built-in strategy', async ({page}) => {
  await loadTerminal(page);
  await openPanel(page, 'Strategy');
  const picker = page.getByRole('combobox', {name: 'Strategy script'});
  await picker.selectOption('builtin:rsi-reversion-short');
  await expect(
    page.getByRole('spinbutton', {name: 'RSI crosses below'})
  ).toHaveValue('70');
  await page.getByRole('tab', {name: 'Backtest'}).click();
  await page.getByRole('button', {name: 'Run backtest'}).click();
  const report = page.getByTestId('backtest-report');
  await expect(report.getByText(/Demo report/)).toBeVisible();
  await expect(
    report.getByRole('table', {name: 'Backtest trades'})
  ).toBeVisible();
  await page.getByRole('button', {name: 'Show on chart'}).click();
  await expect(
    page.getByRole('button', {name: 'Hide from chart'})
  ).toBeVisible();
  await page.getByRole('tab', {name: 'Activate'}).click();
  await expect(page.getByTestId('auto-live-lock')).toContainText(
    'Automatic live trading is locked'
  );
  await page
    .getByRole('combobox', {name: 'Activation mode'})
    .selectOption('PAPER');
  await page.getByRole('button', {name: 'Activate', exact: true}).click();
  const list = page.getByRole('list', {name: 'Activations'});
  await expect(
    list.getByText('builtin:rsi-reversion-short:vdemo')
  ).toBeVisible();
  await list.getByRole('button', {name: 'Pause'}).click();
  await expect(list.getByText('paused')).toBeVisible();
});
