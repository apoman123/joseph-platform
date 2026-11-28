/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Browser coverage of the DEMO heatmap tab: sector-grouped
 * stocks, the hover card, legend toggles, zooming, display settings, the
 * crypto map, opening a chart, persistence across a reload and an axe
 * accessibility scan. Set `HEATMAP_SCREENSHOT_DIR` to also save
 * screenshots of the views.
 */

import AxeBuilder from '@axe-core/playwright';
import {expect, test, type Page} from '@playwright/test';
import {openTerminal} from './support';

/** Directory for optional screenshots, or undefined. */
const SHOTS = process.env.HEATMAP_SCREENSHOT_DIR;

/**
 * Saves a screenshot when `HEATMAP_SCREENSHOT_DIR` is set.
 * @param page Page.
 * @param name File name without extension.
 */
async function shot(page: Page, name: string): Promise<void> {
  if (SHOTS) await page.screenshot({path: `${SHOTS}/heatmap-${name}.png`});
}

/**
 * Opens a new heatmap tab.
 * @param page Page.
 */
async function openHeatmap(page: Page): Promise<void> {
  await page.getByRole('button', {name: 'Add chart tab'}).click();
  await page.getByRole('button', {name: 'Heatmap', exact: true}).click();
  await expect(
    page.getByRole('region', {name: 'Heatmap'}).getByRole('button', {
      name: /^ACME\b.*Acme/,
    })
  ).toBeVisible();
}

test('DEMO stock heatmap: sectors, hover card, legend, zoom and settings', async ({
  page,
}) => {
  await openTerminal(page);
  await openHeatmap(page);
  const heatmap = page.getByRole('region', {name: 'Heatmap'});
  await expect(heatmap.getByText('DEMO', {exact: true})).toBeVisible();
  await expect(heatmap.getByText(/DEMO synthetic data/)).toBeVisible();
  const map = heatmap.getByRole('group', {name: 'S&P 500 tiles'});
  const tiles = map.getByRole('button', {name: /,/});
  expect(await tiles.count()).toBeGreaterThan(50);

  // Tiles stay inside the map and never overlap.
  const area = await map.boundingBox();
  const boxes = await tiles.evaluateAll(nodes =>
    nodes.map(node => {
      const rect = node.getBoundingClientRect();
      return {x: rect.x, y: rect.y, w: rect.width, h: rect.height};
    })
  );
  for (const box of boxes) {
    expect(box.x).toBeGreaterThanOrEqual((area?.x ?? 0) - 0.5);
    expect(box.x + box.w).toBeLessThanOrEqual(
      (area?.x ?? 0) + (area?.width ?? 0) + 0.5
    );
  }
  for (let i = 0; i < boxes.length; i += 1) {
    for (let j = i + 1; j < boxes.length; j += 1) {
      const a = boxes[i];
      const b = boxes[j];
      if (!a || !b) continue;
      const overlapX = Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x);
      const overlapY = Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y);
      expect(overlapX <= 0.5 || overlapY <= 0.5).toBe(true);
    }
  }

  // The hover card shows the sector › industry and the industry peers.
  await map.getByRole('button', {name: /\bORBQ\b/}).hover();
  const card = heatmap.getByRole('tooltip');
  await expect(card).toContainText('Technology › Semiconductors');
  await expect(card).toContainText('QLXD');
  await expect(card).toContainText('Market cap');
  await shot(page, 'stocks');

  const results = await new AxeBuilder({page})
    .include('[aria-label="Heatmap"]')
    .analyze();
  expect(results.violations).toEqual([]);

  // A legend segment hides its tiles and comes back independently.
  const legend = heatmap.getByRole('group', {name: /Colour legend/});
  const before = await tiles.count();
  const chips = legend.getByRole('button');
  await chips.nth(3).click();
  await expect(chips.nth(3)).toHaveAttribute('aria-pressed', 'false');
  expect(await tiles.count()).toBeLessThan(before);
  await chips.nth(3).click();
  await expect(tiles).toHaveCount(before);

  // Zoom into a sector and back.
  await map.getByRole('button', {name: 'Technology', exact: true}).click();
  const crumbs = heatmap.getByRole('navigation', {name: 'Heatmap zoom'});
  await expect(crumbs).toContainText('Technology');
  await expect(map.getByRole('button', {name: /\bBZRX\b/})).toHaveCount(0);
  await heatmap.getByRole('button', {name: 'Display settings'}).click();
  await heatmap.getByRole('radio', {name: 'Symbol description'}).check();
  await heatmap.getByRole('checkbox', {name: /Logo/}).check();
  await expect(
    map.getByRole('button', {name: /\bORBQ\b/}).getByText('Orbitron', {
      exact: false,
    })
  ).toBeVisible();
  await shot(page, 'zoomed-settings');
  await page.keyboard.press('Escape');
  await crumbs.getByRole('button', {name: 'All sectors'}).click();
  await expect(map.getByRole('button', {name: /\bBZRX\b/})).toHaveCount(1);

  // Settings persist with the tab.
  await heatmap.getByLabel('Color by').selectOption('perf_1w');
  await page.waitForTimeout(500);
  await page.reload();
  await expect(
    page.getByRole('region', {name: 'Heatmap'}).getByLabel('Color by')
  ).toHaveValue('perf_1w');
});

test('DEMO crypto heatmap opens a chart and offers the watchlist', async ({
  page,
}) => {
  await openTerminal(page);
  await openHeatmap(page);
  const heatmap = page.getByRole('region', {name: 'Heatmap'});
  await heatmap.getByLabel('Data set').selectOption('crypto_all');
  const map = heatmap.getByRole('group', {name: 'All coins tiles'});
  const btc = map.getByRole('button', {name: /^BTC\b.*Bitcoin/});
  await expect(btc).toBeVisible();
  await expect(heatmap.getByLabel('Group by')).toHaveCount(0);
  await expect(
    heatmap.getByRole('option', {name: 'Change 4h, % — unavailable'})
  ).toBeDisabled();
  await map.getByRole('button', {name: /\bZNTH\b/}).hover();
  await expect(heatmap.getByRole('tooltip')).toContainText(
    'DEMO: this fictional symbol has no chart'
  );
  await shot(page, 'crypto');
  await btc.click({button: 'right'});
  const menu = heatmap.getByRole('menu', {name: 'BTC actions'});
  await menu.getByRole('menuitem', {name: 'Add to watchlist'}).click();
  await expect(
    page.getByText(/already in the watchlist|added to the watchlist/)
  ).toBeVisible();
  await btc.click();
  const tabs = page.getByRole('tablist', {name: 'Chart tabs'});
  await expect(tabs.getByRole('tab', {selected: true})).toContainText('BTCUSD');
});
