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

  it('leaves out the bar that starts at an exclusive toMs', async () => {
    const toMs = Date.UTC(2026, 10, 20, 11);
    const batch = await client.getCandles({
      instrumentId: 'mock:perp:BTCUSD',
      timeframe: '5m',
      toMs,
      limit: 2,
    });
    expect(batch.candles.map(c => c.start_ms)).toEqual([
      toMs - 600_000,
      toMs - 300_000,
    ]);
    expect(batch.candles.every(c => c.is_final)).toBe(true);
  });

  it('keeps the bar containing a toMs that is not a bar start', async () => {
    const start = Date.UTC(2026, 10, 20, 11);
    const batch = await client.getCandles({
      instrumentId: 'mock:perp:BTCUSD',
      timeframe: '5m',
      toMs: start + 60_000,
      limit: 1,
    });
    expect(batch.candles.map(c => c.start_ms)).toEqual([start]);
  });

  it('serves the bars from the one containing fromMs up to toMs', async () => {
    const fromMs = Date.UTC(2026, 10, 20, 11);
    const batch = await client.getCandles({
      instrumentId: 'mock:perp:BTCUSD',
      timeframe: '5m',
      fromMs: fromMs + 60_000,
      toMs: fromMs + 900_000,
    });
    expect(batch.candles.map(c => c.start_ms)).toEqual([
      fromMs,
      fromMs + 300_000,
      fromMs + 600_000,
    ]);
  });

  it('ends a fromMs range with the bar forming now', async () => {
    const fromMs = Date.UTC(2026, 10, 20, 11, 50);
    for (const toMs of [undefined, NOW + 3_600_000]) {
      const batch = await client.getCandles({
        instrumentId: 'mock:perp:BTCUSD',
        timeframe: '5m',
        fromMs,
        ...(toMs === undefined ? {} : {toMs}),
      });
      expect(batch.candles.map(c => c.start_ms)).toEqual([
        fromMs,
        fromMs + 300_000,
        fromMs + 600_000,
        fromMs + 900_000,
      ]);
      expect(batch.candles.at(-1)?.is_final).toBe(false);
    }
  });

  it('keeps OHLC invariants and tick alignment', () => {
    const spec = DEMO_INSTRUMENT_SPECS[0];
    if (!spec) throw new Error('missing spec');
    for (let i = 0; i < 200; i += 1) {
      const c = demoCandle(spec, '1m', NOW - (NOW % 60_000) - i * 60_000, NOW);
      // Mapping a four-element array keeps four elements.
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

  it('serves demo AOIs', async () => {
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
      env => {
        seen.push(env.payload);
      }
    );
    vi.advanceTimersByTime(1000);
    now += 60_000;
    vi.advanceTimersByTime(1000);
    // Every payload on a candles channel is a candle.
    const finals = seen.filter(c => (c as {is_final: boolean}).is_final);
    expect(finals).toHaveLength(1);
    off();
    stream.close();
    vi.useRealTimers();
  });
});
