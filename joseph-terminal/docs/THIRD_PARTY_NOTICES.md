# Third-party notices

The third-party software and assets that Joseph Terminal ships, with their licences. For anyone who redistributes the terminal; the terminal itself is MIT licensed (`LICENSE`, `NOTICE`).

| Component | License | Notes |
|---|---|---|
| `lightweight-charts` (TradingView Lightweight Charts™) | Apache-2.0 | Copyright (c) 2025 TradingView, Inc. Attribution shown in the status bar, on `/notices` and in `NOTICE`. Only the public API is used, from `packages/chart-core/src/adapters/lwc`. |
| `react`, `react-dom` | MIT | |
| `next` | MIT | |
| `lucide-react` | ISC | Icons. |
| Archivo font (Google Fonts, self-hosted by `next/font`) | SIL OFL 1.1 | Font files are downloaded at build time. |
| `@img/sharp-libvips-*` (optional, through Next.js) | LGPL-3.0-or-later | Server-side image optimizer for `next/image`, dynamically linked; never in the browser bundle. Reviewed exception in `scripts/license-check.mjs`. |

`pnpm licenses:check` fails the build if any other production dependency falls outside the allowlist (MIT, ISC, Apache-2.0, BSD-2/3-Clause, 0BSD, CC0-1.0, BlueOak-1.0.0, Unlicense, Python-2.0, CC-BY-4.0), or if this file or `NOTICE` stops crediting Lightweight Charts.

Not included, by policy: TradingView Advanced Charts / Trading Platform code or assets, and the private `apoman123/PineScripts` sources.
