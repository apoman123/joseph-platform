/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Full-stack browser system tests: the production web build
 * talks to the real market and execution engines (DEMO venue only, LIVE
 * trading off). Run with `pnpm test:e2e:fullstack`.
 */

import {defineConfig, devices} from '@playwright/test';

/** Port of the web server. */
const WEB = Number(process.env.E2E_WEB_PORT ?? 3158);
/** Port of the market engine. */
const MARKET = Number(process.env.E2E_MARKET_PORT ?? 18_000);
/** Port of the execution engine. */
const EXECUTION = Number(process.env.E2E_EXECUTION_PORT ?? 18_100);
/** How engines are started (`uv run --no-sync` reuses an existing venv). */
const UV_RUN = process.env.E2E_UV_RUN ?? 'uv run --frozen';
/** Development bearer token the engines accept for the test tenant. */
const TOKEN = 'fullstack-token';
/** Service token the engines use between themselves. */
const SERVICE = 'fullstack-service-token';
/** Repository root, relative to this config's directory. */
const ROOT = '../../..';
/** Build-time environment that points the web build at both engines. */
const API = {
  NEXT_PUBLIC_MARKET_API_URL: `http://127.0.0.1:${MARKET}`,
  NEXT_PUBLIC_MARKET_WS_URL: `ws://127.0.0.1:${MARKET}`,
  NEXT_PUBLIC_EXECUTION_API_URL: `http://127.0.0.1:${EXECUTION}`,
  NEXT_PUBLIC_EXECUTION_WS_URL: `ws://127.0.0.1:${EXECUTION}`,
  NEXT_PUBLIC_DEV_TOKEN: TOKEN,
};
/** Environment shared by both engines: tokens, CORS and LIVE trading off. */
const ENGINE_ENV = {
  JOSEPH_DEV_TOKENS: `${TOKEN}:tenant-web:user-web`,
  JOSEPH_SERVICE_TOKEN: SERVICE,
  JOSEPH_CORS_ORIGINS: `http://127.0.0.1:${WEB}`,
  JOSEPH_LIVE_TRADING_ENABLED: 'false',
};

export default defineConfig({
  testDir: './e2e-fullstack',
  timeout: 90_000,
  expect: {timeout: 15_000},
  workers: 1,
  reporter: [['list']],
  use: {
    baseURL: `http://127.0.0.1:${WEB}`,
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
  webServer: [
    {
      command: `cd ${ROOT}/joseph-market-engine && ${UV_RUN} uvicorn joseph.bootstrap:create_application --factory --host 127.0.0.1 --port ${MARKET}`,
      url: `http://127.0.0.1:${MARKET}/health`,
      timeout: 120_000,
      reuseExistingServer: false,
      env: {
        ...ENGINE_ENV,
        JOSEPH_DEMO_ONLY: 'true',
        JOSEPH_DATABASE_URL: 'sqlite://',
        JOSEPH_EXECUTION_URL: `http://127.0.0.1:${EXECUTION}`,
      },
    },
    {
      command: `cd ${ROOT}/joseph-execution-engine && ${UV_RUN} uvicorn joseph_execution.bootstrap:create_application --factory --host 127.0.0.1 --port ${EXECUTION}`,
      url: `http://127.0.0.1:${EXECUTION}/health`,
      timeout: 120_000,
      reuseExistingServer: false,
      env: {
        ...ENGINE_ENV,
        JOSEPH_DATABASE_URL: 'sqlite://',
        JOSEPH_MARKET_GATEWAY: 'http',
        JOSEPH_MARKET_URL: `http://127.0.0.1:${MARKET}`,
      },
    },
    {
      command: `pnpm exec next build && pnpm exec next start -p ${WEB} -H 127.0.0.1`,
      url: `http://127.0.0.1:${WEB}`,
      timeout: 300_000,
      reuseExistingServer: false,
      env: {
        ...API,
        NEXT_DIST_DIR: process.env.E2E_DIST_DIR ?? '.next/fullstack',
      },
    },
  ],
});

export {API, TOKEN};
