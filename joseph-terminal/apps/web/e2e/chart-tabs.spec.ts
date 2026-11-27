/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/** @fileoverview Browser coverage of saved market tabs and the indicator library. */

import {expect, test} from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import {openTerminal, pickTimeframe} from './support';

test('opens more than eight market tabs and restores independent charts', async ({
  page,
}) => {
  await openTerminal(page);
  const tabs = page.getByRole('tablist', {name: 'Chart tabs'});
  for (let index = 0; index < 9; index += 1) {
    await page.getByRole('button', {name: 'Add chart tab'}).click();
    await page.getByRole('button', {name: 'Candlestick chart'}).click();
  }
  await expect(tabs.getByRole('tab')).toHaveCount(10);
  await expect(page.locator('.chart-grid > .chart-grid__cell')).toHaveCount(1);
  await page.getByTestId('instrument-selector').click();
  await page.getByRole('textbox', {name: 'Search instruments'}).fill('ETH');
  await page.getByTestId('instrument-option-mock:perp:ETHUSD').click();
  await pickTimeframe(page, '1h');
  await expect(tabs.getByRole('tab', {selected: true})).toHaveText(
    'ETHUSD · 1h'
  );
  await tabs.getByRole('tab').first().click();
  await expect(page.locator('.pane__symbol')).toHaveText('BTCUSD');
  await tabs.getByRole('tab', {name: 'ETHUSD · 1h'}).click();
  await expect(page.locator('.pane__symbol')).toHaveText('ETHUSD');
  await page.waitForTimeout(500);
  await page.reload();
  await expect(tabs.getByRole('tab')).toHaveCount(10);
  await expect(tabs.getByRole('tab', {selected: true})).toHaveText(
    'ETHUSD · 1h'
  );
  await page.getByRole('button', {name: 'Close ETHUSD · 1h'}).click();
  await expect(tabs.getByRole('tab')).toHaveCount(9);
});

test('searches indicators and restores the starred collection', async ({
  page,
}) => {
  await openTerminal(page);
  await page.getByTestId('indicators-menu').click();
  const dialog = page.getByRole('dialog', {name: 'Indicators', exact: true});
  const search = dialog.getByRole('textbox', {name: 'Search indicators'});
  await expect(search).toBeFocused();
  await search.fill('RSI');
  await dialog.getByRole('checkbox', {name: 'RSI', exact: true}).check();
  await dialog.getByRole('button', {name: 'Collect RSI'}).click();
  await dialog.getByRole('tab', {name: /My collection/}).click();
  await expect(
    dialog.getByRole('checkbox', {name: 'RSI', exact: true})
  ).toBeChecked();
  await expect(dialog.getByRole('checkbox', {name: 'EMA'})).toHaveCount(0);
  await page.screenshot({path: 'test-results/indicator-library-desktop.png'});
  const accessibility = await new AxeBuilder({page})
    .include('.indicator-library')
    .analyze();
  expect(
    accessibility.violations.filter(
      item => item.impact === 'serious' || item.impact === 'critical'
    )
  ).toEqual([]);
  await page.keyboard.press('Escape');
  await expect(dialog).toHaveCount(0);
  await expect(page.getByTestId('indicators-menu')).toBeFocused();
  await page.waitForTimeout(500);
  await page.reload();
  await page.getByTestId('indicators-menu').click();
  await dialog.getByRole('tab', {name: /My collection/}).click();
  await expect(
    dialog.getByRole('checkbox', {name: 'RSI', exact: true})
  ).toBeChecked();
  await dialog.getByRole('tab', {name: 'My scripts'}).click();
  await expect(dialog.getByText('No custom scripts yet')).toBeVisible();
  await page.setViewportSize({width: 390, height: 844});
  await page.screenshot({path: 'test-results/indicator-library-mobile.png'});
  const box = await dialog.boundingBox();
  expect(box?.width).toBeLessThanOrEqual(390);
});
