/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview System tests in DEMO mode for session markets: the market
 * picker's asset-class groups and name search, a session chart without
 * overnight or weekend gaps (`mock:stock:ACME` on XNYS), the market status
 * badge, the Treasury yield as a % line chart without an order ticket, and
 * the classified (Lee–Ready) delta source of stock bars.
 */

import AxeBuilder from '@axe-core/playwright';
import {expect, test, type Page} from '@playwright/test';
import {
  firstCanvas,
  openTerminal,
  pickTimeframe,
  showDataWindow,
} from './support';
import {openPanel} from './trading-helpers';

const SHOTS = 'e2e/screenshots';

/**
 * Picks an instrument from the picker by search text.
 * @param page Page.
 * @param query Search text.
 * @param option Accessible name of the result.
 */
async function pickInstrument(
  page: Page,
  query: string,
  option: RegExp
): Promise<void> {
  await page.getByTestId('instrument-selector').click();
  await page.getByLabel('Search instruments').fill(query);
  await page.getByRole('option', {name: option}).first().click();
  await expect(page.getByText('Loading candles…')).toHaveCount(0);
}

/**
 * Sweeps the crosshair across the chart and records which bar the data
 * window shows at each x.
 * @param page Page.
 * @return Bars in order with the first x where each appeared.
 */
async function sweepBars(
  page: Page
): Promise<Array<{label: string; x: number}>> {
  const box = await firstCanvas(page).boundingBox();
  if (!box) throw new Error('no chart');
  const time = page.getByTestId('data-window').locator('.data-window__time');
  const seen: Array<{label: string; x: number}> = [];
  for (let x = box.width * 0.35; x < box.width * 0.85; x += 2) {
    await page.mouse.move(box.x + x, box.y + box.height * 0.4);
    const label = (await time.textContent()) ?? '';
    if (label && seen.at(-1)?.label !== label) seen.push({label, x});
  }
  return seen;
}

test('groups the market picker by asset class and finds Chinese names', async ({
  page,
}) => {
  await openTerminal(page);
  await page.getByTestId('instrument-selector').click();
  const listbox = page.getByRole('listbox', {name: 'Instruments'});
  for (const group of [
    'Crypto',
    'Taiwan stocks',
    'US stocks',
    'Bonds & rates',
  ]) {
    await expect(listbox.getByRole('group', {name: group})).toBeVisible();
  }
  await expect(
    listbox.getByRole('group', {name: 'US stocks'}).getByRole('option')
  ).toContainText(['ACME']);
  await page.getByTestId('market-group-bonds').click();
  await expect(listbox.getByRole('option')).toHaveCount(3);
  // A TPEx bond ETF (Taiwan hours, TWD) sits with the other bond ETFs.
  await expect(listbox.getByRole('option', {name: /00999B/})).toContainText(
    'TPEx'
  );
  await expect(listbox.getByRole('option', {name: /UST10Y/})).toContainText(
    'Yield, %'
  );
  await page.getByTestId('market-group-all').click();
  await page.getByLabel('Search instruments').fill('半導體');
  const tw = listbox.getByRole('option', {name: /9901/});
  await expect(tw).toContainText('示範半導體');
  await expect(tw).toContainText('TWSE');
  await expect(tw).toContainText('TWD');
  const axe = await new AxeBuilder({page})
    .include('.instrument-search')
    .analyze();
  expect(
    axe.violations
      .filter(v => v.impact === 'serious' || v.impact === 'critical')
      .map(v => v.id)
  ).toEqual([]);
  await page.screenshot({path: `${SHOTS}/30-market-picker.png`});
  await tw.click();
  await expect(
    page.getByTestId('chart-pane-chart-1').locator('.pane__symbol')
  ).toHaveText('9901');
});

test('charts a stock by session without overnight or weekend gaps', async ({
  page,
}) => {
  await openTerminal(page);
  await pickInstrument(page, 'ACME', /ACME/);
  const pane = page.getByTestId('chart-pane-chart-1');
  await expect(pane.getByTestId('listing-exchange')).toHaveText('NYSE · USD');
  await page
    .getByRole('navigation', {name: 'Timeframe'})
    .getByRole('button', {name: '1h', exact: true})
    .click();
  await expect(page.getByText('Loading candles…')).toHaveCount(0);
  await showDataWindow(page);
  // Session breaks mark where the night was taken out.
  await page.getByTestId('indicators-menu').click();
  await page.getByTestId('session-separators-toggle').check();
  await page.keyboard.press('Escape');

  const bars = await sweepBars(page);
  expect(bars.length).toBeGreaterThan(12);
  // Every bar is an NYSE session bar in exchange time: 09:30 … 15:30 ET.
  for (const {label} of bars) {
    expect(label).toMatch(
      /^\d{4}-\d{2}-\d{2} (09:30|10:30|11:30|12:30|13:30|14:30|15:30) ET$/
    );
  }
  // Each bar takes one slot, also across the session break: the spacing
  // from a session's last bar to the next session's first bar is the same
  // as between bars inside a session (no empty overnight or weekend room).
  const spacings = bars.slice(1).map((bar, i) => bar.x - (bars[i]?.x ?? 0));
  const sorted = [...spacings].sort((a, b) => a - b);
  const median = sorted[Math.floor(sorted.length / 2)] ?? 0;
  const breaks = bars
    .slice(1)
    .map((bar, i) => ({bar, previous: bars[i], spacing: spacings[i] ?? 0}))
    .filter(
      ({bar, previous}) =>
        bar.label.slice(0, 10) !== previous?.label.slice(0, 10)
    );
  expect(breaks.length).toBeGreaterThan(0);
  for (const {bar, previous, spacing} of breaks) {
    expect(previous?.label).toMatch(/15:30 ET$/);
    expect(bar.label).toMatch(/09:30 ET$/);
    expect(spacing).toBeLessThanOrEqual(median + 3);
  }
  // A session's last bar closes at the close (16:00, or 13:00 early).
  const last = bars.find(bar => bar.label.endsWith('15:30 ET'));
  if (!last) throw new Error('no 15:30 bar');
  const box = await firstCanvas(page).boundingBox();
  await page.mouse.move(
    (box?.x ?? 0) + last.x + 0.5,
    (box?.y ?? 0) + (box?.height ?? 0) * 0.4
  );
  await expect(page.getByTestId('data-window')).toContainText(
    /Closes\s*(16|13):00:00 ET/
  );
  await page.screenshot({path: `${SHOTS}/31-session-chart-acme.png`});

  // Market status: open or closed with the next open, in exchange time.
  const status = pane.getByTestId('market-status');
  await expect(status).toBeVisible();
  await expect(status).toHaveAttribute('data-state', /^(open|closed)$/);
  await expect(status).toContainText(/(Open|Closed|Holiday)/);
  await expect(status).toContainText(/(closes|opens) .*\d{2}:\d{2} ET/);
  // The bar countdown runs in session; between sessions the badge shows
  // the next open instead.
  if ((await status.getAttribute('data-state')) === 'open') {
    await expect(pane.getByTestId('bar-countdown')).toHaveText(
      /^\d+:\d{2}(:\d{2})?$/
    );
  } else {
    await expect(pane.getByTestId('bar-countdown')).toHaveCount(0);
  }
  await status.screenshot({path: `${SHOTS}/32-market-status.png`});
});

test('shows stock delta as classified (Lee–Ready)', async ({page}) => {
  await openTerminal(page);
  await pickInstrument(page, 'ACME', /ACME/);
  await showDataWindow(page);
  const box = await firstCanvas(page).boundingBox();
  if (!box) throw new Error('no chart');
  await page.mouse.move(box.x + box.width * 0.6, box.y + box.height * 0.3);
  const data = page.getByTestId('data-window');
  await expect(data).toContainText('Classified (Lee–Ready)');
  await expect(data).toContainText(/Buy volume\s*[\d,]+ sh/);
  await page.waitForTimeout(300);
  await page.screenshot({path: `${SHOTS}/34-classified-delta.png`});
});

test('charts the 10-year yield as a % line with no order ticket', async ({
  page,
}) => {
  await openTerminal(page);
  await pickInstrument(page, 'UST10Y', /UST10Y/);
  const pane = page.getByTestId('chart-pane-chart-1');
  await expect(pane.locator('.pane__symbol')).toHaveText('UST10Y');
  await expect(page.locator('#chart-type')).toHaveValue('line');
  await expect(pane).toContainText('Yield %');
  await pickTimeframe(page, '1d');
  await showDataWindow(page);
  const box = await firstCanvas(page).boundingBox();
  if (!box) throw new Error('no chart');
  await page.mouse.move(box.x + box.width * 0.6, box.y + box.height * 0.3);
  await expect(page.getByTestId('data-window')).toContainText(
    /Close\s*\d+\.\d{3}%/
  );
  await expect(pane.getByTestId('market-status')).toBeVisible();
  await openPanel(page, 'Order ticket');
  const ticket = page.getByTestId('order-ticket');
  await expect(ticket).toContainText(/not a tradable instrument/i);
  await expect(ticket.getByTestId('confirm-order')).toHaveCount(0);
  await page.waitForTimeout(300);
  await page.screenshot({path: `${SHOTS}/33-rate-line-chart.png`});
  // Leaving the yield restores candles.
  await pickInstrument(page, 'BTC', /BTCUSD/);
  await expect(page.locator('#chart-type')).toHaveValue('candles');
});
