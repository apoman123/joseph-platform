/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Store that starts work on first subscription and stops
 * after the last one (used for polling backend data per chart).
 */

import {createStore, type ReadableStore} from '@joseph/feature-registry';

/**
 * Creates a lazily started store: `start` runs when the first listener
 * subscribes and its stop function when the last one leaves; a later first
 * subscriber starts it again.
 * @param initial Value before anything has been produced.
 * @param start Starts producing values through `set`; returns a stop
 *     function.
 * @return Readable store; `get` keeps returning the last value while
 *     stopped.
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
      if (subscribers === 1) stop = start(value => void inner.set(value));
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
