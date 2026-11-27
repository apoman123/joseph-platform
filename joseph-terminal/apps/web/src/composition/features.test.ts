/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Feature manifest graph checks for the composed terminal.
 */

import {describe, expect, it} from 'vitest';
import {composeFeatures} from './features';
import {createRuntime} from './runtime';

/** DEMO configuration (no backend). */
const DEMO_CONFIG = {
  marketApiUrl: null,
  marketWsUrl: null,
  executionApiUrl: null,
  executionWsUrl: null,
  devToken: null,
  demo: true,
};

describe('composed feature graph', () => {
  it('has unique ids, removal plans and no structural issues', () => {
    const runtime = createRuntime(composeFeatures, DEMO_CONFIG);
    const ids = runtime.registry.ids();
    expect(new Set(ids).size).toBe(ids.length);
    expect(runtime.registry.validate()).toEqual([]);
    expect(runtime.registry.activationOrder()).toHaveLength(ids.length);
  });

  it('activates every feature in DEMO mode and disposes cleanly', async () => {
    const runtime = createRuntime(composeFeatures, DEMO_CONFIG);
    await runtime.registry.activateAll();
    const statuses = runtime.registry.status.get();
    expect(statuses.filter(s => s.lifecycle !== 'ENABLED')).toEqual([]);
    expect(runtime.tools.list().length).toBe(37 + 86);
    expect(runtime.commands.list().length).toBeGreaterThan(20);
    runtime.registry.deactivateAll();
    expect(runtime.commands.list()).toEqual([]);
    expect(runtime.tools.list()).toEqual([]);
    expect(runtime.chartLayers.providers.get()).toEqual([]);
  });

  it('removing one drawing tool leaves the rest of the terminal intact', async () => {
    const runtime = createRuntime(
      rt =>
        composeFeatures(rt).filter(
          f => f.manifest.id !== 'drawing.tool.fib-retracement'
        ),
      DEMO_CONFIG
    );
    expect(runtime.registry.validate()).toEqual([]);
    await runtime.registry.activateAll();
    expect(runtime.tools.get('fib-retracement')).toBeUndefined();
    expect(runtime.tools.get('trendline')).toBeDefined();
    runtime.registry.deactivateAll();
  });

  it('starts in paper mode with DEMO data and never selects LIVE by default', () => {
    const runtime = createRuntime(composeFeatures, DEMO_CONFIG);
    expect(runtime.orderMode.get()).toBe('PAPER');
    expect(runtime.clients.demo).toBe(true);
    expect(runtime.selection.get().instrumentId).toBe('mock:perp:BTCUSD');
  });
});
