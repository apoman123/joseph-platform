/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Client-only loader for pop-out windows (no server
 * rendering: they depend on canvas, storage, BroadcastChannel and live
 * connections). Next.js accepts `dynamic(..., {ssr: false})` only in a
 * client component, hence this file.
 */

'use client';
import type {ReactElement} from 'react';
import dynamic from 'next/dynamic';

/** Lazily loaded pop-out window; a placeholder shows while its code loads. */
const PopoutApp = dynamic(
  () => import('../../shell/popout-app').then(module => module.PopoutApp),
  {
    ssr: false,
    loading: () => (
      <div className="boot" role="status">
        <span className="brand__mark" aria-hidden="true">
          J
        </span>
        <span>Opening the window…</span>
      </div>
    ),
  }
);

/**
 * Renders the pop-out window on the client.
 * @return Pop-out window.
 */
export function ClientPopout(): ReactElement {
  return <PopoutApp />;
}
