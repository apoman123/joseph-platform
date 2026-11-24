/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview 404 page.
 */

import type {ReactElement} from 'react';
import Link from 'next/link';

/**
 * Not-found page.
 * @return Page.
 */
export default function NotFound(): ReactElement {
  return (
    <main className="static-page">
      <h1>This page doesn’t exist</h1>
      <p>The terminal lives at the home page.</p>
      <Link className="j-btn j-btn--primary" href="/">
        Open the terminal
      </Link>
    </main>
  );
}
