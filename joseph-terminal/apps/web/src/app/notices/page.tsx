/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Third-party notices page (Apache-2.0 attribution for
 * Lightweight Charts and the terminal's MIT license).
 */

import type {ReactElement} from 'react';
import Link from 'next/link';

/**
 * Renders the licenses and notices page.
 * @return Page.
 */
export default function NoticesPage(): ReactElement {
  return (
    <main className="static-page">
      <h1>Licenses and notices</h1>
      <p>
        Joseph Terminal is open-source software released under the MIT License.
        It is not affiliated with or endorsed by TradingView or any exchange.
      </p>
      <h2>TradingView Lightweight Charts™</h2>
      <p>
        Charts are rendered with TradingView Lightweight Charts™, Copyright (c)
        2025 TradingView, Inc., licensed under the Apache License, Version 2.0.{' '}
        <a href="https://www.tradingview.com/" target="_blank" rel="noreferrer">
          https://www.tradingview.com/
        </a>
      </p>
      <p>
        Every drawing tool, indicator layer, layout and trading interaction in
        this terminal is original code built on the public Lightweight Charts
        API. No TradingView Advanced Charts or Trading Platform code or assets
        are included.
      </p>
      <h2>Other components</h2>
      <p>
        React, Next.js and lucide-react are MIT or ISC licensed. The full list
        ships in docs/THIRD_PARTY_NOTICES.md.
      </p>
      <Link className="j-btn" href="/">
        Back to the terminal
      </Link>
    </main>
  );
}
