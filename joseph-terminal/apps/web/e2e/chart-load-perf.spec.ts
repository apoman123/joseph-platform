/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Chart load timing in DEMO mode: time from navigation to the
 * first drawn candles, and from a timeframe switch back to an already
 * viewed series until its candles are drawn again. The timings are printed
 * for comparison between builds; the budgets only catch regressions far
 * beyond them.
 */

import {expect, test, type Page} from '@playwright/test';
import {pickTimeframe} from './support';

/**
 * Waits until the first chart shows candles and no pane is loading.
 * @param page Page.
 */
async function candlesDrawn(page: Page): Promise<void> {
  await expect(
    page.locator('[data-testid^="chart-pane-"] canvas').first()
  ).toBeVisible();
  await expect(page.getByText('Loading candles…')).toHaveCount(0, {
    timeout: 30_000,
  });
}

test('draws the first candles quickly and revisits series instantly', async ({
  page,
}) => {
  await page.addInitScript(() => {
    if (!sessionStorage.getItem('e2e-cleared')) {
      localStorage.clear();
      sessionStorage.setItem('e2e-cleared', 'yes');
    }
  });
  const started = Date.now();
  await page.goto('/');
  await candlesDrawn(page);
  const firstMs = Date.now() - started;

  await pickTimeframe(page, '1h');
  await candlesDrawn(page);
  await pickTimeframe(page, '4h');
  await candlesDrawn(page);
  const back = Date.now();
  await pickTimeframe(page, '1h');
  await candlesDrawn(page);
  const revisitMs = Date.now() - back;

  console.log(
    `chart-load-perf first_candles_ms=${firstMs} revisit_ms=${revisitMs}`
  );
  expect(firstMs).toBeLessThan(20_000);
  expect(revisitMs).toBeLessThan(5_000);
});
