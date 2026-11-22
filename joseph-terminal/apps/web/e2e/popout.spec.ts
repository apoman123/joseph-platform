/**
 * @fileoverview System tests of detachable panes in DEMO mode: a chart
 * popped out into its own window follows the main window and docks again
 * when the window closes; a detached panel remembered by the layout is
 * offered for re-opening after the browser loses its windows.
 */
import {expect, test, type Page} from '@playwright/test';
import {openTerminal} from './support';
import {openPanel} from './trading-helpers';

const SHOTS = 'e2e/screenshots';

/**
 * Waits until a pop-out window is ready and linked to the main window.
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

test('pops a chart out, follows the main window and docks again on close', async ({
  page,
  context,
}) => {
  await openTerminal(page);
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

test('brings a detached panel back and re-opens remembered windows after a reload', async ({
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
