/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Candles first: study runs wait while chart candles are
 * loading (for a bounded time), so a chart's own history is not queued
 * behind study work in the browser's connection pool or the engine's venue
 * rate limits.
 */

import type {MarketDataClient, StudyClient} from '@joseph/contracts';

/** Longest wait of a study request for pending candles (3 s). */
export const HISTORY_FIRST_MAX_WAIT_MS = 3_000;

/** A shared gate between candle requests and study runs. */
export interface HistoryFirst {
  /**
   * Wraps a market client whose candle requests hold study runs back.
   * @param market Client to wrap; other methods are forwarded unchanged.
   * @return Client with the same interface.
   */
  market(market: MarketDataClient): MarketDataClient;
  /**
   * Wraps a study client whose batched runs wait for pending candles.
   * @param studies Client to wrap; the catalog calls are not delayed.
   * @return Client with the same interface.
   */
  studies(studies: StudyClient): StudyClient;
}

/**
 * Creates the candles-first gate.
 * @param maxWaitMs Longest wait of one study run, in milliseconds.
 * @return The gate's client wrappers.
 */
export function historyFirst(
  maxWaitMs = HISTORY_FIRST_MAX_WAIT_MS
): HistoryFirst {
  let pending = 0;
  const waiters = new Set<() => void>();
  /** Releases the waiting study runs once no candles are pending. */
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
  return {
    market(market) {
      const getCandles: MarketDataClient['getCandles'] = query => {
        pending += 1;
        let request: ReturnType<MarketDataClient['getCandles']>;
        try {
          request = market.getCandles(query);
        } catch (error) {
          settle();
          throw error;
        }
        return request.finally(settle);
      };
      return new Proxy(market, {
        get(target, property, receiver): unknown {
          if (property === 'getCandles') return getCandles;
          const value: unknown = Reflect.get(target, property, receiver);
          return typeof value === 'function'
            ? (value as (...args: unknown[]) => unknown).bind(target)
            : value;
        },
      });
    },
    studies(studies) {
      return {
        catalog: kind => studies.catalog(kind),
        catalogScript: id => studies.catalogScript(id),
        runBatch: body => idle().then(() => studies.runBatch(body)),
      };
    },
  };
}
