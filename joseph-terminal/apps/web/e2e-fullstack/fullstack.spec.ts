/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Browser → real engines: market data and backend AOIs (areas
 * of interest) render, seconds bars and true delta come from the engine,
 * engine-backed drawings read engine series, a paper order placed in the
 * ticket reaches the execution engine, a built-in Python strategy
 * backtests and activates on the engine, and notification subscriptions
 * are stored server-side.
 *
 * Runs with `playwright.fullstack.config.ts`, which starts both engines and
 * a production build of the terminal; the test backend enables only the
 * `mock` venue.
 */

import {expect, test, type Page} from '@playwright/test';
import {API, TOKEN} from '../playwright.fullstack.config';
import {
  armTool,
  clickChart,
  hoverChart,
  objectDetail,
  pickTimeframe,
  showDataWindow,
} from '../e2e/support';
import {
  loadTerminal,
  openPanel,
  previewOrder,
  ticket,
} from '../e2e/trading-helpers';

/** Authorization header for calling the engines directly. */
const AUTH = {Authorization: `Bearer ${TOKEN}`};

/**
 * Pane title of the DEMO venue BTC contract: its id until the instrument
 * metadata arrives, then the engine's display symbol.
 */
const DEMO_BTC = /^(BTCUSD|BTC-USD DEMO)$/;

/**
 * Loads the terminal and switches the active chart to the DEMO venue BTC
 * instrument served by the backend (the test backend enables only `mock`).
 * @param page Playwright page.
 */
async function loadOnDemoVenue(page: Page): Promise<void> {
  await loadTerminal(page);
  const symbol = page
    .getByTestId('chart-pane-chart-1')
    .locator('.pane__symbol');
  // Search results can re-render while they load, so a click may land on a
  // list that is being replaced: retry the pick until the chart switches.
  await expect(async () => {
    if (DEMO_BTC.test((await symbol.textContent()) ?? '')) return;
    if (!(await page.getByLabel('Search instruments').isVisible())) {
      await page.getByTestId('instrument-selector').click();
    }
    await page.getByLabel('Search instruments').fill('BTC');
    await page
      .getByRole('option', {name: /BTCUSD/})
      .first()
      .click({timeout: 5_000});
    await expect(symbol).toHaveText(DEMO_BTC, {timeout: 5_000});
  }).toPass({timeout: 45_000});
  await expect(page.getByText('Loading candles…')).toHaveCount(0);
}

test('an unavailable venue shows an error, never synthetic prices', async ({
  page,
}) => {
  // The default chart is a Binance instrument, which the test backend does
  // not enable. `.` matches either apostrophe.
  await loadTerminal(page);
  await expect(page.getByText(/Candles didn.t load/)).toBeVisible();
  await expect(
    page.getByText(/venue binance is not enabled/).first()
  ).toBeVisible();
  await expect(page.getByText(/Demo mode: synthetic prices/)).toHaveCount(0);
  await page.screenshot({path: 'e2e/screenshots/10-fullstack-unavailable.png'});
});

test('terminal runs on backend data, not the in-browser demo provider', async ({
  page,
}) => {
  // Listen before loading so the response is not missed; the engine may
  // still be warming up, hence the long timeout.
  const aois = page.waitForResponse(
    response =>
      response.url().startsWith(`${API.NEXT_PUBLIC_MARKET_API_URL}/v1/aois`) &&
      response.url().includes('mock%3Aperp%3ABTCUSD') &&
      response.ok(),
    {timeout: 60_000}
  );
  await loadOnDemoVenue(page);
  // Only the fields the test reads.
  const body = (await (await aois).json()) as {
    aois: Array<{source: string; kind: string}>;
    feed_state: string;
  };
  // Python-computed AOIs arrived from the market engine and are provenance
  // labelled; the in-browser DEMO provider is not in use.
  expect(body.aois.length).toBeGreaterThan(0);
  expect(body.feed_state).toBe('DEMO');
  await expect(page.getByText(/Demo mode: synthetic prices/)).toHaveCount(0);
  await expect(page.locator('canvas').first()).toBeVisible();
  // Let the backend layers draw before the screenshot.
  await page.waitForTimeout(1500);
  await page.screenshot({path: 'e2e/screenshots/09-fullstack-terminal.png'});
});

test('engine indicator series and pivot levels reach the chart', async ({
  page,
}) => {
  const series = page.waitForResponse(
    response =>
      response
        .url()
        .startsWith(`${API.NEXT_PUBLIC_MARKET_API_URL}/v1/indicators/series`) &&
      response.url().includes('mock%3Aperp%3ABTCUSD') &&
      response.url().includes('plugin=volume_delta') &&
      response.ok(),
    {timeout: 60_000}
  );
  const aois = page.waitForResponse(
    response =>
      response.url().startsWith(`${API.NEXT_PUBLIC_MARKET_API_URL}/v1/aois`) &&
      response.url().includes('mock%3Aperp%3ABTCUSD') &&
      response.ok(),
    {timeout: 60_000}
  );
  await loadOnDemoVenue(page);
  // The Delta % sub-pane is fed by the engine's volume delta built-in, bar
  // by bar.
  const seriesBody = (await (await series).json()) as {
    plugin_id: string;
    indicator_version: string;
    points: Array<{bar_start_ms: number; values: Record<string, unknown>}>;
  };
  expect(seriesBody.plugin_id).toBe('volume_delta');
  expect(seriesBody.indicator_version).not.toBe('demo');
  expect(seriesBody.points.length).toBeGreaterThan(0);
  expect(seriesBody.points[0]?.values).toHaveProperty('delta_pct');
  // The pivot points built-in publishes the floor levels as AOIs.
  const aoiBody = (await (await aois).json()) as {
    aois: Array<{source: string; kind: string}>;
  };
  const pivots = aoiBody.aois.filter(aoi => aoi.source === 'pivot_points');
  expect(pivots.length).toBeGreaterThan(0);
  expect(pivots.every(aoi => aoi.kind.startsWith('PIVOT_'))).toBe(true);
  // The data window shows the engine's Delta % under the crosshair.
  await page.getByTestId('indicators-menu').click();
  await page.getByTestId('data-window-toggle').check();
  await page.keyboard.press('Escape');
  const chart = page
    .getByTestId('chart-pane-chart-1')
    .locator('canvas')
    .first();
  const box = await chart.boundingBox();
  if (!box) throw new Error('chart canvas has no box');
  await page.mouse.move(box.x + box.width * 0.75, box.y + box.height * 0.4);
  await expect(page.getByTestId('data-window')).toContainText(
    /Delta %\s*[−+-]?\d/
  );
  await page.screenshot({path: 'e2e/screenshots/20-fullstack-indicators.png'});
});

/** Series point as served by `/v1/indicators/series`. */
interface SeriesBody {
  plugin_id: string;
  indicator_version: string;
  points: Array<{bar_start_ms: number; values: Record<string, unknown>}>;
}

test('seconds bars and true delta come from the engine', async ({
  page,
  request,
}) => {
  const market = API.NEXT_PUBLIC_MARKET_API_URL;
  const venues = (await (await request.get(`${market}/v1/venues`)).json()) as {
    venues: Array<{
      capabilities: {
        venue: string;
        timeframes: string[];
        trade_built_timeframes?: string[];
        market_data: {taker_volume?: boolean};
      };
    }>;
  };
  const mock = venues.venues.find(v => v.capabilities.venue === 'mock');
  expect(mock?.capabilities.timeframes).toEqual(
    expect.arrayContaining(['1s', '5s', '1m', '1M'])
  );
  expect(mock?.capabilities.trade_built_timeframes).toContain('5s');
  expect(mock?.capabilities.market_data.taker_volume).toBe(true);
  // Seconds bars are folded from the venue's trades and carry taker volume.
  const candles = await request.get(`${market}/v1/candles`, {
    params: {instrument_id: 'mock:perp:BTCUSD', tf: '5s', limit: 60},
  });
  expect(candles.ok()).toBeTruthy();
  const bars = (
    (await candles.json()) as {
      candles: Array<{
        start_ms: number;
        end_ms: number;
        taker_buy_base_volume: string | null;
      }>;
    }
  ).candles;
  expect(bars.length).toBeGreaterThan(10);
  const [first] = bars;
  expect(first ? first.end_ms - first.start_ms : 0).toBe(5_000);
  expect(bars.filter(b => b.taker_buy_base_volume !== null).length).toBe(
    bars.length
  );

  await loadOnDemoVenue(page);
  await showDataWindow(page);
  const series = page.waitForResponse(
    response =>
      response.url().startsWith(`${market}/v1/indicators/series`) &&
      response.url().includes('plugin=volume_delta') &&
      response.url().includes('tf=5s') &&
      response.ok(),
    {timeout: 90_000}
  );
  await pickTimeframe(page, '5s');
  const body = (await (await series).json()) as SeriesBody;
  expect(body.indicator_version).not.toBe('demo');
  expect(body.points.some(p => p.values.method === 'taker_volume')).toBe(true);
  // A 5 s bar without trades shows no delta, so sweep the crosshair until
  // it rests on a bar that has one.
  const dataWindow = page.getByTestId('data-window');
  let fraction = 0.75;
  await expect(async () => {
    await hoverChart(page, fraction, 0.4);
    fraction = fraction > 0.3 ? fraction - 0.03 : 0.75;
    await expect(dataWindow).toContainText('True (taker volume)', {
      timeout: 1_000,
    });
  }).toPass({timeout: 30_000});
  await page.screenshot({path: 'e2e/screenshots/28-fullstack-seconds.png'});
});

test('an anchored VWAP drawing reads the engine series', async ({page}) => {
  await loadOnDemoVenue(page);
  const series = page.waitForResponse(
    response =>
      response
        .url()
        .startsWith(`${API.NEXT_PUBLIC_MARKET_API_URL}/v1/indicators/series`) &&
      response.url().includes('plugin=anchored_vwap') &&
      response.ok(),
    {timeout: 60_000}
  );
  await armTool(page, 'studies', 'anchored-vwap');
  await clickChart(page, 0.5, 0.5);
  const body = (await (await series).json()) as SeriesBody;
  expect(body.plugin_id).toBe('anchored_vwap');
  expect(body.indicator_version).not.toBe('demo');
  const priced = body.points.filter(p => p.values.vwap !== null);
  expect(priced.length).toBeGreaterThan(0);
  expect(priced[0]?.values.anchor_in_window).toBe(true);
  await openPanel(page, 'Objects');
  await expect(objectDetail(page, 'anchored-vwap')).toHaveText(
    /^VWAP [\d,]+\.\d+ from \d{4}-\d\d-\d\d \d\d:\d\d UTC/
  );
  await page.screenshot({
    path: 'e2e/screenshots/29-fullstack-anchored-vwap.png',
  });
});

test('a paper order from the ticket is created by the execution engine', async ({
  page,
  request,
}) => {
  await loadOnDemoVenue(page);
  await openPanel(page, 'Order ticket');
  await previewOrder(page, {type: 'Market', side: 'Buy', quantity: '0.002'});
  const confirm = ticket(page).getByTestId('confirm-order');
  await expect(confirm).toHaveText(/Place paper market buy 0\.002 BTC/);
  await confirm.click();
  await expect
    .poll(
      async () => {
        const response = await request.get(
          `${API.NEXT_PUBLIC_EXECUTION_API_URL}/v1/orders`,
          {headers: AUTH}
        );
        const body = (await response.json()) as {
          orders: Array<{
            quantity_base: string;
            source?: string;
            state: string;
          }>;
        };
        return body.orders.filter(o => o.quantity_base.startsWith('0.002'))
          .length;
      },
      {timeout: 20_000}
    )
    .toBeGreaterThan(0);
  await openPanel(page, 'Orders');
  await expect(page.getByText(/0\.002/).first()).toBeVisible();
});

test('a built-in Python strategy backtests and activates on the engine', async ({
  page,
}) => {
  await loadOnDemoVenue(page);
  await openPanel(page, 'Strategy');
  await page
    .getByRole('combobox', {name: 'Strategy script'})
    .selectOption('builtin:ema-trend-long');
  await page.getByRole('tab', {name: 'Backtest'}).click();
  await page.getByRole('button', {name: 'Run backtest'}).click();
  const report = page.getByTestId('backtest-report');
  await expect(report.getByText('Win rate')).toBeVisible({timeout: 30_000});
  await expect(report.getByText(/Demo report/)).toHaveCount(0);
  await page.getByRole('tab', {name: 'Activate'}).click();
  await page.getByRole('button', {name: 'Activate', exact: true}).click();
  const list = page.getByRole('list', {name: 'Activations'});
  await expect(list.getByText(/^builtin:ema-trend-long:v/)).toBeVisible();
  await list.getByRole('button', {name: 'Delete'}).first().click();
});

test('a notification subscription saved in the terminal is stored server-side', async ({
  page,
  request,
}) => {
  await loadTerminal(page);
  await openPanel(page, 'Notifications');
  const form = page.getByRole('form', {name: 'Notification settings'});
  await form.getByLabel('Name').fill('desk-hook');
  await form.getByLabel('Channel').selectOption('WEBHOOK');
  await form.getByLabel('HTTPS URL').fill('https://hooks.example.com/joseph');
  await form.getByRole('button', {name: 'Save notification'}).click();
  await expect(page.getByRole('row', {name: /desk-hook/})).toContainText(
    'Not delivered'
  );
  const stored = await request.get(
    `${API.NEXT_PUBLIC_MARKET_API_URL}/v1/alerts/subscriptions`,
    {headers: AUTH}
  );
  const body = (await stored.json()) as {
    subscriptions: Array<{subscription_id: string; channel: string}>;
  };
  expect(body.subscriptions).toContainEqual(
    expect.objectContaining({subscription_id: 'desk-hook', channel: 'WEBHOOK'})
  );
});
