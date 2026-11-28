/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Full stack: the screener scans built-in Filter scripts and
 * the alert manager saves a built-in Alert job on the real market engine,
 * without the isolated Python runtime.
 */

import {expect, test} from '@playwright/test';
import {API, TOKEN} from '../playwright.fullstack.config';
import {loadTerminal, openPanel} from '../e2e/trading-helpers';

/** Authorization header for calling the engine directly. */
const AUTH = {Authorization: `Bearer ${TOKEN}`};

test('the screener scans the built-in liquidity filter on the engine', async ({
  page,
}) => {
  await loadTerminal(page);
  await openPanel(page, 'Screener');
  await page.getByRole('combobox', {name: 'Venue'}).selectOption('mock');
  const run = page.getByTestId('screener-run');
  await expect(run).toBeEnabled({timeout: 15_000});
  await run.click();
  await expect(page.getByTestId('screener-progress')).toContainText(
    'complete',
    {timeout: 30_000}
  );
  await expect(page.getByTestId('screener-row-mock:perp:BTCUSD')).toBeVisible();
});

test('the alert manager saves a built-in alert job', async ({
  page,
  request,
}) => {
  await loadTerminal(page);
  await openPanel(page, 'Alert manager');
  const create = page.getByTestId('alert-create');
  await expect(create).toBeEnabled({timeout: 15_000});
  await create.click();
  const dialog = page.getByRole('dialog', {name: 'Create alert'});
  await dialog.getByTestId('alert-symbol').fill('mock:perp:ETHUSD');
  await dialog
    .getByTestId('alert-condition')
    .selectOption('builtin:ma-cross@0');
  await dialog.getByTestId('alert-save').click();
  await expect(page.getByRole('table', {name: 'Alert jobs'})).toContainText(
    'Moving average cross'
  );
  const jobs = await request.get(
    `${API.NEXT_PUBLIC_MARKET_API_URL}/v1/script-alerts`,
    {
      headers: AUTH,
    }
  );
  const body = (await jobs.json()) as Array<{
    script_id: string;
    version: number;
    source_hash: string | null;
    job_id: string;
  }>;
  const job = body.find(row => row.script_id === 'builtin:ma-cross');
  expect(job?.version).toBe(0);
  expect(job?.source_hash).toBeTruthy();
  await request.delete(
    `${API.NEXT_PUBLIC_MARKET_API_URL}/v1/script-alerts/${job?.job_id ?? ''}`,
    {headers: AUTH}
  );
});
