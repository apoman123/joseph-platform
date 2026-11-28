/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Browser tests in DEMO mode for seconds and monthly bars
 * (grouped menu, typed resolutions, bar-close countdown, data window), the
 * true order-flow delta and the signal ranking by bar volume.
 */

import {expect, test, type Page} from '@playwright/test';
import {firstCanvas, openTerminal, pickTimeframe} from './support';
import {openPanel} from './trading-helpers';

/** Folder of the documentation screenshots, relative to `apps/web`. */
const SHOTS = 'e2e/screenshots';

/**
 * Hovers a fixed point of the first chart so the data window shows a bar.
 * @param page Page.
 * @throws `Error` when the chart canvas is not visible.
 */
async function hoverChart(page: Page): Promise<void> {
  const box = await firstCanvas(page).boundingBox();
  if (!box) throw new Error('no chart');
  await page.mouse.move(box.x + box.width * 0.6, box.y + box.height * 0.3);
}

test('charts seconds and monthly bars with a countdown and true delta', async ({
  page,
}) => {
  await openTerminal(page);
  const pane = page.getByTestId('chart-pane-chart-1');
  await page.getByTestId('indicators-menu').click();
  await page.getByTestId('data-window-toggle').check();
  await page.keyboard.press('Escape');

  await pickTimeframe(page, '5s');
  await expect(pane.locator('.pane__chip').nth(1)).toHaveText('5s');
  await expect(pane.getByTestId('bar-countdown')).toHaveText(/^0:0\d$/);
  await hoverChart(page);
  const data = page.getByTestId('data-window');
  await expect(data).toContainText(/\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} UTC/);
  await expect(data).toContainText(/Delta %\s*[−+-]?\d/);
  await expect(data).toContainText('Delta % Buy volume');
  await expect(data).toContainText('Delta % Coverage %100%');
  // Let the panes repaint before the screenshot.
  await page.waitForTimeout(300);
  await page.screenshot({path: `${SHOTS}/24-seconds-true-delta.png`});

  // Typed TradingView-style resolution: 1M is a calendar month.
  await page
    .getByRole('navigation', {name: 'Timeframe'})
    .locator('.timeframes__more')
    .click();
  await page.getByLabel('Type a resolution').fill('1M');
  await page.getByLabel('Type a resolution').press('Enter');
  await expect(pane.locator('.pane__chip').nth(1)).toHaveText('1M');
  await expect(page.getByText('Loading candles…')).toHaveCount(0);
  await expect(pane.getByTestId('bar-countdown')).toHaveText(
    /^(\d+d \d{2}:\d{2}|\d+:\d{2}(:\d{2})?)$/
  );
  await hoverChart(page);
  await expect(data).toContainText(
    /(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) \d{4}/
  );
  await expect(data).toContainText(/Closes\s*\d{4}-\d{2}-01 00:00 UTC/);
});

test('ranks signals by the signal bar quote turnover', async ({page}) => {
  await openTerminal(page);
  await openPanel(page, 'Alerts');
  await expect(
    page.locator('[data-testid^="signal-demo-signal-"]').first()
  ).toBeVisible();
  await page.getByTestId('alerts-sort').selectOption('volume');
  const cells = page.getByTestId('signal-volume');
  await expect(cells.first()).toHaveText(/^\d+(\.\d)?[KMB]?$/);
  await expect(
    page.getByRole('columnheader', {name: 'Bar vol.'})
  ).toHaveAttribute('title', /quote-currency turnover of the signal bar/i);
  const texts = await cells.allTextContents();
  const scale: Record<string, number> = {K: 1e3, M: 1e6, B: 1e9};
  const values = texts.map(text => {
    const match = /^([\d.]+)([KMB]?)$/.exec(text);
    return match ? Number(match[1]) * (scale[match[2] ?? ''] ?? 1) : -1;
  });
  expect(values).toEqual([...values].sort((a, b) => b - a));
});
