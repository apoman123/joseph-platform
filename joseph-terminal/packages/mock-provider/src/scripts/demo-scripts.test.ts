/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Tests for the DEMO scripts client: the catalog mirrors the
 * engine's export, scans combine built-in filters with AND and report
 * missing data as unavailable, and alert jobs log only newly closed bars.
 */

import {readFileSync} from 'node:fs';
import {join} from 'node:path';
import type {MarketSnapshot, ScanJob} from '@joseph/contracts';
import {describe, expect, it} from 'vitest';
import {DemoMarketDataClient} from '../market';
import {demoAlertEvents} from './demo-alerts';
import {DemoUnavailable, demoFilter, type DemoBar} from './demo-filters';
import {DEMO_SCRIPT_CATALOG} from './demo-script-catalog';
import {DemoScriptsClient} from './demo-scripts-client';

const ARTIFACT = join(
  __dirname,
  '..',
  '..',
  '..',
  'contracts',
  'openapi',
  'builtin-script-catalog.json'
);

/**
 * Polls a DEMO scan until it finishes.
 * @param client Client.
 * @param job Submitted job.
 * @return Final job.
 */
async function finish(client: DemoScriptsClient, job: ScanJob) {
  for (let i = 0; i < 200; i++) {
    const next = await client.scanStatus(job.job_id);
    if (next.status !== 'running') return next;
    await new Promise(resolve => setTimeout(resolve, 5));
  }
  throw new Error('scan did not finish');
}

/**
 * Builds closed bars from closes.
 * @param closes Closes.
 * @return Bars.
 */
function bars(closes: number[]): DemoBar[] {
  return closes.map(close => ({
    open: close,
    high: close,
    low: close,
    close,
    volume: 100,
  }));
}

describe('DEMO scripts client', () => {
  it('mirrors the engine built-in filter and alert catalog', () => {
    const exported = JSON.parse(readFileSync(ARTIFACT, 'utf8')) as {
      scripts: Array<Record<string, unknown>>;
    };
    expect(DEMO_SCRIPT_CATALOG.map(s => s.script_id)).toEqual(
      exported.scripts.map(s => s.script_id)
    );
    DEMO_SCRIPT_CATALOG.forEach((script, i) => {
      const engine = exported.scripts[i];
      expect(script.inputs).toEqual(engine?.inputs);
      expect(script.source_hash).toBe(engine?.source_hash);
      expect(script.title).toBe(engine?.title);
    });
  });

  it('scans built-in filters combined with AND on DEMO data', async () => {
    const client = new DemoScriptsClient({market: new DemoMarketDataClient()});
    const all = await finish(
      client,
      await client.screen({
        conditions: [
          {
            script_id: 'builtin:price',
            inputs: {operator: 'above', value: 0},
          },
        ],
      })
    );
    expect(all.status).toBe('complete');
    expect(all.matches?.length).toBeGreaterThan(3);
    const none = await finish(
      client,
      await client.screen({
        market_type: 'perp',
        conditions: [
          {script_id: 'builtin:price', inputs: {operator: 'above', value: 0}},
          {script_id: 'builtin:price', inputs: {operator: 'below', value: 0}},
        ],
      })
    );
    expect(none.matches).toEqual([]);
    expect(none.total).toBe(3);
    const missing = await finish(
      client,
      await client.screen({conditions: [{script_id: 'builtin:rsi-range'}]})
    );
    expect(missing.matches).toEqual([]);
    expect(missing.unavailable?.length).toBe(missing.total);
    const liquid = await finish(
      client,
      await client.screen({
        conditions: [{script_id: 'builtin:liquidity-qualification'}],
      })
    );
    expect(liquid.matches?.map(m => m.instrument_id)).toContain(
      'mock:perp:BTCUSD'
    );
    expect(liquid.matches?.map(m => m.instrument_id)).not.toContain(
      'mock:rate:UST10Y'
    );
    await expect(
      client.screen({conditions: [{script_id: 'my-filter'}]})
    ).rejects.toThrow(/market engine/);
    await expect(
      client.screen({
        conditions: [{script_id: 'builtin:price', inputs: {bogus: 1}}],
      })
    ).rejects.toThrow(/no input bogus/);
  });

  it('reports missing snapshot values as unavailable', async () => {
    const market = new DemoMarketDataClient();
    const snapshot: MarketSnapshot = {
      ...(await market.getSnapshot('mock:perp:BTCUSD')),
      change_24h_pct: null,
    };
    const [listed] = (await market.listInstruments({venue: 'mock'}))
      .instruments;
    if (!listed) throw new Error('no DEMO instrument');
    const ctx = {
      snapshot,
      instrument: listed,
      bars: [],
      book: null,
      nowMs: 0,
    };
    expect(() =>
      demoFilter('builtin:change', ctx, {operator: 'above', value: 0})
    ).toThrow(DemoUnavailable);
  });

  it('publishes the built-in alert events with the engine names', () => {
    const events = demoAlertEvents(
      'builtin:price-cross',
      bars([99, 100, 101, 102, 99]),
      {level: 100, condition: 'crossing'},
      'mock:perp:BTCUSD'
    );
    expect(events.map(e => [e.index, e.name])).toEqual([
      [2, 'crossing-up'],
      [4, 'crossing-down'],
    ]);
    expect(
      demoAlertEvents(
        'builtin:breakout',
        bars([5, 4, 3, 6, 2, 1]),
        {length: 3, extreme: 'either'},
        'x'
      ).map(e => e.name)
    ).toEqual(['new-high', 'new-low', 'new-low']);
  });

  it('logs alert jobs only for bars closing after they were saved', async () => {
    let now = Date.UTC(2026, 10, 2, 12, 0, 30);
    const market = new DemoMarketDataClient({now: () => now});
    const client = new DemoScriptsClient({market, now: () => now});
    const job = await client.saveAlert({
      script_id: 'builtin:channel',
      version: 0,
      instrument_id: 'mock:perp:BTCUSD',
      tf: '1m',
      enabled: true,
      history_bars: 20,
      inputs: {lower: 0, upper: 1e12, condition: 'inside'},
    });
    expect(job.title).toBe('Price channel');
    expect(await client.alertLog()).toEqual([]);
    now += 3 * 60_000;
    const log = await client.alertLog();
    expect(log.length).toBeGreaterThanOrEqual(2);
    expect(log.every(s => s.kind === 'CUSTOM_ALERT')).toBe(true);
    expect(log.every(s => s.data_source === 'DEMO')).toBe(true);
    expect(await client.alertLog()).toEqual(log);
    expect((await client.alerts())[0]?.emitted).toBe(log.length);
    await expect(
      client.saveAlert({...job, script_id: 'mine', version: 1})
    ).rejects.toThrow(/market engine/);
    await client.deleteAlert(job.job_id);
    expect(await client.alerts()).toEqual([]);
  });
});
