/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Browser tests of the chart stack in DEMO mode: provenance,
 * instrument and timeframe switching, drawings with undo/redo and
 * persistence, the four-chart layout, the light theme, the command palette,
 * accessibility (axe) and security headers. Some tests also write the
 * documentation screenshots.
 */

import AxeBuilder from '@axe-core/playwright';
import {expect, test} from '@playwright/test';
import {
  BTC_DRAWINGS_KEY,
  dragOnChart,
  openTerminal,
  savedTools,
} from './support';

/** Folder of the documentation screenshots, relative to `apps/web`. */
const SHOTS = 'e2e/screenshots';

test('loads in DEMO mode with unmistakable provenance', async ({page}) => {
  await openTerminal(page);
  await expect(
    page.locator('.ribbon[data-feed-state="DEMO"]').first()
  ).toBeVisible();
  await expect(
    page.getByTestId('chart-pane-chart-1').getByTestId('provenance-badge')
  ).toContainText('Demo data');
  await expect(
    page.getByText('Demo mode: synthetic prices, simulated paper broker')
  ).toBeVisible();
  await expect(page.getByTestId('order-mode')).toHaveText('Paper trading');
  // Let fonts, canvas paints and transitions settle before the screenshot.
  await page.waitForTimeout(800);
  await page.screenshot({path: `${SHOTS}/01-terminal-demo-dark.png`});
});

test('switches instrument and timeframe on the active chart', async ({
  page,
}) => {
  await openTerminal(page);
  await page.getByTestId('instrument-selector').click();
  await page.getByRole('option', {name: /ETHUSD/}).click();
  const pane = page.getByTestId('chart-pane-chart-1');
  await expect(pane.locator('.pane__symbol')).toHaveText('ETHUSD');
  await page
    .getByRole('navigation', {name: 'Timeframe'})
    .getByRole('button', {name: '1h', exact: true})
    .click();
  await expect(pane.locator('.pane__chip').nth(1)).toHaveText('1h');
  await expect(page.getByText('Loading candles…')).toHaveCount(0);
});

test('draws a trendline and rectangle that survive a reload, with undo and redo', async ({
  page,
}) => {
  await openTerminal(page);
  await page.getByTestId('tool-group-lines').click();
  await page.getByTestId('tool-option-reference.trend-line').click();
  await dragOnChart(page, [0.3, 0.6], [0.6, 0.35]);
  await page.getByTestId('tool-group-shapes').click();
  await page.getByTestId('tool-option-reference.rectangle').click();
  await dragOnChart(page, [0.65, 0.3], [0.8, 0.45]);
  await expect
    .poll(() => savedTools(page))
    .toEqual(['reference.trend-line', 'reference.rectangle']);
  await page.screenshot({path: `${SHOTS}/02-drawings.png`});

  await page.getByTestId('undo').click();
  await expect.poll(() => savedTools(page)).toEqual(['reference.trend-line']);
  await page.getByTestId('redo').click();
  await expect
    .poll(() => savedTools(page))
    .toEqual(['reference.trend-line', 'reference.rectangle']);

  await page.reload();
  await expect(page.locator('.terminal[data-ready="true"]')).toBeVisible();
  await expect
    .poll(() => savedTools(page))
    .toEqual(['reference.trend-line', 'reference.rectangle']);
  // Drawings persist; their undo history does not.
  await expect(page.getByTestId('undo')).toBeDisabled();
  const raw = await page.evaluate(
    key => localStorage.getItem(key),
    BTC_DRAWINGS_KEY
  );
  // The drawing store's saved shape. Anchor prices must be plain decimal
  // strings, never floats in exponent notation.
  const anchors =
    (
      JSON.parse(raw ?? '{}') as {
        drawings: Array<{anchors: Array<{price: string}>}>;
      }
    ).drawings[0]?.anchors ?? [];
  expect(anchors.every(a => /^\d+(\.\d+)?$/.test(a.price))).toBe(true);
});

test('shows four synced charts and restores the layout after reload', async ({
  page,
}) => {
  await openTerminal(page);
  await page.getByTestId('layout-menu').click();
  await page.getByTestId('layout-4').click();
  await expect(page.locator('[data-testid^="chart-pane-"]')).toHaveCount(4);
  await page.getByTestId('layout-menu').click();
  await expect(page.getByTestId('sync-crosshair')).toBeChecked();
  await page.keyboard.press('Escape');
  // Hover the first chart so the screenshot shows the crosshair synced on
  // all four, then let the panes repaint.
  const box = await page.getByTestId('chart-canvas').first().boundingBox();
  if (box) {
    await page.mouse.move(box.x + box.width * 0.5, box.y + box.height * 0.5);
  }
  await page.waitForTimeout(500);
  await page.screenshot({path: `${SHOTS}/03-four-charts.png`});
  await page.reload();
  await expect(page.locator('[data-testid^="chart-pane-"]')).toHaveCount(4);
});

test('toggles the daylight theme', async ({page}) => {
  await openTerminal(page);
  await page.getByTestId('theme-toggle').click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  await expect(
    page.locator('[data-testid^="chart-pane-"] canvas').first()
  ).toBeVisible();
  // The charts repaint in the new theme's colours; let them settle.
  await page.waitForTimeout(800);
  await page.screenshot({path: `${SHOTS}/04-terminal-light.png`});
  await page.getByTestId('theme-toggle').click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
});

test('runs commands from the palette', async ({page}) => {
  await openTerminal(page);
  await page.keyboard.press('Control+k');
  const palette = page.getByRole('dialog', {name: 'Command palette'});
  await expect(palette).toBeVisible();
  await palette.getByRole('textbox').fill('four charts');
  await page.screenshot({path: `${SHOTS}/05-command-palette.png`});
  await page.keyboard.press('Enter');
  await expect(palette).toHaveCount(0);
  await expect(page.locator('[data-testid^="chart-pane-"]')).toHaveCount(4);
});

test('has no serious or critical accessibility violations', async ({page}) => {
  await openTerminal(page);
  const results = await new AxeBuilder({page}).analyze();
  const serious = results.violations.filter(
    v => v.impact === 'serious' || v.impact === 'critical'
  );
  expect(
    serious.map(
      v =>
        `${v.id}: ${v.nodes
          .map(n => n.target.join(' '))
          .slice(0, 3)
          .join(', ')}`
    )
  ).toEqual([]);
});

test('sends strict security headers', async ({request}) => {
  const response = await request.get('/');
  const csp = response.headers()['content-security-policy'] ?? '';
  expect(csp).toContain("frame-ancestors 'none'");
  expect(csp).not.toContain('unsafe-eval');
  expect(response.headers()['x-content-type-options']).toBe('nosniff');
});
