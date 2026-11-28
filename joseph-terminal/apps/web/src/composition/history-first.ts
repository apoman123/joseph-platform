/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Candles first: indicator series and AOI requests wait while
 * chart candles are loading (for a bounded time), so a chart's own history
 * is not queued behind study work in the browser's connection pool or the
 * engine's venue rate limits.
 */

import type {MarketDataClient} from '@joseph/contracts';

/** Longest wait of a study request for pending candles (3 s). */
export const HISTORY_FIRST_MAX_WAIT_MS = 3_000;

/** Methods the wrapper replaces. */
type Gated = Pick<
  MarketDataClient,
  'getCandles' | 'getIndicatorSeries' | 'getAois'
>;

/**
 * Wraps a market client so study requests yield to candle requests.
 * @param market Client to wrap; every other method is forwarded unchanged.
 * @param maxWaitMs Longest wait of one study request, in milliseconds.
 * @return Client with the same interface.
 */
export function historyFirst(
  market: MarketDataClient,
  maxWaitMs = HISTORY_FIRST_MAX_WAIT_MS
): MarketDataClient {
  let pending = 0;
  const waiters = new Set<() => void>();
  /** Releases the waiting study requests once no candles are pending. */
  const settle = (): void => {
    pending -= 1;
    if (pending > 0) return;
    for (const wake of [...waiters]) {
      wake();
    }
  };
  /**
   * Resolves once no candle request is pending, or after `maxWaitMs`.
   * @return Promise that never rejects.
   */
  const idle = (): Promise<void> =>
    pending === 0
      ? Promise.resolve()
      : new Promise<void>(resolve => {
          /** Resolves the wait and forgets it. */
          const wake = (): void => {
            clearTimeout(timer);
            waiters.delete(wake);
            resolve();
          };
          const timer = setTimeout(wake, maxWaitMs);
          waiters.add(wake);
        });
  const gated: Gated = {
    getCandles: query => {
      pending += 1;
      let request: Promise<Awaited<ReturnType<Gated['getCandles']>>>;
      try {
        request = market.getCandles(query);
      } catch (error) {
        settle();
        throw error;
      }
      return request.finally(settle);
    },
    getIndicatorSeries: query =>
      idle().then(() => market.getIndicatorSeries(query)),
    getAois: query => idle().then(() => market.getAois(query)),
  };
  return new Proxy(market, {
    get(target, property, receiver): unknown {
      if (property in gated) return gated[property as keyof Gated];
      const value: unknown = Reflect.get(target, property, receiver);
      return typeof value === 'function'
        ? (value as (...args: unknown[]) => unknown).bind(target)
        : value;
    },
  });
}
