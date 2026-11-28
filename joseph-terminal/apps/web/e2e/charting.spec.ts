/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Browser tests of the TradingView-style chart workflows in
 * DEMO mode: volume, Delta % and study panes, indicator layers and
 * settings, comparison, session breaks, data window, bar replay, the new
 * drawing tools with the style editor and object list, screenshots,
 * templates, alert triage and resizable docks.
 */

import {expect, test, type Locator, type Page} from '@playwright/test';
import {openPanel} from './trading-helpers';
import {
  BTC_DRAWINGS_KEY,
  clickChart,
  firstCanvas,
  openTerminal,
  savedTools,
} from './support';

/** Folder of the documentation screenshots, relative to `apps/web`. */
const SHOTS = 'e2e/screenshots';

/**
 * Counts the canvases inside the first chart. Lightweight Charts draws each
 * pane on its own set of canvases, so the count tracks the pane count.
 * @param page Page.
 * @return Canvas count.
 */
async function canvasCount(page: Page): Promise<number> {
  return firstCanvas(page).locator('canvas').count();
}

/**
 * Opens the Indicators menu and waits for its dialog.
 * @param page Page.
 */
async function openLayers(page: Page): Promise<void> {
  await page.getByTestId('indicators-menu').click();
  await expect(page.getByRole('dialog', {name: 'Indicators'})).toBeVisible();
}

test('shows volume, Delta % and study panes and the data window', async ({
  page,
}) => {
  await openTerminal(page);
  // Price, volume and Delta % panes each own canvases. The Delta % pane
  // appears only after its study run answers, which is slow under load.
  await expect
    .poll(() => canvasCount(page), {timeout: 30_000})
    .toBeGreaterThanOrEqual(9);
  const credit = page.getByRole('link', {name: 'TradeX · Saifullah Shafin'});
  await expect(credit).toBeVisible();
  await expect(credit).toHaveAttribute(
    'href',
    'https://github.com/saifullahshafin/open-source-tradingview'
  );
  const withPanes = await canvasCount(page);
  await openLayers(page);
  const volume = page.getByRole('checkbox', {name: 'Volume', exact: true});
  await volume.uncheck();
  await expect.poll(() => canvasCount(page)).toBeLessThan(withPanes);
  await volume.check();
  await expect.poll(() => canvasCount(page)).toBe(withPanes);
  const delta = page.getByTestId('layer-toggle-builtin:volume_delta');
  await delta.uncheck();
  await expect.poll(() => canvasCount(page)).toBeLessThan(withPanes);
  await delta.check();
  await expect.poll(() => canvasCount(page)).toBe(withPanes);
  // The studies come from the engine's script catalog; RSI draws in its
  // own pane.
  await page.getByTestId('add-study-builtin:rsi').click();
  await expect(page.getByTestId('layer-toggle-builtin:rsi')).toBeChecked();
  await expect.poll(() => canvasCount(page)).toBeGreaterThan(withPanes);
  await page.getByTestId('data-window-toggle').check();
  await page.getByTestId('session-separators-toggle').check();
  await page.keyboard.press('Escape');
  const box = await firstCanvas(page).boundingBox();
  if (!box) throw new Error('no chart');
  // The data window shows the bar under the pointer.
  await page.mouse.move(box.x + box.width * 0.7, box.y + box.height * 0.3);
  const data = page.getByTestId('data-window');
  await expect(data).toContainText('Open');
  await expect(page.getByTestId('bar-volume')).toContainText(/\d/);
  const selectedVolume = await data
    .getByText('Volume', {exact: true})
    .locator('..')
    .locator('dd')
    .innerText();
  await expect(page.getByTestId('bar-volume')).toContainText(selectedVolume);
  await expect(data).toContainText(/Delta %\s*[−+-]?\d/);
  // Let the canvases repaint before the screenshot.
  await page.waitForTimeout(400);
  await page.screenshot({path: `${SHOTS}/12-panes-studies-data-window.png`});
});

test('zooms prices when scrolling over the right price scale', async ({
  page,
}) => {
  await openTerminal(page);
  const chart = firstCanvas(page);
  const chartBox = await chart.boundingBox();
  if (!chartBox) throw new Error('chart is not visible');
  let axis: Locator | null = null;
  const canvases = chart.locator('canvas');
  for (let index = 0; index < (await canvases.count()); index += 1) {
    const candidate = canvases.nth(index);
    const box = await candidate.boundingBox();
    if (
      box &&
      box.width < 150 &&
      box.height > 100 &&
      box.x + box.width >= chartBox.x + chartBox.width - 2
    ) {
      axis = candidate;
      break;
    }
  }
  if (!axis) throw new Error('right price scale canvas is not visible');
  const axisBox = await axis.boundingBox();
  if (!axisBox) throw new Error('right price scale canvas has no bounds');
  await page.mouse.move(
    axisBox.x + axisBox.width / 2,
    axisBox.y + axisBox.height / 2
  );
  const before = await axis.screenshot();
  await page.mouse.wheel(0, -400);
  await expect
    .poll(async () => (await axis.screenshot()).equals(before))
    .toBe(false);
});

test('edits study inputs from the settings dialog', async ({page}) => {
  await openTerminal(page);
  await openLayers(page);
  await page.getByTestId('study-settings-builtin:volume_delta').click();
  const dialog = page.getByTestId('indicator-settings');
  await expect(dialog.getByLabel('Minimum coverage')).toHaveValue('0.8');
  await dialog.getByLabel('Minimum coverage').fill('1.5');
  await dialog.getByTestId('indicator-settings-save').click();
  await expect(dialog.getByText('At most 1')).toBeVisible();
  await dialog.getByLabel('Minimum coverage').fill('0.9');
  await page.screenshot({path: `${SHOTS}/13-indicator-settings.png`});
  await dialog.getByTestId('indicator-settings-save').click();
  await expect(dialog).toHaveCount(0);
  // The workspace autosaves after a short debounce.
  await expect
    .poll(() =>
      page.evaluate(
        () => localStorage.getItem('joseph.workspace.v1.default') ?? ''
      )
    )
    .toContain('"min_coverage":0.9');
});

test('compares another symbol on its own percentage axis', async ({page}) => {
  await openTerminal(page);
  await page.getByTestId('compare-menu').click();
  await page.getByLabel('Add a symbol').fill('ETH');
  await page.getByTestId('compare-add-mock:perp:ETHUSD').click();
  await page.keyboard.press('Escape');
  await expect(page.getByTestId('compare-chip-mock:perp:ETHUSD')).toBeVisible();
  // The comparison series loads its own candles; let it draw first.
  await page.waitForTimeout(500);
  await page.screenshot({path: `${SHOTS}/14-compare.png`});
  await page.getByRole('button', {name: 'Stop comparing ETHUSD'}).click();
  await expect(page.getByTestId('compare-chip-mock:perp:ETHUSD')).toHaveCount(
    0
  );
});

test('replays bars point-in-time with trading disabled', async ({page}) => {
  await openTerminal(page);
  await page.getByTestId('replay-toggle').click();
  const bar = page.getByTestId('replay-bar');
  await expect(bar).toHaveAttribute('data-phase', 'select');
  await clickChart(page, 0.5, 0.4);
  await expect(bar).toHaveAttribute('data-phase', 'paused');
  await expect(
    page.locator('.ribbon[data-feed-state="REPLAY"]').first()
  ).toBeVisible();
  const before = await page.getByTestId('replay-pos').textContent();
  await page.getByTestId('replay-step').click();
  await expect(page.getByTestId('replay-pos')).not.toHaveText(before ?? '');
  await page.getByTestId('replay-speed').click();
  await page.getByTestId('replay-play').click();
  await expect(bar).toHaveAttribute('data-phase', 'playing');
  // Playback advances on a timer; at the raised speed a bar passes within
  // a few seconds.
  const playing = await page.getByTestId('replay-pos').textContent();
  await expect(page.getByTestId('replay-pos')).not.toHaveText(playing ?? '', {
    timeout: 5000,
  });
  await page.getByTestId('replay-play').click();
  await openPanel(page, 'Order ticket');
  const ticket = page.getByTestId('order-ticket');
  await expect(ticket.getByText(/bar replay/)).toBeVisible();
  await expect(ticket.getByTestId('confirm-order')).toBeDisabled();
  await page.screenshot({path: `${SHOTS}/15-replay.png`});
  await page.getByTestId('replay-exit').click();
  await expect(bar).toHaveCount(0);
  await expect(
    page.locator('.ribbon[data-feed-state="DEMO"]').first()
  ).toBeVisible();
});

test('draws polyline, brush and zone box, styles them and manages the object list', async ({
  page,
}) => {
  await openTerminal(page);
  await page.getByTestId('tool-group-shapes').click();
  await page.getByTestId('tool-option-reference.polyline').click();
  await clickChart(page, 0.2, 0.5);
  await clickChart(page, 0.3, 0.3);
  // A second click on the last vertex finishes the polyline.
  await clickChart(page, 0.4, 0.45);
  await clickChart(page, 0.4, 0.45);
  await expect.poll(() => savedTools(page)).toEqual(['reference.polyline']);

  await page.getByTestId('tool-group-annotation').click();
  await page.getByTestId('tool-option-reference.brush').click();
  const box = await firstCanvas(page).boundingBox();
  if (!box) throw new Error('no chart');
  await page.mouse.move(box.x + box.width * 0.5, box.y + box.height * 0.5);
  await page.mouse.down();
  // A zigzag of separate moves: the brush samples a point only after the
  // pointer has travelled a few pixels.
  for (let i = 1; i <= 12; i += 1) {
    await page.mouse.move(
      box.x + box.width * (0.5 + i * 0.01),
      box.y + box.height * (0.5 - (i % 4) * 0.02)
    );
  }
  await page.mouse.up();
  await expect
    .poll(() => savedTools(page))
    .toEqual(['reference.polyline', 'reference.brush']);

  await page.getByTestId('tool-group-shapes').click();
  await page.getByTestId('tool-option-zone-box').click();
  await page.mouse.move(box.x + box.width * 0.65, box.y + box.height * 0.25);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width * 0.72, box.y + box.height * 0.32, {
    steps: 4,
  });
  await page.mouse.up();
  await expect
    .poll(() => savedTools(page))
    .toEqual(['reference.polyline', 'reference.brush', 'zone-box']);

  const style = page.getByTestId('drawing-style-bar');
  await expect(style).toBeVisible();
  await style.getByRole('button', {name: 'Color #5ab0f0'}).click();
  await style.getByLabel('Drawing text').fill('Weekly zone');
  await style.getByLabel('Drawing text').press('Enter');
  // Style and text are saved with the drawing.
  await expect
    .poll(() =>
      page.evaluate(key => {
        const docs =
          JSON.parse(localStorage.getItem(key) ?? '{}').drawings ?? [];
        const zone = docs.find(
          (d: {tool_id: string}) => d.tool_id === 'zone-box'
        );
        return zone ? `${zone.style.color}|${zone.text}` : '';
      }, BTC_DRAWINGS_KEY)
    )
    .toBe('#5ab0f0|Weekly zone');
  await page.screenshot({path: `${SHOTS}/16-drawings-style.png`});

  await openPanel(page, 'Objects');
  const list = page.getByRole('list', {name: 'Drawings, top first'});
  await expect(list.getByRole('listitem')).toHaveCount(3);
  await list
    .getByTestId('object-reference.brush')
    .getByRole('button', {name: 'Lock Brush'})
    .click();
  await expect(
    list
      .getByTestId('object-reference.brush')
      .getByRole('button', {name: 'Delete Brush'})
  ).toBeDisabled();
  await list
    .getByTestId('object-reference.polyline')
    .getByRole('button', {name: 'Hide Polyline'})
    .click();
  await page.screenshot({path: `${SHOTS}/17-object-list.png`});
  await list
    .getByTestId('object-zone-box')
    .getByRole('button', {name: 'Delete Zone box'})
    .click();
  await expect
    .poll(() => savedTools(page))
    .toEqual(['reference.polyline', 'reference.brush']);
  await page.keyboard.press('Control+z');
  await expect
    .poll(() => savedTools(page))
    .toEqual(['reference.polyline', 'reference.brush', 'zone-box']);
});

test('downloads a screenshot and applies a saved chart template', async ({
  page,
}) => {
  await openTerminal(page);
  const download = page.waitForEvent('download');
  await page.getByTestId('screenshot').click();
  expect((await download).suggestedFilename()).toMatch(
    /^joseph-BTCUSD-5m-\d{8}-\d{4}\.png$/
  );

  await page.locator('#chart-type').selectOption('heikin_ashi');
  await page.getByTestId('templates-menu').click();
  await page.getByLabel('Template name').fill('HA swing');
  await page.getByTestId('template-save').click();
  await page.keyboard.press('Escape');
  await page.locator('#chart-type').selectOption('line');
  await page.getByTestId('templates-menu').click();
  await page.getByTestId('template-apply-HA swing').click();
  await expect(page.locator('#chart-type')).toHaveValue('heikin_ashi');
});

test('sorts, snoozes and dismisses alerts', async ({page}) => {
  await openTerminal(page);
  await openPanel(page, 'Alerts');
  const first = page.locator('[data-testid^="signal-demo-signal-"]').first();
  await expect(first).toBeVisible();
  await page.getByTestId('alerts-sort').selectOption('rr');
  const count = await page
    .locator('[data-testid^="signal-demo-signal-"]')
    .count();
  const target = page.locator('[data-testid^="signal-demo-signal-"]').first();
  const id = await target.getAttribute('data-testid');
  // Snooze one signal for 60 minutes and dismiss another; "show hidden"
  // brings both back.
  await target.getByTestId('signal-snooze').selectOption('60');
  await expect(page.getByTestId(id ?? '')).toHaveCount(0);
  const next = page.locator('[data-testid^="signal-demo-signal-"]').first();
  await next.getByTestId('signal-dismiss').click();
  await expect(
    page.locator('[data-testid^="signal-demo-signal-"]')
  ).toHaveCount(count - 2);
  await page.getByTestId('alerts-show-hidden').check();
  await expect(
    page.locator('[data-testid^="signal-demo-signal-"]')
  ).toHaveCount(count);
  await page.screenshot({path: `${SHOTS}/18-alerts-triage.png`});
});

test('resizes the side dock and chart columns, and keeps the sizes', async ({
  page,
}) => {
  await openTerminal(page);
  const side = page.getByRole('region', {name: 'Side panel'});
  const before = (await side.boundingBox())?.width ?? 0;
  // The side dock sits on the right, so dragging its handle left widens it.
  const handle = page.getByTestId('resize-side-panel');
  const hb = await handle.boundingBox();
  if (!hb) throw new Error('no handle');
  await page.mouse.move(hb.x + hb.width / 2, hb.y + 200);
  await page.mouse.down();
  await page.mouse.move(hb.x - 80, hb.y + 200, {steps: 5});
  await page.mouse.up();
  await expect
    .poll(async () => (await side.boundingBox())?.width ?? 0)
    .toBeGreaterThan(before + 60);

  await page.getByTestId('layout-menu').click();
  await page.getByTestId('layout-2h').click();
  const left = page.getByTestId('chart-pane-chart-1');
  const leftBefore = (await left.boundingBox())?.width ?? 0;
  const splitter = page.getByTestId('grid-splitter-col-0');
  const sb = await splitter.boundingBox();
  if (!sb) throw new Error('no splitter');
  await page.mouse.move(sb.x + sb.width / 2, sb.y + 100);
  await page.mouse.down();
  await page.mouse.move(sb.x + 150, sb.y + 100, {steps: 5});
  await page.mouse.up();
  await expect
    .poll(async () => (await left.boundingBox())?.width ?? 0)
    .toBeGreaterThan(leftBefore + 100);
  await page.screenshot({path: `${SHOTS}/19-resized-layout.png`});
  await page.reload();
  await expect(page.locator('.terminal[data-ready="true"]')).toBeVisible();
  await expect
    .poll(
      async () =>
        (await page.getByTestId('chart-pane-chart-1').boundingBox())?.width ?? 0
    )
    .toBeGreaterThan(leftBefore + 100);
  await expect
    .poll(async () => (await side.boundingBox())?.width ?? 0)
    .toBeGreaterThan(before + 60);
});
