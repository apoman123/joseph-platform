/**
 * @fileoverview Browser → real engines: market data and backend AOIs render,
 * a paper order placed in the ticket reaches the execution engine, and the
 * strategy builder validates against the Python compiler.
 */
import {expect, test, type Page} from '@playwright/test';
import {API, TOKEN} from '../playwright.fullstack.config';
import {
  loadTerminal,
  openPanel,
  previewOrder,
  ticket,
} from '../e2e/trading-helpers';

const AUTH = {Authorization: `Bearer ${TOKEN}`};

/**
 * Loads the terminal and switches the active chart to the DEMO venue BTC
 * instrument served by the backend (the test backend enables only `mock`).
 * @param page Playwright page.
 */
async function loadOnDemoVenue(page: Page): Promise<void> {
  await loadTerminal(page);
  await page.getByTestId('instrument-selector').click();
  await page.getByLabel('Search instruments').fill('BTC');
  await page
    .getByRole('option', {name: /BTCUSD/})
    .first()
    .click();
  await expect(
    page.getByTestId('chart-pane-chart-1').locator('.pane__symbol')
  ).toHaveText('BTCUSD');
  await expect(page.getByText('Loading candles…')).toHaveCount(0);
}

test('an unavailable venue shows an error, never synthetic prices', async ({
  page,
}) => {
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
  const aois = page.waitForResponse(
    response =>
      response.url().startsWith(`${API.NEXT_PUBLIC_MARKET_API_URL}/v1/aois`) &&
      response.url().includes('mock%3Aperp%3ABTCUSD') &&
      response.ok(),
    {timeout: 60_000}
  );
  await loadOnDemoVenue(page);
  const body = (await (await aois).json()) as {
    aois: {source: string; kind: string}[];
    feed_state: string;
  };
  // Python-computed AOIs arrived from the market engine and are provenance
  // labelled; the in-browser DEMO provider is not in use.
  expect(body.aois.length).toBeGreaterThan(0);
  expect(body.feed_state).toBe('DEMO');
  await expect(page.getByText(/Demo mode: synthetic prices/)).toHaveCount(0);
  await expect(page.locator('canvas').first()).toBeVisible();
  await page.waitForTimeout(1500);
  await page.screenshot({path: 'e2e/screenshots/09-fullstack-terminal.png'});
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
          orders: {quantity_base: string; source?: string; state: string}[];
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

test('strategy builder validates on the Python compiler', async ({page}) => {
  await loadTerminal(page);
  await openPanel(page, 'Strategy');
  await page
    .getByRole('combobox', {name: 'Start from a template'})
    .selectOption({label: 'Short B: premium OTE rejection'});
  await page.getByRole('button', {name: 'Validate'}).click();
  await expect(page.getByText(/Valid on the server/)).toBeVisible();
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
    subscriptions: {subscription_id: string; channel: string}[];
  };
  expect(body.subscriptions).toContainEqual(
    expect.objectContaining({subscription_id: 'desk-hook', channel: 'WEBHOOK'})
  );
});
