/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Playwright system tests against a production build in DEMO
 * mode (no backend, synthetic `mock` venue).
 */

import {defineConfig, devices} from '@playwright/test';

/** Port used by the test server. */
const PORT = Number(process.env.E2E_PORT ?? 3157);

/** Build directory (separate runs in one checkout need separate ones). */
const DIST_DIR = process.env.E2E_DIST_DIR ?? '.next/e2e';

export default defineConfig({
  testDir: './e2e',
  timeout: 60_000,
  expect: {timeout: 10_000},
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: [
    ['list'],
    ['html', {open: 'never', outputFolder: 'playwright-report'}],
  ],
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    viewport: {width: 1440, height: 900},
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: {...devices['Desktop Chrome'], viewport: {width: 1440, height: 900}},
    },
  ],
  webServer: {
    command: `pnpm exec next build && pnpm exec next start -p ${PORT} -H 127.0.0.1`,
    url: `http://127.0.0.1:${PORT}`,
    reuseExistingServer: process.env.E2E_REUSE === '1',
    timeout: 240_000,
    env: {
      NEXT_PUBLIC_MARKET_API_URL: '',
      NEXT_PUBLIC_EXECUTION_API_URL: '',
      NEXT_DIST_DIR: DIST_DIR,
    },
  },
});
