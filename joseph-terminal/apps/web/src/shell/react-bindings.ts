/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview React bindings for the framework-free feature registry.
 *
 * Features contribute `WebView` components to slots; every view receives
 * the feature's own `FeatureContext`, so it can only reach injected clients,
 * stores and registries.
 */

import type {
  FeatureContext,
  ReadableStore,
  TerminalFeature,
} from '@joseph/feature-registry';
import {type ComponentType, useCallback, useSyncExternalStore} from 'react';

/** Props passed to every slot view. */
export interface WebSlotProps {
  /** Context of the feature that contributed the view. */
  ctx: WebFeatureContext;
}

/** Component type hosted in UI slots. */
export type WebView = ComponentType<WebSlotProps>;

/** Feature context specialized for React views. */
export type WebFeatureContext = FeatureContext<WebView>;

/** Terminal feature specialized for React views. */
export type WebFeature = TerminalFeature<WebView>;

/**
 * Subscribes a component to a store; the component re-renders when the
 * value changes.
 * @param store Observable store.
 * @return The current value.
 */
export function useStore<T>(store: ReadableStore<T>): T {
  // A stable subscribe function per store: React re-subscribes whenever
  // this identity changes, which would restart lazily started stores.
  const subscribe = useCallback(
    (listener: () => void) => store.subscribe(() => void listener()),
    [store]
  );
  return useSyncExternalStore(
    subscribe,
    () => store.get(),
    () => store.get()
  );
}
