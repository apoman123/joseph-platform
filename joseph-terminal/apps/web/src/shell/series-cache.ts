/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Recently viewed chart series, per runtime: switching back to
 * a series draws its bars at once while a fresh batch revalidates them, and
 * a pane's first candle request can start before the chart library has
 * loaded (the pane then adopts that request instead of sending another).
 */

import type {ChartBar} from '@joseph/chart-core';
import type {CandleBatch, FeedState, PriceAdjustment} from '@joseph/contracts';

/** Series kept per runtime (least recently used dropped first). */
export const SERIES_CACHE_CAPACITY = 12;
/** A started request is adopted only this soon after it began (5 s). */
const ADOPT_WINDOW_MS = 5_000;

/** A series as a pane last displayed it. */
export interface CachedSeries {
  /** Ascending bars (the pane's array; read, never mutated here). */
  bars: readonly ChartBar[];
  /** Price basis identity (`adjustment:epoch`) the bars share. */
  basis: string;
  /** Price adjustment of the bars, or null when unknown. */
  priceAdjustment: PriceAdjustment | null;
  /** Feed state of the last batch or live update. */
  feedState: FeedState;
  /** When adjusted history must be reloaded (UTC ms), if ever. */
  refreshAfterMs: number | undefined;
  /** When the bars were last updated (UTC ms). */
  savedAtMs: number;
}

/** Started candle request awaiting its pane. */
export interface StartedRequest {
  /** The request. */
  batch: Promise<CandleBatch>;
  /** Cancels the request. */
  controller: AbortController;
  /** When it started (UTC ms). */
  startedAtMs: number;
}

/** LRU of displayed series plus requests started ahead of their pane. */
export class SeriesCache {
  private readonly entries = new Map<string, CachedSeries>();
  private readonly started = new Map<string, StartedRequest>();

  /**
   * Creates a cache.
   * @param capacity Series kept.
   * @param now Clock (UTC ms).
   */
  constructor(
    private readonly capacity = SERIES_CACHE_CAPACITY,
    private readonly now: () => number = Date.now
  ) {}

  /**
   * Returns a series and marks it recently used.
   * @param key Series key (`seriesKey`).
   * @return The cached series, or undefined.
   */
  get(key: string): CachedSeries | undefined {
    const entry = this.entries.get(key);
    if (entry) {
      this.entries.delete(key);
      this.entries.set(key, entry);
    }
    return entry;
  }

  /**
   * Stores a series, dropping the least recently used beyond capacity.
   * @param key Series key.
   * @param entry Series as displayed.
   */
  set(key: string, entry: CachedSeries): void {
    this.entries.delete(key);
    this.entries.set(key, entry);
    while (this.entries.size > this.capacity) {
      const oldest = this.entries.keys().next().value;
      if (oldest === undefined) break;
      this.entries.delete(oldest);
    }
  }

  /**
   * Starts a series' first candle request unless one is already waiting.
   * @param key Series key.
   * @param load Sends the request with a cancellation signal.
   */
  prefetch(
    key: string,
    load: (signal: AbortSignal) => Promise<CandleBatch>
  ): void {
    const waiting = this.started.get(key);
    if (waiting && this.now() - waiting.startedAtMs < ADOPT_WINDOW_MS) return;
    waiting?.controller.abort();
    const controller = new AbortController();
    const batch = load(controller.signal);
    // A request nobody adopts must not surface as an unhandled rejection.
    batch.catch(() => undefined);
    this.started.set(key, {batch, controller, startedAtMs: this.now()});
  }

  /**
   * Takes a request started for a series, if it is recent enough.
   * @param key Series key.
   * @return The request, now owned by the caller, or undefined (the caller
   *     then sends its own).
   */
  adopt(key: string): StartedRequest | undefined {
    const waiting = this.started.get(key);
    this.started.delete(key);
    if (!waiting) return undefined;
    if (this.now() - waiting.startedAtMs >= ADOPT_WINDOW_MS) {
      waiting.controller.abort();
      return undefined;
    }
    return waiting;
  }

  /**
   * Cancels a started request nobody adopted (its series is obsolete).
   * @param key Series key.
   */
  drop(key: string): void {
    this.started.get(key)?.controller.abort();
    this.started.delete(key);
  }
}

/**
 * Builds the cache key of a series.
 * @param instrumentId Canonical instrument id.
 * @param timeframe Timeframe.
 * @param feedMode Feed mode.
 * @return Key.
 */
export function seriesKey(
  instrumentId: string,
  timeframe: string,
  feedMode: string
): string {
  return `${instrumentId}|${timeframe}|${feedMode}`;
}

/** One cache per runtime (tests create many runtimes). */
const CACHES = new WeakMap<object, SeriesCache>();

/**
 * Returns the series cache of a runtime.
 * @param runtime Terminal runtime (any object identifying it).
 * @return Its cache, created on first use.
 */
export function seriesCache(runtime: object): SeriesCache {
  let cache = CACHES.get(runtime);
  if (!cache) {
    cache = new SeriesCache();
    CACHES.set(runtime, cache);
  }
  return cache;
}
