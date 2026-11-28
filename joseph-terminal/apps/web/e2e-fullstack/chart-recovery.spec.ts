/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Browser regressions for stalled history, tab isolation and
 * feed-mode subscriptions against both running engines.
 */

import {expect, test, type Page} from '@playwright/test';
import type {CandleBatch} from '@joseph/contracts';
import {API} from '../playwright.fullstack.config';
import {pickTimeframe} from '../e2e/support';

/**
 * Selects a backend mock instrument through the normal symbol picker.
 * @param page Browser page containing a loaded terminal.
 * @param symbol Mock symbol, such as BTCUSD or ETHUSD.
 * @return Promise resolving after the picker selects the instrument.
 */
async function selectInstrument(page: Page, symbol: string): Promise<void> {
  await page.getByTestId('instrument-selector').click();
  await page.getByRole('textbox', {name: 'Search instruments'}).fill(symbol);
  await page.getByTestId(`instrument-option-mock:perp:${symbol}`).click();
}

test('an open chart automatically reloads revised adjusted history without retaining old prices', async ({
  page,
}) => {
  await page.clock.install();
  await page.routeWebSocket(
    `${API.NEXT_PUBLIC_MARKET_WS_URL}/v1/stream/market*`,
    () => undefined
  );
  let batches = 0;
  await page.route('**/v1/candles?**', async route => {
    const response = await route.fetch();
    const url = new URL(route.request().url());
    if (
      !response.ok() ||
      url.searchParams.get('instrument_id') !== 'mock:perp:BTCUSD'
    ) {
      await route.fulfill({response});
      return;
    }
    const batch = (await response.json()) as CandleBatch;
    batches += 1;
    // Prices honor the mock BTC instrument's 0.1 tick on every render.
    const price = batches === 1 ? '188.8' : '47.2';
    await route.fulfill({
      response,
      json: {
        ...batch,
        price_adjustment: 'ALL',
        price_adjustment_epoch_ms: batches === 1 ? 0 : 900_000,
        price_adjustment_refresh_after_ms:
          batches === 1 ? Date.now() + 1000 : Date.now() + 3_600_000,
        candles: batch.candles.map(candle => ({
          ...candle,
          open: price,
          high: price,
          low: price,
          close: price,
        })),
      },
    });
  });
  await page.goto('/');
  await page.locator('.terminal[data-ready="true"]').waitFor();
  await selectInstrument(page, 'BTCUSD');
  const pane = page.getByTestId('chart-pane-chart-1');
  await expect(pane.getByTestId('price-adjustment')).toHaveText(
    'Adjusted · splits & dividends'
  );
  await expect(pane.locator('.pane__ohlc')).toContainText('188.8');
  await page.clock.runFor(30_000);
  await expect.poll(() => batches).toBeGreaterThan(1);
  await expect(pane.locator('.pane__ohlc')).toContainText('47.2');
  await expect(pane.locator('.pane__ohlc')).not.toContainText('188.8');
});

test('monthly stock history loads when 500 bars predate the exchange calendar', async ({
  page,
}) => {
  await page.goto('/');
  await page.locator('.terminal[data-ready="true"]').waitFor();
  await page.getByTestId('instrument-selector').click();
  await page.getByRole('textbox', {name: 'Search instruments'}).fill('ACME');
  await page.getByTestId('instrument-option-mock:stock:ACME').click();
  const loaded = page.waitForResponse(response => {
    const url = new URL(response.url());
    return (
      url.pathname === '/v1/candles' &&
      url.searchParams.get('instrument_id') === 'mock:stock:ACME' &&
      url.searchParams.get('tf') === '1M' &&
      url.searchParams.get('limit') === '500'
    );
  });
  await pickTimeframe(page, '1M');
  expect((await loaded).status()).toBe(200);
  await expect(
    page.getByText('Candles didn’t load', {exact: true})
  ).toHaveCount(0);
  await expect(page.locator('.pane__ohlc')).toContainText(/C\s*[\d,]+/);
});

test('stalled history times out and Retry restores real backend candles', async ({
  page,
}) => {
  // A quiet or disconnected quote stream must not mask a stalled REST read.
  await page.routeWebSocket(
    `${API.NEXT_PUBLIC_MARKET_WS_URL}/v1/stream/market*`,
    () => undefined
  );
  let stalled = true;
  const pending: Array<() => void> = [];
  await page.route('**/v1/candles?**', async route => {
    const url = new URL(route.request().url());
    if (
      stalled &&
      url.searchParams.get('instrument_id') === 'mock:perp:BTCUSD' &&
      url.searchParams.get('limit') === '500'
    ) {
      await new Promise<void>(resolve => pending.push(resolve));
      await route.abort().catch(() => undefined);
    } else {
      await route.continue();
    }
  });
  await page.goto('/');
  await page.locator('.terminal[data-ready="true"]').waitFor();
  await selectInstrument(page, 'BTCUSD');
  await expect(page.getByText('Loading candles…', {exact: true})).toBeVisible();
  await expect.poll(() => pending.length).toBeGreaterThan(0);
  const pane = page.getByTestId('chart-pane-chart-1');
  await expect(pane.getByRole('alert')).toContainText(
    'GET /v1/candles timed out',
    {timeout: 35_000}
  );
  await expect(page.getByText('Loading candles…', {exact: true})).toHaveCount(
    0
  );
  stalled = false;
  for (const release of pending) {
    release();
  }
  const recovered = page.waitForResponse(response => {
    const url = new URL(response.url());
    return (
      url.pathname === '/v1/candles' &&
      url.searchParams.get('instrument_id') === 'mock:perp:BTCUSD' &&
      url.searchParams.get('limit') === '500' &&
      response.ok()
    );
  });
  await pane.getByRole('button', {name: 'Retry candles'}).click();
  const batch = (await (await recovered).json()) as {candles: unknown[]};
  expect(batch.candles.length).toBeGreaterThan(0);
  await expect(pane.getByRole('button', {name: 'Retry candles'})).toHaveCount(
    0
  );
  await expect(pane.locator('.pane__ohlc')).toContainText(/C\s*[\d,]+/);
  await expect(pane.locator('canvas').first()).toBeVisible();
});

test('backend tabs retain independent indicators and subscribe to changed feed modes', async ({
  page,
}) => {
  const channels: string[] = [];
  page.on('websocket', socket => {
    socket.on('framereceived', frame => {
      const envelope = JSON.parse(String(frame.payload)) as {
        type?: string;
        channel?: string;
      };
      if (envelope.type === 'candle.updated' && envelope.channel) {
        channels.push(envelope.channel);
      }
    });
  });
  await page.goto('/');
  await page.locator('.terminal[data-ready="true"]').waitFor();
  await selectInstrument(page, 'BTCUSD');
  await expect(page.getByText('Loading candles…', {exact: true})).toHaveCount(
    0
  );
  await page.getByTestId('indicators-menu').click();
  const library = page.getByRole('dialog', {name: 'Indicators', exact: true});
  await library
    .getByRole('textbox', {name: 'Search indicators'})
    .fill('Relative');
  await library.getByTestId('add-study-builtin:rsi').click();
  await library
    .getByRole('button', {name: 'Collect Relative Strength Index'})
    .click();
  await page.keyboard.press('Escape');
  await page.getByRole('button', {name: 'Add chart tab'}).click();
  await page.getByRole('button', {name: 'Candlestick chart'}).click();
  await selectInstrument(page, 'ETHUSD');
  await pickTimeframe(page, '1h');
  const tabs = page.getByRole('tablist', {name: 'Chart tabs'});
  await expect(tabs.getByRole('tab', {selected: true})).toHaveText(
    'ETHUSD · 1h'
  );
  await page.getByTestId('indicators-menu').click();
  await expect(
    library.getByRole('checkbox', {name: 'RSI', exact: true})
  ).toHaveCount(0);
  await library.getByRole('tab', {name: /My collection/}).click();
  await expect(library.getByTestId('add-study-builtin:rsi')).toBeVisible();
  await page.keyboard.press('Escape');
  await tabs.getByRole('tab', {name: 'BTCUSD · 5m'}).click();
  await expect(page.getByText('Loading candles…', {exact: true})).toHaveCount(
    0
  );
  await page.getByTestId('indicators-menu').click();
  await expect(
    library.getByRole('checkbox', {name: 'RSI', exact: true})
  ).toBeChecked();
  await page.keyboard.press('Escape');
  await page.getByLabel('Feed mode', {exact: true}).selectOption('REST_POLL');
  await expect
    .poll(() => channels.includes('candles:mock:perp:BTCUSD:5m:REST_POLL'))
    .toBe(true);
  await page.getByLabel('Feed mode', {exact: true}).selectOption('NATIVE_WS');
  await expect
    .poll(() => channels.includes('candles:mock:perp:BTCUSD:5m:NATIVE_WS'))
    .toBe(true);
  await expect(page.getByText('Loading candles…', {exact: true})).toHaveCount(
    0
  );
  await expect(page.locator('.pane__ohlc')).toContainText(/C\s*[\d,]+/);
});
