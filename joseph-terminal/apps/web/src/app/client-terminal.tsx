/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Client-only loader for the terminal (no server rendering:
 * it depends on canvas, storage and live connections). Next.js accepts
 * `dynamic(..., {ssr: false})` only in a client component, hence this file.
 */

'use client';
import type {ReactElement} from 'react';
import dynamic from 'next/dynamic';

/** Lazily loaded terminal; a boot placeholder shows while its code loads. */
const TerminalApp = dynamic(
  () => import('../shell/terminal-app').then(module => module.TerminalApp),
  {
    ssr: false,
    loading: () => (
      <div className="boot" role="status">
        <span className="brand__mark" aria-hidden="true">
          J
        </span>
        <span>Opening the terminal…</span>
      </div>
    ),
  }
);

/**
 * Renders the terminal on the client.
 * @return Terminal.
 */
export function ClientTerminal(): ReactElement {
  return <TerminalApp />;
}
