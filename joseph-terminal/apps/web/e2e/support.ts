/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Shared helpers for the terminal's browser tests against the
 * DEMO build: opening the terminal on clean storage, pointer input on the
 * chart canvas (which has no DOM content to click), and reading what the
 * drawing store saved.
 */

import {expect, type Locator, type Page} from '@playwright/test';

/** Storage key of the DEMO BTCUSD drawings in the default workspace. */
export const BTC_DRAWINGS_KEY = 'joseph.drawings.v1.default.mock:perp:BTCUSD';

/**
 * Opens the terminal and waits until it can be used: every feature is
 * activated (`data-ready`), the first chart has mounted its canvas, and the
 * first candles have loaded.
 * @param page Page.
 * @param options `clear` wipes local storage before the first load of the
 *     test; it is on when `options` is omitted.
 */
export async function openTerminal(
  page: Page,
  options: {clear?: boolean} = {clear: true}
): Promise<void> {
  if (options.clear) {
    // Init scripts run on every navigation; the session flag limits the
    // wipe to the first load, so a reload inside the test keeps its state.
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
 * Returns the element hosting the first chart's canvases.
 * @param page Page.
 * @return Locator.
 */
export function firstCanvas(page: Page): Locator {
  return page.getByTestId('chart-canvas').first();
}

/**
 * Drags across the first chart between relative positions.
 * @param page Page.
 * @param from Start as `[x, y]` fractions of the canvas box.
 * @param to End as `[x, y]` fractions of the canvas box.
 * @throws `Error` when the canvas is not visible.
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
  // Pointer moves reach the chart only through `mouse.move`; stepping via
  // the midpoint gives the drawing controller moves past its drag slop
  // (and freehand samples) between press and release.
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
 * Reads the tool ids of the saved drawings from local storage.
 * @param page Page.
 * @param key Storage key; the DEMO BTCUSD drawings by default.
 * @return Tool ids in saved order; empty when nothing is saved.
 */
export async function savedTools(
  page: Page,
  key = BTC_DRAWINGS_KEY
): Promise<string[]> {
  return page.evaluate(storageKey => {
    const raw = localStorage.getItem(storageKey);
    if (!raw) return [];
    // The drawing store's saved shape; only the tool ids are read.
    return (
      JSON.parse(raw) as {drawings: Array<{tool_id: string}>}
    ).drawings.map(d => d.tool_id);
  }, key);
}

/**
 * Clicks at a relative position of the first chart canvas.
 * @param page Page.
 * @param x Fraction of the width.
 * @param y Fraction of the height.
 * @throws `Error` when the canvas is not visible.
 */
export async function clickChart(
  page: Page,
  x: number,
  y: number
): Promise<void> {
  const box = await firstCanvas(page).boundingBox();
  if (!box) throw new Error('chart canvas not visible');
  await page.mouse.click(box.x + box.width * x, box.y + box.height * y);
}

/**
 * Hovers a relative position of the first chart canvas, in a few steps so
 * the chart sees pointer moves (crosshair, previews).
 * @param page Page.
 * @param x Fraction of the width.
 * @param y Fraction of the height.
 * @throws `Error` when the canvas is not visible.
 */
export async function hoverChart(
  page: Page,
  x: number,
  y: number
): Promise<void> {
  const box = await firstCanvas(page).boundingBox();
  if (!box) throw new Error('chart canvas not visible');
  await page.mouse.move(box.x + box.width * x, box.y + box.height * y, {
    steps: 3,
  });
}

/**
 * Arms a tool from its toolbar group flyout.
 * @param page Page.
 * @param group Tool group.
 * @param toolId Tool id.
 */
export async function armTool(
  page: Page,
  group: string,
  toolId: string
): Promise<void> {
  await page.getByTestId(`tool-group-${group}`).click();
  const selected = ['zone-box', 'anchored-vwap', 'regression-channel'].includes(
    toolId
  )
    ? toolId
    : `reference.${toolId}`;
  await page.getByTestId(`tool-option-${selected}`).click();
}

/**
 * Turns on the data window from the Layers menu, then closes the menu.
 * @param page Page.
 */
export async function showDataWindow(page: Page): Promise<void> {
  await page.getByTestId('indicators-menu').click();
  await page.getByTestId('data-window-toggle').check();
  await page.keyboard.press('Escape');
}

/**
 * Returns the detail text of a drawing in the object list.
 * @param page Page.
 * @param toolId Tool id.
 * @return Locator of the detail.
 */
export function objectDetail(page: Page, toolId: string): Locator {
  return page
    .getByRole('list', {name: 'Drawings, top first'})
    .getByTestId(
      `object-${['anchored-vwap', 'regression-channel', 'zone-box'].includes(toolId) ? toolId : `reference.${toolId}`}`
    )
    .getByTestId('object-detail');
}

/**
 * Picks a timeframe from the "More" menu and waits for its candles.
 * @param page Page.
 * @param timeframe Timeframe id.
 */
export async function pickTimeframe(
  page: Page,
  timeframe: string
): Promise<void> {
  // The class, not the text: the button shows "More" or, when the current
  // timeframe is not a favorite, that timeframe.
  await page
    .getByRole('navigation', {name: 'Timeframe'})
    .locator('.timeframes__more')
    .click();
  await page.getByTestId(`timeframe-${timeframe}`).click();
  await expect(page.getByText('Loading candles…')).toHaveCount(0);
}
