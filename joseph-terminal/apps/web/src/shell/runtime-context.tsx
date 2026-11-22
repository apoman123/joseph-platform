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

/** Runtime context. */
const RuntimeContext = createContext<TerminalRuntime | null>(null);

/**
 * Provides the runtime.
 * @param props Runtime and children.
 * @return Provider.
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
 * Reads the runtime.
 * @return Runtime.
 * @throws {Error} Outside a provider.
 */
export function useRuntime(): TerminalRuntime {
  const runtime = useContext(RuntimeContext);
  if (!runtime)
    throw new Error('useRuntime must be used inside RuntimeProvider');
  return runtime;
}
