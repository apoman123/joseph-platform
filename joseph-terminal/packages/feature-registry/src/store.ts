/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Minimal observable store shared across features.
 *
 * Framework-free; the web app adapts it to React with
 * `useSyncExternalStore`.
 */

/** Removes a subscription or registration. */
export type Disposer = () => void;

/** Read-only observable value. */
export interface ReadableStore<T> {
  /**
   * Returns the current value.
   * @return Current value.
   */
  get(): T;
  /**
   * Subscribes to changes (not invoked immediately).
   * @param listener Called with the new value after each change.
   * @return Disposer.
   */
  subscribe(listener: (value: T) => void): Disposer;
}

/** Mutable observable value. */
export interface WritableStore<T> extends ReadableStore<T> {
  /**
   * Replaces the value; listeners run only when it changes (Object.is).
   * @param value New value.
   */
  set(value: T): void;
  /**
   * Derives the next value from the current one.
   * @param updater Pure update function.
   */
  update(updater: (current: T) => T): void;
}

/**
 * Creates a writable store.
 * @param initial Initial value.
 * @return The store.
 */
export function createStore<T>(initial: T): WritableStore<T> {
  let value = initial;
  const listeners = new Set<(value: T) => void>();
  return {
    get: () => value,
    subscribe(listener) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    set(next) {
      if (Object.is(next, value)) return;
      value = next;
      for (const listener of [...listeners]) {
        listener(value);
      }
    },
    update(updater) {
      this.set(updater(value));
    },
  };
}
