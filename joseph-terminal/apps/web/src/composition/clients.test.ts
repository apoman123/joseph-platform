/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Tests for the client bundle: the DEMO LIVE rehearsal is
 * opt-in through the page URL and DEMO-only.
 */

import {afterEach, describe, expect, it} from 'vitest';
import {buildClients, demoLiveRehearsalRequested} from './clients';
import type {TerminalConfig} from './env';

/** DEMO configuration (no backend). */
const DEMO_CONFIG: TerminalConfig = {
  marketApiUrl: null,
  marketWsUrl: null,
  executionApiUrl: null,
  executionWsUrl: null,
  devToken: null,
  demo: true,
};

afterEach(() => {
  window.history.replaceState(null, '', '/');
});

/**
 * Returns whether the bundle's execution client lists a LIVE-capable
 * account.
 * @param config Configuration to build the clients from.
 * @return True when an account offers LIVE.
 */
async function offersLive(config: TerminalConfig): Promise<boolean> {
  const {accounts} =
    await buildClients(config).clients.execution.listAccounts();
  return accounts.some(a => a.modes.includes('LIVE'));
}

describe('DEMO live rehearsal opt-in', () => {
  it('reads only the explicit query parameter', () => {
    expect(demoLiveRehearsalRequested('?demo-live-rehearsal=1')).toBe(true);
    expect(demoLiveRehearsalRequested('?demo-live-rehearsal=0')).toBe(false);
    expect(demoLiveRehearsalRequested('?live=1')).toBe(false);
    expect(demoLiveRehearsalRequested('')).toBe(false);
  });

  it('keeps every DEMO account off LIVE unless the page opts in', async () => {
    expect(await offersLive(DEMO_CONFIG)).toBe(false);
    window.history.replaceState(null, '', '/?demo-live-rehearsal=1');
    expect(demoLiveRehearsalRequested()).toBe(true);
    expect(await offersLive(DEMO_CONFIG)).toBe(true);
  });

  it('ignores the opt-in with a configured execution engine', async () => {
    window.history.replaceState(null, '', '/?demo-live-rehearsal=1');
    const bundle = buildClients({
      ...DEMO_CONFIG,
      marketApiUrl: 'http://market.test',
      executionApiUrl: 'http://engine.test',
      demo: false,
    });
    expect(bundle.clients.demo).toBe(false);
    expect(bundle.capabilities).toContain('execution');
  });
});
