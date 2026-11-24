/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Tests for the DEMO market provider.
 */

import {describe, expect, it, vi} from 'vitest';
import {DemoMarketDataClient, DemoMarketStream} from './demo-market-client';
import {DEMO_INSTRUMENT_SPECS, demoCandle, priceAt} from './prices';

const NOW = Date.UTC(2026, 10, 20, 12, 7, 30);
const client = new DemoMarketDataClient({now: () => NOW});

describe('DemoMarketDataClient', () => {
  it('generates deterministic, DEMO-labeled candles', async () => {
    const a = await client.getCandles({
      instrumentId: 'mock:perp:BTCUSD',
      timeframe: '5m',
      limit: 50,
    });
    const b = await new DemoMarketDataClient({now: () => NOW}).getCandles({
      instrumentId: 'mock:perp:BTCUSD',
      timeframe: '5m',
      limit: 50,
    });
    expect(a.candles).toEqual(b.candles);
    expect(a.candles).toHaveLength(50);
    expect(a.feed_state).toBe('DEMO');
    expect(
      a.candles.every(c => c.feed_state === 'DEMO' && c.source === 'mock')
    ).toBe(true);
    expect(a.candles.at(-1)?.is_final).toBe(false);
    expect(a.candles.at(-2)?.is_final).toBe(true);
  });

  it('keeps OHLC invariants and tick alignment', () => {
    const spec = DEMO_INSTRUMENT_SPECS[0];
    if (!spec) throw new Error('missing spec');
    for (let i = 0; i < 200; i += 1) {
      const c = demoCandle(spec, '1m', NOW - (NOW % 60_000) - i * 60_000, NOW);
      const [o, h, l, cl] = [c.open, c.high, c.low, c.close].map(Number) as [
        number,
        number,
        number,
        number,
      ];
      expect(l).toBeLessThanOrEqual(Math.min(o, cl));
      expect(h).toBeGreaterThanOrEqual(Math.max(o, cl));
      expect(c.close.split('.')[1]?.length ?? 0).toBeLessThanOrEqual(1);
    }
    expect(priceAt(spec, NOW)).toBeGreaterThan(spec.basePrice * 0.9);
  });

  it('refuses to invent data for real venues', async () => {
    await expect(
      client.getCandles({instrumentId: 'binance:perp:BTCUSDT', timeframe: '5m'})
    ).rejects.toThrow(/connected market engine/);
    expect(
      (await client.listInstruments({venue: 'binance'})).instruments
    ).toEqual([]);
  });

  it('serves demo AOIs, events and qualification', async () => {
    const aois = await client.getAois({
      instrumentId: 'mock:perp:ETHUSD',
      timeframe: '15m',
    });
    expect(aois.feed_state).toBe('DEMO');
    expect(
      aois.aois.every(
        a =>
          a.instrument_id === 'mock:perp:ETHUSD' &&
          Number(a.lower) <= Number(a.upper)
      )
    ).toBe(true);
    expect(aois.aois.every(a => a.confirmed_at_ms <= aois.as_of_ms)).toBe(true);
    const report = await client.getQualification(undefined, 'QUALIFIED');
    expect(report.records.map(r => r.instrument_id)).toEqual([
      'mock:perp:BTCUSD',
      'mock:perp:ETHUSD',
    ]);
    const sol = (await client.getQualification()).records.find(
      r => r.instrument_id === 'mock:perp:SOLUSD'
    );
    expect(
      sol?.gates.find(g => g.gate === 'SLIPPAGE_50K_PCT')?.passed
    ).toBeNull();
  });

  it('reports a snapshot with separate mark and bid/ask', async () => {
    const snap = await client.getSnapshot('mock:perp:SOLUSD');
    expect(Number(snap.best_ask)).toBeGreaterThan(Number(snap.best_bid));
    expect(snap.feed_state).toBe('DEMO');
  });
});

describe('DemoMarketStream', () => {
  it('emits candle updates and final bars on rollover', () => {
    vi.useFakeTimers();
    let now = NOW;
    const stream = new DemoMarketStream({now: () => now, intervalMs: 1000});
    const seen: unknown[] = [];
    const off = stream.subscribe(
      [
        {
          channel: 'candles',
          instrument_id: 'mock:perp:BTCUSD',
          timeframe: '1m',
        },
      ],
      env => seen.push(env.payload)
    );
    vi.advanceTimersByTime(1000);
    now += 60_000;
    vi.advanceTimersByTime(1000);
    const finals = seen.filter(c => (c as {is_final: boolean}).is_final);
    expect(finals).toHaveLength(1);
    off();
    stream.close();
    vi.useRealTimers();
  });
});
