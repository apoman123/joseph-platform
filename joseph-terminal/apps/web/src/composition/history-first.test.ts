/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Tests for the candles-first market client: study requests
 * wait for pending candles, at most for the bounded delay, and everything
 * else passes through.
 */

import type {
  CandleBatch,
  CandleQuery,
  IndicatorSeriesQuery,
  MarketDataClient,
  SeriesQuery,
} from '@joseph/contracts';
import {DemoMarketDataClient} from '@joseph/mock-provider';
import {afterEach, describe, expect, it, vi} from 'vitest';
import {historyFirst} from './history-first';

const CANDLES: CandleQuery = {
  instrumentId: 'mock:perp:BTCUSD',
  timeframe: '1h',
};
const SERIES: IndicatorSeriesQuery = {...CANDLES, pluginId: 'volume_delta'};
const AOIS: SeriesQuery = {...CANDLES};

/**
 * Builds a DEMO client whose candles resolve on demand, recording calls.
 * @return Client, the calls it saw and the candle resolver.
 */
function fakeClient(): {
  client: MarketDataClient;
  calls: string[];
  finish: () => void;
} {
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
  const series = client.getIndicatorSeries.bind(client);
  vi.spyOn(client, 'getIndicatorSeries').mockImplementation(query => {
    calls.push('series');
    return series(query);
  });
  const aois = client.getAois.bind(client);
  vi.spyOn(client, 'getAois').mockImplementation(query => {
    calls.push('aois');
    return aois(query);
  });
  return {client, calls, finish};
}

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe('historyFirst', () => {
  it('holds study requests until pending candles settle', async () => {
    const {client, calls, finish} = fakeClient();
    const gated = historyFirst(client);
    const batch = gated.getCandles(CANDLES);
    const series = gated.getIndicatorSeries(SERIES);
    const aois = gated.getAois(AOIS);
    await Promise.resolve();
    expect(calls).toEqual(['candles']);
    finish();
    await batch;
    await Promise.allSettled([series, aois]);
    expect(calls).toEqual(['candles', 'series', 'aois']);
    // Other methods are forwarded with their receiver.
    await expect(gated.listVenues()).resolves.toBeTruthy();
  });

  it('waits no longer than the bound', async () => {
    vi.useFakeTimers();
    const {client, calls} = fakeClient();
    const gated = historyFirst(client, 1_000);
    void gated.getCandles(CANDLES);
    const series = gated.getIndicatorSeries(SERIES);
    await vi.advanceTimersByTimeAsync(999);
    expect(calls).toEqual(['candles']);
    await vi.advanceTimersByTimeAsync(1);
    await Promise.allSettled([series]);
    expect(calls).toEqual(['candles', 'series']);
  });

  it('sends study requests at once without pending candles', async () => {
    const {client, calls} = fakeClient();
    await Promise.allSettled([historyFirst(client).getIndicatorSeries(SERIES)]);
    expect(calls).toEqual(['series']);
  });
});
