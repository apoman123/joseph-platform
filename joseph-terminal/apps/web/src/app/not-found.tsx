/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Not-found (404) page with a link back to the terminal.
 */

import type {ReactElement} from 'react';
import Link from 'next/link';

/**
 * Renders the not-found page.
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
