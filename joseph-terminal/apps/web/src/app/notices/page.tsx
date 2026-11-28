/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Third-party notices page (Apache-2.0 attribution for
 * Lightweight Charts, TradeX and the integrated AGPL license).
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
        Joseph Terminal is open-source software released under the AGPL-3.0-only
        License. It is not affiliated with or endorsed by TradingView or any
        exchange.
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
        Drawing geometry uses{' '}
        <a href="https://github.com/deepentropy/lightweight-charts-drawing">
          lightweight-charts-drawing by deepentropy
        </a>{' '}
        (MIT, version 0.6.2). No TradingView Advanced Charts or Trading Platform
        code or assets are included.
      </p>
      <h2>TradeX integration</h2>
      <p>
        Based on TradeX by Saifullah Shafin -{' '}
        <a href="https://github.com/saifullahshafin/open-source-tradingview">
          https://github.com/saifullahshafin/open-source-tradingview
        </a>
      </p>
      <p>
        Modified from TradeX. Joseph retains its own architecture and Python
        script runtime.
      </p>
      <p>
        <a href="https://github.com/apoman123/joseph-platform">
          Corresponding source and build instructions (AGPL)
        </a>
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
