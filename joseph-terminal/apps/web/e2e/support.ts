/**
 * @fileoverview Shared helpers for terminal system tests.
 */
import {expect, type Locator, type Page} from '@playwright/test';

/** Storage key of BTC drawings in the default workspace. */
export const BTC_DRAWINGS_KEY = 'joseph.drawings.v1.default.mock:perp:BTCUSD';

/**
 * Opens the terminal with clean storage and waits for candles.
 * @param page Page.
 * @param options Whether to clear storage first.
 */
export async function openTerminal(
  page: Page,
  options: {clear?: boolean} = {clear: true}
): Promise<void> {
  if (options.clear) {
    await page.addInitScript(() => {
      if (!sessionStorage.getItem('e2e-cleared')) {
        localStorage.clear();
        sessionStorage.setItem('e2e-cleared', 'yes');
      }
    });
  }
  await page.goto('/');
  await expect(page.locator('.terminal[data-ready="true"]')).toBeVisible();
  await expect(
    page.locator('[data-testid^="chart-pane-"] canvas').first()
  ).toBeVisible();
  await expect(page.getByText('Loading candles…')).toHaveCount(0);
}

/**
 * Returns the first chart canvas host.
 * @param page Page.
 * @return Locator.
 */
export function firstCanvas(page: Page): Locator {
  return page.getByTestId('chart-canvas').first();
}

/**
 * Drags across the chart between relative positions.
 * @param page Page.
 * @param from Start as fractions of the canvas box.
 * @param to End as fractions of the canvas box.
 */
export async function dragOnChart(
  page: Page,
  from: [number, number],
  to: [number, number]
): Promise<void> {
  const box = await firstCanvas(page).boundingBox();
  if (!box) throw new Error('chart canvas not visible');
  await page.mouse.move(
    box.x + box.width * from[0],
    box.y + box.height * from[1]
  );
  await page.mouse.down();
  await page.mouse.move(
    box.x + box.width * ((from[0] + to[0]) / 2),
    box.y + box.height * ((from[1] + to[1]) / 2),
    {steps: 4}
  );
  await page.mouse.move(box.x + box.width * to[0], box.y + box.height * to[1], {
    steps: 4,
  });
  await page.mouse.up();
}

/**
 * Reads saved drawing tool ids from storage.
 * @param page Page.
 * @param key Storage key.
 * @return Tool ids.
 */
export async function savedTools(
  page: Page,
  key = BTC_DRAWINGS_KEY
): Promise<string[]> {
  return page.evaluate(storageKey => {
    const raw = localStorage.getItem(storageKey);
    if (!raw) return [];
    return (JSON.parse(raw) as {drawings: {tool_id: string}[]}).drawings.map(
      d => d.tool_id
    );
  }, key);
}
