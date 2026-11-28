/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview React context exposing the terminal runtime to shell views.
 */

'use client';
import {
  createContext,
  useContext,
  type ReactNode,
  type ReactElement,
} from 'react';
import type {TerminalRuntime} from '../composition/runtime';

/** Runtime context; null outside a `RuntimeProvider`. */
const RuntimeContext = createContext<TerminalRuntime | null>(null);

/**
 * Provides the runtime to the shell views below it.
 * @param props Runtime and children.
 * @return Provider element.
 */
export function RuntimeProvider({
  runtime,
  children,
}: {
  runtime: TerminalRuntime;
  children: ReactNode;
}): ReactElement {
  return (
    <RuntimeContext.Provider value={runtime}>
      {children}
    </RuntimeContext.Provider>
  );
}

/**
 * Returns the runtime of the nearest `RuntimeProvider`.
 * @return Runtime.
 * @throws `Error` when called outside a `RuntimeProvider`.
 */
export function useRuntime(): TerminalRuntime {
  const runtime = useContext(RuntimeContext);
  if (!runtime) {
    throw new Error('useRuntime must be used inside RuntimeProvider');
  }
  return runtime;
}

/**
 * Returns the runtime of the nearest `RuntimeProvider`, if any.
 * @return Runtime, or null outside a provider (isolated feature tests).
 */
export function useOptionalRuntime(): TerminalRuntime | null {
  return useContext(RuntimeContext);
}
