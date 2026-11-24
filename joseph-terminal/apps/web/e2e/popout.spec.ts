/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Browser tests of detachable panes in DEMO mode: a chart
 * popped out into its own window follows the main window and docks again
 * when the window closes; pop-outs keep running while the main window
 * reloads and are re-adopted without a click (changes made in them
 * meanwhile reach the reloaded main window); panes remembered by the layout
 * are offered as "Restore pop-outs" after the browser loses its windows.
 */

import {expect, test, type Page} from '@playwright/test';
import {openTerminal} from './support';
import {openPanel} from './trading-helpers';

/** Folder of the documentation screenshots, relative to `apps/web`. */
const SHOTS = 'e2e/screenshots';

/**
 * Waits until a pop-out window has activated its features (`data-ready`)
 * and its message link to the main window reports `linked`.
 * @param popup Pop-out page.
 */
async function expectLinked(popup: Page): Promise<void> {
  await popup.waitForLoadState('domcontentloaded');
  await expect(popup.getByTestId('popout')).toHaveAttribute(
    'data-ready',
    'true'
  );
  await expect(popup.getByTestId('popout-link')).toHaveAttribute(
    'data-state',
    'linked'
  );
}

/**
 * Records, in the main window, whether the restore banner or a dormant
 * placeholder ever appears, even for a moment that assertions polling
 * afterwards would miss. Registered as an init script, so it survives
 * reloads.
 * @param page Main page.
 */
async function watchRestoreOffers(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const seen: string[] = [];
    // A test-only global that `restoreOffers` reads back.
    (window as unknown as {restoreOffers: string[]}).restoreOffers = seen;
    new MutationObserver(() => {
      if (document.querySelector('[data-testid="detached-banner"]')) {
        seen.push('banner');
      }
      if (document.querySelector('.detached[data-status="dormant"]')) {
        seen.push('dormant');
      }
    }).observe(document, {subtree: true, childList: true, attributes: true});
  });
}

/**
 * Returns the restore offers recorded since the main page last loaded.
 * @param page Main page.
 * @return `banner` and `dormant` entries, one per observed mutation; empty
 *     when none appeared.
 */
async function restoreOffers(page: Page): Promise<string[]> {
  return page.evaluate(
    () => (window as unknown as {restoreOffers?: string[]}).restoreOffers ?? []
  );
}

/**
 * Returns the timeframe of chart-1 in the main window's saved workspace.
 * @param page Main page.
 * @return Timeframe, or null when nothing is saved.
 */
async function savedTimeframe(page: Page): Promise<string | null> {
  return page.evaluate(() => {
    const raw = localStorage.getItem('joseph.workspace.v1.default');
    const doc = raw
      ? (JSON.parse(raw) as {
          charts?: Array<{chart_id: string; timeframe: string}>;
        })
      : null;
    return doc?.charts?.find(c => c.chart_id === 'chart-1')?.timeframe ?? null;
  });
}

/**
 * Picks a timeframe in a pop-out's window bar.
 * @param popup Pop-out page.
 * @param timeframe Timeframe id.
 */
async function pickPopoutTimeframe(
  popup: Page,
  timeframe: string
): Promise<void> {
  await popup
    .getByRole('navigation', {name: 'Timeframe'})
    .locator('.timeframes__more')
    .click();
  await popup.getByTestId(`timeframe-${timeframe}`).click();
}

test('pops a chart out, follows the main window and docks again on close', async ({
  page,
  context,
}) => {
  await openTerminal(page);
  // Listen for the new window before clicking, so its event is not missed.
  const [popup] = await Promise.all([
    context.waitForEvent('page'),
    page.getByTestId('popout-chart-1').click(),
  ]);
  await expectLinked(popup);
  expect(new URL(popup.url()).pathname).toBe('/popout');
  const popped = popup.getByTestId('chart-pane-chart-1');
  await expect(popped.locator('canvas').first()).toBeVisible();
  await expect(popped.locator('.pane__symbol')).toHaveText('BTCUSD');

  // The main window keeps a placeholder in the slot.
  const placeholder = page.getByTestId('detached-chart:chart-1');
  await expect(placeholder).toContainText('Open in another window');
  await expect(placeholder.getByTestId('detached-focus')).toBeVisible();
  await expect(placeholder.getByTestId('detached-bring-back')).toBeVisible();
  await expect(page.getByTestId('chart-pane-chart-1')).toHaveCount(0);

  // A symbol change in the main window reaches the pop-out.
  await page.getByTestId('instrument-selector').click();
  await page.getByRole('option', {name: /ETHUSD/}).click();
  await expect(popped.locator('.pane__symbol')).toHaveText('ETHUSD');
  // Theme changes follow too.
  await page.getByTestId('theme-toggle').click();
  await expect(popup.locator('html')).toHaveAttribute('data-theme', 'light');
  await page.getByTestId('theme-toggle').click();
  await expect(popup.locator('html')).toHaveAttribute('data-theme', 'dark');
  // Trading in the pop-out starts at Paper, like the main window.
  await expect(popup.getByTestId('order-mode')).toHaveText('Paper trading');
  // Let both windows repaint after the theme round trip.
  await page.waitForTimeout(400);
  await page.screenshot({path: `${SHOTS}/21-detached-placeholder.png`});
  await popup.screenshot({path: `${SHOTS}/22-popout-chart.png`});

  await popup.close();
  await expect(page.getByTestId('chart-pane-chart-1')).toBeVisible();
  await expect(page.getByTestId('detached-chart:chart-1')).toHaveCount(0);
  await expect(
    page.getByTestId('chart-pane-chart-1').locator('.pane__symbol')
  ).toHaveText('ETHUSD');
});

test('a pop-out survives a main-window reload and keeps its changes', async ({
  page,
  context,
}) => {
  await watchRestoreOffers(page);
  await openTerminal(page);
  const [popup] = await Promise.all([
    context.waitForEvent('page'),
    page.getByTestId('popout-chart-1').click(),
  ]);
  await expectLinked(popup);
  const popped = popup.getByTestId('chart-pane-chart-1');
  await expect(popped.locator('.pane__symbol')).toHaveText('BTCUSD');

  // Reload: the pop-out keeps running and the new main window adopts it.
  await page.reload();
  await expect(page.locator('.terminal[data-ready="true"]')).toBeVisible();
  const placeholder = page.getByTestId('detached-chart:chart-1');
  await expect(placeholder).toHaveAttribute('data-status', 'live');
  await expect(popup.getByTestId('popout-link')).toHaveAttribute(
    'data-state',
    'linked'
  );
  // Wait past the reload probe (`PROBE_MS`, 2.5 s): an unclaimed pane would
  // turn dormant and offer a restore at its end.
  await page.waitForTimeout(3_000);
  await expect(placeholder).toHaveAttribute('data-status', 'live');
  await expect(page.getByTestId('detached-banner')).toHaveCount(0);
  expect(await restoreOffers(page)).toEqual([]);
  await page.screenshot({path: `${SHOTS}/35-popout-after-reload.png`});

  // Both directions work with the reloaded main window.
  await page.getByTestId('instrument-selector').click();
  await page.getByRole('option', {name: /ETHUSD/}).click();
  await expect(popped.locator('.pane__symbol')).toHaveText('ETHUSD');
  await pickPopoutTimeframe(popup, '15m');
  await expect.poll(() => savedTimeframe(page)).toBe('15m');

  // Changes made while the main window is away are replayed to it. Leaving
  // for about:blank unloads the main window but keeps the pop-out open.
  await page.goto('about:blank');
  await expect(popup.getByTestId('popout-link')).toHaveAttribute(
    'data-state',
    'reconnecting'
  );
  await pickPopoutTimeframe(popup, '1h');
  await expect(popped.locator('.pane__chip').nth(1)).toHaveText('1h');
  // The timeframe change waits (the chart's own viewport update may follow
  // it a moment later, so count at least one).
  await expect(popup.getByTestId('popout-link')).toHaveAttribute(
    'data-pending',
    /^[1-9]\d*$/
  );
  await page.goto('/');
  await expect(page.locator('.terminal[data-ready="true"]')).toBeVisible();
  await expect(page.getByTestId('detached-chart:chart-1')).toHaveAttribute(
    'data-status',
    'live'
  );
  await expect(popup.getByTestId('popout-link')).toHaveAttribute(
    'data-pending',
    '0'
  );
  await expect.poll(() => savedTimeframe(page)).toBe('1h');
  await expect(popped.locator('.pane__chip').nth(1)).toHaveText('1h');
  expect(await restoreOffers(page)).toEqual([]);

  await popup.getByTestId('popout-bring-back').click();
  await expect(page.getByTestId('chart-pane-chart-1')).toBeVisible();
  await expect(
    page.getByTestId('chart-pane-chart-1').locator('.pane__chip').nth(1)
  ).toHaveText('1h');
});

test('brings a detached panel back and restores remembered windows after a browser restart', async ({
  page,
  context,
}) => {
  await openTerminal(page);
  await openPanel(page, 'Alerts');
  const [popup] = await Promise.all([
    context.waitForEvent('page'),
    page.getByTestId('popout-panel-alerts').click(),
  ]);
  await expectLinked(popup);
  await expect(
    popup.locator('[data-testid^="signal-demo-signal-"]').first()
  ).toBeVisible();
  await expect(page.getByTestId('detached-panel:alerts')).toBeVisible();

  // The browser loses every window while the layout remembers the panel.
  await page.goto('about:blank');
  await popup.close();
  await page.goto('/');
  await expect(page.locator('.terminal[data-ready="true"]')).toBeVisible();
  const banner = page.getByTestId('detached-banner');
  await expect(banner).toContainText('Alerts was open in another window.');
  await expect(banner.getByTestId('reopen-detached')).toHaveText(
    'Restore pop-outs'
  );
  await page.screenshot({path: `${SHOTS}/23-reopen-detached.png`});

  const [reopened] = await Promise.all([
    context.waitForEvent('page'),
    banner.getByTestId('reopen-detached').click(),
  ]);
  await expectLinked(reopened);
  await expect(banner).toHaveCount(0);
  await reopened.getByTestId('popout-bring-back').click();
  await expect(page.getByTestId('detached-panel:alerts')).toHaveCount(0);
  await openPanel(page, 'Alerts');
  await expect(
    page.locator('[data-testid^="signal-demo-signal-"]').first()
  ).toBeVisible();
});

test('restores several pop-outs with one action when pop-ups are allowed', async ({
  page,
  context,
}) => {
  await openTerminal(page);
  await openPanel(page, 'Alerts');
  const [alerts] = await Promise.all([
    context.waitForEvent('page'),
    page.getByTestId('popout-panel-alerts').click(),
  ]);
  await expectLinked(alerts);
  const [chart] = await Promise.all([
    context.waitForEvent('page'),
    page.getByTestId('popout-chart-1').click(),
  ]);
  await expectLinked(chart);

  // A browser restart: every window is gone, the layout remembers both.
  await page.goto('about:blank');
  await alerts.close();
  await chart.close();
  await page.goto('/');
  await expect(page.locator('.terminal[data-ready="true"]')).toBeVisible();
  const banner = page.getByTestId('detached-banner');
  await expect(banner).toContainText('2 panes were open in other windows');
  await expect(banner).toContainText('one window per click');
  await page.screenshot({path: `${SHOTS}/36-restore-popouts.png`});

  // The test browser allows pop-ups for the site, so one click restores all.
  const restored: Page[] = [];
  context.on('page', opened => restored.push(opened));
  await banner.getByTestId('reopen-detached').click();
  await expect.poll(() => restored.length).toBe(2);
  for (const opened of restored) {
    await expectLinked(opened);
  }
  await expect(banner).toHaveCount(0);
  await expect(page.getByTestId('detached-chart:chart-1')).toHaveAttribute(
    'data-status',
    'live'
  );

  // The command palette offers the same action.
  await page.keyboard.press('Control+k');
  const palette = page.getByRole('dialog', {name: 'Command palette'});
  await palette.getByRole('textbox').fill('restore pop');
  await expect(
    palette.getByRole('option', {name: /Restore pop-outs/})
  ).toBeVisible();
  await page.keyboard.press('Escape');
});
