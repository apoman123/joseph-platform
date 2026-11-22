/**
 * @fileoverview Store that starts work on first subscription and stops
 * after the last one (used for polling backend data per chart).
 */
import {createStore, type ReadableStore} from '@joseph/feature-registry';

/**
 * Creates a lazily started store.
 * @param initial Initial value.
 * @param start Starts producing values; returns a stop function.
 * @return Readable store.
 */
export function createLazyStore<T>(
  initial: T,
  start: (set: (value: T) => void) => () => void
): ReadableStore<T> {
  const inner = createStore<T>(initial);
  let subscribers = 0;
  let stop: (() => void) | null = null;
  return {
    get: () => inner.get(),
    subscribe(listener) {
      subscribers += 1;
      if (subscribers === 1) stop = start(value => inner.set(value));
      const unsubscribe = inner.subscribe(listener);
      return () => {
        unsubscribe();
        subscribers -= 1;
        if (subscribers === 0) {
          stop?.();
          stop = null;
        }
      };
    },
  };
}
