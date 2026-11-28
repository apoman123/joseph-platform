/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/** @fileoverview Quiet sessions versus open-market outages over real APIs. */

import {expect, test, type Page} from '@playwright/test';
import type {CandleBatch, MarketSnapshot} from '@joseph/contracts';
import {API} from '../playwright.fullstack.config';

/**
 * Leaves the stream quiet while retaining real backend candles and metadata.
 * @param page Test page whose clock and backend responses are controlled.
 * @param symbol Exact mock symbol: ACME for XNYS or BTCUSD for 24x7.
 * @param marketType Canonical mock market type used by the symbol selector.
 * @return Resolves after candles and per-bar volume are visible.
 */
async function openQuietChart(
  page: Page,
  symbol: string,
  marketType: 'stock' | 'perp'
): Promise<void> {
  await page.clock.install({time: new Date('2026-11-28T02:00:00Z')});
  await page.routeWebSocket(
    `${API.NEXT_PUBLIC_MARKET_WS_URL}/v1/stream/market*`,
    () => undefined
  );
  await page.route('**/v1/candles?**', async route => {
    const response = await route.fetch();
    if (!response.ok()) {
      await route.fulfill({response});
      return;
    }
    const batch = (await response.json()) as CandleBatch;
    await route.fulfill({response, json: {...batch, feed_state: 'POLL'}});
  });
  await page.route('**/v1/market/snapshot?**', async route => {
    const response = await route.fetch();
    if (!response.ok()) {
      await route.fulfill({response});
      return;
    }
    const snapshot = (await response.json()) as MarketSnapshot;
    await route.fulfill({
      response,
      json: {
        ...snapshot,
        feed_state: 'STALE',
        stale: true,
        data_age_ms: 420_000,
      },
    });
  });
  await page.goto('/');
  await page.locator('.terminal[data-ready="true"]').waitFor();
  await page.getByTestId('instrument-selector').click();
  await page.getByRole('textbox', {name: 'Search instruments'}).fill(symbol);
  await page
    .getByTestId(`instrument-option-mock:${marketType}:${symbol}`)
    .click();
  await expect(page.getByText('Loading candles…', {exact: true})).toHaveCount(
    0
  );
  await expect(page.getByTestId('bar-volume')).toContainText(/\d/);
}

test.afterEach(async ({page}) => {
  await page.unrouteAll({behavior: 'ignoreErrors'});
});

test('closed exchange sessions stay quiet, then stale warnings resume after the opening grace', async ({
  page,
}) => {
  await openQuietChart(page, 'ACME', 'stock');
  const pane = page.getByTestId('chart-pane-chart-1');
  await expect(pane.getByTestId('listing-exchange')).toContainText('DEMO');
  await expect(pane.getByTestId('market-status')).toHaveAttribute(
    'data-state',
    'closed'
  );
  await page.clock.fastForward(7 * 60_000);
  await expect(pane.getByTestId('provenance-badge')).toContainText(
    'Market closed'
  );
  await expect(page.getByText(/No update for/)).toHaveCount(0);
  await expect(page.getByRole('tabpanel', {name: 'Market'})).not.toContainText(
    /Stale \d/
  );
  // Monday's regular XNYS open. No new quotes are injected by the fixture.
  await page.clock.setSystemTime(new Date('2026-11-30T14:30:01Z'));
  await page.clock.runFor(1000);
  await expect(pane.getByTestId('market-status')).toHaveAttribute(
    'data-state',
    'open'
  );
  await expect(page.getByText(/No update for/)).toHaveCount(0);
  await page.clock.runFor(60_000);
  await expect(pane.getByText(/No update for/)).toBeVisible();
});

test('continuous crypto still warns about missing prices on weekends', async ({
  page,
}) => {
  await openQuietChart(page, 'BTCUSD', 'perp');
  await page.clock.runFor(70_000);
  await expect(
    page.getByTestId('chart-pane-chart-1').getByText(/No update for/)
  ).toBeVisible();
});
