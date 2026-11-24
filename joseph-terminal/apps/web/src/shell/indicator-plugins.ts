/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Catalogue of the indicator plugins the engine (or DEMO)
 * publishes: their inputs, AOI (area of interest) kinds, events, values and
 * display hints. Loaded once per runtime and shared by the Layers menu, the
 * settings dialog, the chart studies and the strategy builder.
 */

'use client';
import type {IndicatorPluginInfo} from '@joseph/contracts';
import {createStore, type ReadableStore} from '@joseph/feature-registry';
import type {TerminalRuntime} from '../composition/runtime';
import {useStore} from './react-bindings';
import {useRuntime} from './runtime-context';

/** Plugin list requests in flight or answered, per runtime. */
const loads = new WeakMap<
  TerminalRuntime,
  Promise<readonly IndicatorPluginInfo[]>
>();

/** Plugin list stores, per runtime. */
const stores = new WeakMap<
  TerminalRuntime,
  ReadableStore<readonly IndicatorPluginInfo[] | null>
>();

/**
 * Loads the plugin descriptors once per runtime; a failed request is
 * forgotten, so the next call retries.
 * @param runtime Runtime whose market client is asked.
 * @return Descriptors in the engine's registration order.
 */
export function loadIndicatorPlugins(
  runtime: TerminalRuntime
): Promise<readonly IndicatorPluginInfo[]> {
  let promise = loads.get(runtime);
  if (!promise) {
    promise = runtime.clients.market
      .listIndicatorPlugins()
      .then(list => list.plugins);
    promise.catch(() => void loads.delete(runtime));
    loads.set(runtime, promise);
  }
  return promise;
}

/**
 * Returns the shared plugin catalogue of a runtime. The first call starts
 * the request; while the list is missing (not answered yet or failed),
 * every new subscription asks again.
 * @param runtime Runtime whose market client is asked.
 * @return Store of the descriptors in registration order; null until the
 *     engine answers.
 */
export function indicatorPlugins(
  runtime: TerminalRuntime
): ReadableStore<readonly IndicatorPluginInfo[] | null> {
  const existing = stores.get(runtime);
  if (existing) return existing;
  const inner = createStore<readonly IndicatorPluginInfo[] | null>(null);
  /** Requests the list unless it has arrived. */
  function request(): void {
    if (inner.get() !== null) return;
    loadIndicatorPlugins(runtime).then(
      plugins => void inner.set(plugins),
      () => undefined
    );
  }
  const store: ReadableStore<readonly IndicatorPluginInfo[] | null> = {
    get: () => inner.get(),
    subscribe(listener) {
      request();
      return inner.subscribe(listener);
    },
  };
  stores.set(runtime, store);
  request();
  return store;
}

/**
 * Subscribes a component to the plugin catalogue of the current runtime.
 * @return Descriptors in registration order; null until the engine answers.
 */
export function useIndicatorPlugins(): readonly IndicatorPluginInfo[] | null {
  return useStore(indicatorPlugins(useRuntime()));
}
