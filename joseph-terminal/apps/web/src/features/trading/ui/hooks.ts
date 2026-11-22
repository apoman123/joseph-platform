'use client';
/**
 * @fileoverview Data hooks and lazy stores shared by the F6 features.
 */
import {
  createStore,
  type Disposer,
  type ReadableStore,
} from '@joseph/feature-registry';
import {useCallback, useEffect, useRef, useState} from 'react';

/** State of an async resource. */
export interface AsyncState<T> {
  /** Last successful value. */
  data: T | null;
  /** Last error message. */
  error: string | null;
  /** True until the first load settles. */
  loading: boolean;
  /** Forces a reload. */
  refresh: () => void;
}

/**
 * Converts an unknown error into a user-facing message.
 * @param err Thrown value.
 * @return Message.
 */
export function errorMessage(err: unknown): string {
  if (err && typeof err === 'object' && 'body' in err) {
    const body = (err as {body?: {message?: unknown}}).body;
    if (body && typeof body.message === 'string') return body.message;
  }
  if (err instanceof Error) return err.message;
  return 'The request failed. Check the backend connection and try again.';
}

/**
 * Loads a value and optionally refreshes it on an interval.
 * @param load Loader; re-run when `key` changes.
 * @param key Cache key describing the loader's inputs.
 * @param intervalMs Refresh interval, or 0 to load once.
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
  const loader = useRef(load);
  loader.current = load;
  const [tick, setTick] = useState(0);
  const refresh = useCallback(() => setTick(t => t + 1), []);
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
  sources: readonly ReadableStore<unknown>[],
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
          source.subscribe(() => inner.set(compute()))
        );
      }
      subscribers += 1;
      const off = inner.subscribe(listener);
      return () => {
        off();
        subscribers -= 1;
        if (subscribers === 0) {
          for (const dispose of attached) dispose();
          attached = [];
        }
      };
    },
  };
}

/**
 * Re-renders every `intervalMs` (for countdowns and data ages).
 * @param intervalMs Interval.
 * @return Current time (UTC ms).
 */
export function useNow(intervalMs = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(timer);
  }, [intervalMs]);
  return now;
}
