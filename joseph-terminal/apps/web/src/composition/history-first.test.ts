/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Tests for the candles-first gate: study runs wait for
 * pending candles, at most for the bounded delay, and everything else
 * passes through.
 */

import type {
  CandleBatch,
  CandleQuery,
  ScriptBatchRun,
  StudyClient,
} from '@joseph/contracts';
import {DemoMarketDataClient} from '@joseph/mock-provider';
import {afterEach, describe, expect, it, vi} from 'vitest';
import {historyFirst} from './history-first';

const CANDLES: CandleQuery = {
  instrumentId: 'mock:perp:BTCUSD',
  timeframe: '1h',
};
const RUN: ScriptBatchRun = {
  instrument_id: 'mock:perp:BTCUSD',
  tf: '1h',
  runs: [{script_id: 'builtin:ema'}],
};

/**
 * Builds a DEMO market client whose candles resolve on demand and a study
 * client, recording calls.
 * @param maxWaitMs Gate bound.
 * @return Gated clients, the calls they saw and the candle resolver.
 */
function fake(maxWaitMs?: number) {
  const client = new DemoMarketDataClient();
  const calls: string[] = [];
  let finish: () => void = () => undefined;
  const pending = new Promise<CandleBatch>(resolve => {
    finish = () =>
      void resolve({
        schema_version: 1,
        instrument_id: 'mock:perp:BTCUSD',
        timeframe: '1h',
        candles: [],
        feed_state: 'DEMO',
        source: 'mock',
        as_of_ms: 0,
        complete: true,
        gaps: [],
      });
  });
  vi.spyOn(client, 'getCandles').mockImplementation(() => {
    calls.push('candles');
    return pending;
  });
  const studies: StudyClient = {
    catalog: async () => {
      calls.push('catalog');
      return {scripts: [], failures: []};
    },
    catalogScript: () => Promise.reject(new Error('none')),
    runBatch: async () => {
      calls.push('run');
      return {results: []};
    },
  };
  const gate = historyFirst(maxWaitMs);
  return {
    market: gate.market(client),
    studies: gate.studies(studies),
    calls,
    finish,
  };
}

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe('historyFirst', () => {
  it('holds study runs until pending candles settle', async () => {
    const {market, studies, calls, finish} = fake();
    const batch = market.getCandles(CANDLES);
    const run = studies.runBatch(RUN);
    await studies.catalog('indicator');
    expect(calls).toEqual(['candles', 'catalog']);
    finish();
    await batch;
    await run;
    expect(calls).toEqual(['candles', 'catalog', 'run']);
    // Other methods are forwarded with their receiver.
    await expect(market.listVenues()).resolves.toBeTruthy();
  });

  it('waits no longer than the bound', async () => {
    vi.useFakeTimers();
    const {market, studies, calls} = fake(1_000);
    void market.getCandles(CANDLES);
    const run = studies.runBatch(RUN);
    await vi.advanceTimersByTimeAsync(999);
    expect(calls).toEqual(['candles']);
    await vi.advanceTimersByTimeAsync(1);
    await run;
    expect(calls).toEqual(['candles', 'run']);
  });

  it('runs studies at once without pending candles', async () => {
    const {studies, calls} = fake();
    await studies.runBatch(RUN);
    expect(calls).toEqual(['run']);
  });
});
