/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Browser test in DEMO mode: *Export workspace* writes the
 * layout and the drawings of every instrument (including drawings of tools
 * this build doesn't know), and *Import a workspace* restores them into a
 * browser that has none, showing them on the open chart without a reload.
 */

import {readFileSync} from 'node:fs';
import {expect, test, type Page} from '@playwright/test';
import {
  BTC_DRAWINGS_KEY,
  dragOnChart,
  openTerminal,
  savedTools,
} from './support';
import {openPanel} from './trading-helpers';

/** Folder of the documentation screenshots, relative to `apps/web`. */
const SHOTS = 'e2e/screenshots';

/** Storage key of the DEMO ETHUSD drawings in the default workspace. */
const ETH_DRAWINGS_KEY = 'joseph.drawings.v1.default.mock:perp:ETHUSD';

/**
 * Runs a command from the palette: types the text and runs the top match.
 * @param page Page.
 * @param text Text typed into the palette.
 */
async function runCommand(page: Page, text: string): Promise<void> {
  await page.keyboard.press('Control+k');
  const palette = page.getByRole('dialog', {name: 'Command palette'});
  await expect(palette).toBeVisible();
  await palette.getByRole('textbox').fill(text);
  await page.keyboard.press('Enter');
}

/** Export file shape read back by the test. */
interface ExportFile {
  format: string;
  schema_version: number;
  workspace: {workspace_id: string};
  drawings: Array<{instrument_id: string; drawings: Array<{tool_id: string}>}>;
}

test('exports drawings with the workspace and imports them back', async ({
  page,
}) => {
  await openTerminal(page);
  await page.getByTestId('tool-trendline').click();
  await dragOnChart(page, [0.3, 0.6], [0.6, 0.35]);
  await page.getByTestId('tool-rectangle').click();
  await dragOnChart(page, [0.65, 0.3], [0.8, 0.45]);
  await expect.poll(() => savedTools(page)).toEqual(['trendline', 'rectangle']);

  // Drawings on another instrument (not on screen) are exported too, and a
  // drawing of a tool this build doesn't have is kept, never dropped.
  await page.evaluate(
    ({btcKey, ethKey}) => {
      const btc = JSON.parse(localStorage.getItem(btcKey) ?? '{}') as {
        drawings: Array<Record<string, unknown>>;
      };
      const [first] = btc.drawings;
      const eth = (id: string, tool: string) => ({
        ...first,
        drawing_id: id,
        tool_id: tool,
        instrument_id: 'mock:perp:ETHUSD',
      });
      localStorage.setItem(
        ethKey,
        JSON.stringify({
          schema_version: 1,
          workspace_id: 'default',
          instrument_id: 'mock:perp:ETHUSD',
          drawings: [eth('eth-1', 'trendline'), eth('eth-2', 'removed-tool')],
        })
      );
    },
    {btcKey: BTC_DRAWINGS_KEY, ethKey: ETH_DRAWINGS_KEY}
  );

  // Listen before running the command, so the event is not missed.
  const [download] = await Promise.all([
    page.waitForEvent('download'),
    runCommand(page, 'Export workspace'),
  ]);
  expect(download.suggestedFilename()).toBe('joseph-workspace.json');
  const file = test.info().outputPath('joseph-workspace.json');
  await download.saveAs(file);
  const exported = JSON.parse(readFileSync(file, 'utf8')) as ExportFile;
  expect(exported.format).toBe('joseph.workspace-export');
  expect(exported.schema_version).toBe(2);
  expect(
    exported.drawings.map(c => [
      c.instrument_id,
      c.drawings.map(d => d.tool_id),
    ])
  ).toEqual([
    ['mock:perp:BTCUSD', ['trendline', 'rectangle']],
    ['mock:perp:ETHUSD', ['trendline', 'removed-tool']],
  ]);
  await expect(page.getByText(/exported with 4 drawings on 2/)).toBeVisible();

  // A browser without any saved drawings.
  await page.evaluate(() => void localStorage.clear());
  await page.reload();
  await expect(page.locator('.terminal[data-ready="true"]')).toBeVisible();
  await expect.poll(() => savedTools(page)).toEqual([]);
  await openPanel(page, 'Objects');
  const list = page.getByRole('list', {name: 'Drawings, top first'});
  await expect(list.getByRole('listitem')).toHaveCount(0);

  const [chooser] = await Promise.all([
    page.waitForEvent('filechooser'),
    runCommand(page, 'Import a workspace'),
  ]);
  await chooser.setFiles(file);
  await expect(
    page.getByText(/Imported the workspace and 4 drawings on 2 instruments/)
  ).toBeVisible();
  // The open BTC chart shows the restored drawings without a reload.
  await expect(list.getByRole('listitem')).toHaveCount(2);
  await expect.poll(() => savedTools(page)).toEqual(['trendline', 'rectangle']);
  await expect
    .poll(() => savedTools(page, ETH_DRAWINGS_KEY))
    .toEqual(['trendline', 'removed-tool']);
  // Let the chart draw the restored drawings before the screenshot.
  await page.waitForTimeout(300);
  await page.screenshot({path: `${SHOTS}/37-workspace-import-drawings.png`});

  // Restored drawings are saved, so they survive a reload.
  await page.reload();
  await expect(page.locator('.terminal[data-ready="true"]')).toBeVisible();
  await expect.poll(() => savedTools(page)).toEqual(['trendline', 'rectangle']);
});
