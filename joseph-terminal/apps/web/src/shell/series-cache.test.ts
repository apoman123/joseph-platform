/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Tests for the recently viewed series cache and the adoption
 * of candle requests started ahead of a pane.
 */

import type {CandleBatch} from '@joseph/contracts';
import {describe, expect, it} from 'vitest';
import {
  SeriesCache,
  seriesCache,
  seriesKey,
  type CachedSeries,
} from './series-cache';

/**
 * Builds a cached series.
 * @param basis Price basis identity.
 * @return Entry.
 */
function entry(basis: string): CachedSeries {
  return {
    bars: [],
    basis,
    priceAdjustment: null,
    feedState: 'LIVE',
    refreshAfterMs: undefined,
    savedAtMs: 0,
  };
}

describe('SeriesCache', () => {
  it('keeps the most recently used series', () => {
    const cache = new SeriesCache(2);
    cache.set('a', entry('a'));
    cache.set('b', entry('b'));
    expect(cache.get('a')?.basis).toBe('a');
    cache.set('c', entry('c'));
    expect(cache.get('b')).toBeUndefined();
    expect(cache.get('a')?.basis).toBe('a');
    expect(cache.get('c')?.basis).toBe('c');
  });

  it('adopts one recent started request per series', async () => {
    let now = 0;
    const cache = new SeriesCache(4, () => now);
    let sent = 0;
    const answer: CandleBatch = {
      schema_version: 1,
      instrument_id: 'mock:perp:BTCUSD',
      timeframe: '1h',
      candles: [],
      feed_state: 'DEMO',
      source: 'mock',
      as_of_ms: 0,
      complete: true,
      gaps: [],
    };
    const load = () => {
      sent += 1;
      return Promise.resolve(answer);
    };
    cache.prefetch('k', load);
    cache.prefetch('k', load);
    expect(sent).toBe(1);
    await expect(cache.adopt('k')?.batch).resolves.toBe(answer);
    expect(cache.adopt('k')).toBeUndefined();
    cache.prefetch('k', load);
    now = 6_000;
    expect(cache.adopt('k')).toBeUndefined();
    expect(sent).toBe(2);
  });

  it('never surfaces an unadopted failure', async () => {
    const cache = new SeriesCache();
    cache.prefetch('k', () => Promise.reject(new Error('down')));
    await Promise.resolve();
    await expect(cache.adopt('k')?.batch).rejects.toThrow('down');
  });

  it('keys by series and keeps one cache per runtime', () => {
    expect(seriesKey('mock:perp:BTCUSD', '1h', 'AUTO')).toBe(
      'mock:perp:BTCUSD|1h|AUTO'
    );
    const runtime = {};
    expect(seriesCache(runtime)).toBe(seriesCache(runtime));
    expect(seriesCache({})).not.toBe(seriesCache(runtime));
  });
});
