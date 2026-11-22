/**
 * @fileoverview End-to-end: strategy template → validate → save version,
 * with automatic live trading visibly locked (DEMO mode).
 */
import {expect, test} from '@playwright/test';
import {loadTerminal, openPanel} from './trading-helpers';

test('builds, validates and saves a strategy version', async ({page}) => {
  await loadTerminal(page);
  await openPanel(page, 'Strategy');
  await page
    .getByRole('combobox', {name: 'Start from a template'})
    .selectOption({label: 'Short B: premium OTE rejection'});
  await expect(
    page
      .locator('[data-testid^="node-"]')
      .getByText('At least 2 of 1d, 4h and 1h bias are bearish')
  ).toBeVisible();
  await page.getByRole('button', {name: 'Validate'}).click();
  await expect(page.getByText(/Valid on the server/)).toBeVisible();
  await page.getByRole('button', {name: 'Save strategy'}).click();
  await expect(page.getByTestId('strategy-version')).toHaveText('Version 1');
  await page.getByRole('tab', {name: 'Versions'}).click();
  await expect(page.getByTestId('auto-live-lock')).toContainText(
    'Automatic live trading is locked'
  );
});
