/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Data hooks and a derived store shared by the feature panels
 * (trading, portfolio, alerts, screener, strategy, watchlist): error
 * messages, polling, a clock and subscription-scoped derived stores.
 */

'use client';
import {
  createStore,
  type Disposer,
  type ReadableStore,
} from '@joseph/feature-registry';
import {useCallback, useEffect, useRef, useState} from 'react';

/** State of an async resource. */
export interface AsyncState<T> {
  /** Last successful value, kept when a later load fails; null before. */
  data: T | null;
  /** Message of the last failed load, cleared by the next success. */
  error: string | null;
  /** True until the first load settles. */
  loading: boolean;
  /** Reloads now and restarts the interval. */
  refresh: () => void;
}

/**
 * Converts an unknown error into a user-facing message.
 * @param err Thrown value.
 * @return Message.
 */
export function errorMessage(err: unknown): string {
  // Prefer the server's message from an `ApiError`-like body, read without
  // trusting its shape.
  if (err && typeof err === 'object' && 'body' in err) {
    const body = (err as {body?: {message?: unknown}}).body;
    if (body && typeof body.message === 'string') return body.message;
  }
  if (err instanceof Error) return err.message;
  return 'The request failed. Check the backend connection and try again.';
}

/**
 * Loads a value and optionally refreshes it on an interval. Results that
 * arrive after the key changed or the component unmounted are dropped.
 * @param load Loader; the latest one is called on every run.
 * @param key Describes the loader's inputs; a new key reloads at once.
 * @param intervalMs Refresh interval in milliseconds, or 0 to load once.
 * @return Async state.
 */
export function usePolling<T>(
  load: () => Promise<T>,
  key: string,
  intervalMs = 0
): AsyncState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  // `load` is usually a new closure every render; a ref lets each run call
  // the latest one without restarting the effect.
  const loader = useRef(load);
  loader.current = load;
  const [tick, setTick] = useState(0);
  const refresh = useCallback(() => void setTick(t => t + 1), []);
  useEffect(() => {
    let alive = true;
    const run = () => {
      loader.current().then(
        value => {
          if (!alive) return;
          setData(value);
          setError(null);
          setLoading(false);
        },
        (err: unknown) => {
          if (!alive) return;
          setError(errorMessage(err));
          setLoading(false);
        }
      );
    };
    run();
    const timer = intervalMs > 0 ? setInterval(run, intervalMs) : null;
    return () => {
      alive = false;
      if (timer) clearInterval(timer);
    };
  }, [key, intervalMs, tick]);
  return {data, error, loading, refresh};
}

/**
 * Creates a store derived from source stores that subscribes to its sources
 * only while it has subscribers (so per-chart layer stores never leak).
 * @param sources Source stores.
 * @param compute Pure function of the sources' current values.
 * @return Derived read-only store.
 */
export function derived<T>(
  sources: ReadonlyArray<ReadableStore<unknown>>,
  compute: () => T
): ReadableStore<T> {
  const inner = createStore<T>(compute());
  let attached: Disposer[] = [];
  let subscribers = 0;
  return {
    get() {
      return subscribers > 0 ? inner.get() : compute();
    },
    subscribe(listener) {
      if (subscribers === 0) {
        inner.set(compute());
        attached = sources.map(source =>
          source.subscribe(() => void inner.set(compute()))
        );
      }
      subscribers += 1;
      const off = inner.subscribe(listener);
      return () => {
        off();
        subscribers -= 1;
        if (subscribers === 0) {
          for (const dispose of attached) {
            dispose();
          }
          attached = [];
        }
      };
    },
  };
}

/**
 * Re-renders every `intervalMs` (for countdowns and data ages).
 * @param intervalMs Interval in milliseconds.
 * @return Current time (UTC ms).
 */
export function useNow(intervalMs = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => void setNow(Date.now()), intervalMs);
    return () => void clearInterval(timer);
  }, [intervalMs]);
  return now;
}
