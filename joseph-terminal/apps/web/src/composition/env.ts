/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Build-time terminal configuration from `NEXT_PUBLIC_*`.
 *
 * Without a market engine URL the terminal runs in DEMO mode on the
 * synthetic `mock` venue. A configured backend never falls back to
 * synthetic data: failures surface as STALE or UNAVAILABLE.
 */

/** Resolved configuration. */
export interface TerminalConfig {
  /** Market engine REST base URL. */
  marketApiUrl: string | null;
  /** Market engine WebSocket base URL. */
  marketWsUrl: string | null;
  /** Execution engine REST base URL. */
  executionApiUrl: string | null;
  /** Execution engine WebSocket base URL. */
  executionWsUrl: string | null;
  /**
   * Development bearer token. Production must replace this with a
   * server-side session (BFF); never ship real credentials to the browser.
   */
  devToken: string | null;
  /** Whether the terminal serves DEMO data. */
  demo: boolean;
}

/**
 * Normalizes an optional URL.
 * @param value Raw value.
 * @return URL without trailing slash, or null.
 */
function clean(value: string | undefined): string | null {
  const trimmed = (value ?? '').trim().replace(/\/$/, '');
  return trimmed ? trimmed : null;
}

/**
 * Reads the configuration (values are inlined by Next.js at build time).
 * @return Configuration.
 */
export function readConfig(): TerminalConfig {
  const marketApiUrl = clean(process.env.NEXT_PUBLIC_MARKET_API_URL);
  const marketWsUrl =
    clean(process.env.NEXT_PUBLIC_MARKET_WS_URL) ??
    (marketApiUrl ? marketApiUrl.replace(/^http/, 'ws') : null);
  const executionApiUrl = clean(process.env.NEXT_PUBLIC_EXECUTION_API_URL);
  const executionWsUrl =
    clean(process.env.NEXT_PUBLIC_EXECUTION_WS_URL) ??
    (executionApiUrl ? executionApiUrl.replace(/^http/, 'ws') : null);
  return {
    marketApiUrl,
    marketWsUrl,
    executionApiUrl,
    executionWsUrl,
    devToken: clean(process.env.NEXT_PUBLIC_DEV_TOKEN),
    demo: marketApiUrl === null,
  };
}
