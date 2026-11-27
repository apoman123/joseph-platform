# Third-party notices

The third-party software and assets that Joseph Terminal ships, with their licences. For anyone who redistributes the terminal; the integrated terminal is AGPL-3.0-only licensed (`LICENSE`, `NOTICE`).

| Component | License | Notes |
|---|---|---|
| `lightweight-charts` (TradingView Lightweight Charts™) | Apache-2.0 | Copyright (c) 2025 TradingView, Inc. Attribution shown in the status bar, on `/notices` and in `NOTICE`. Only the public API is used, from `packages/chart-core/src/adapters/lwc`. |
| `react`, `react-dom` | MIT | |
| `next` | MIT | |
| `lucide-react` | ISC | Icons. |
| Archivo font (Google Fonts, self-hosted by `next/font`) | SIL OFL 1.1 | Font files are downloaded at build time. |
| `@img/sharp-libvips-*` (optional, through Next.js) | LGPL-3.0-or-later | Server-side image optimizer for `next/image`, dynamically linked; never in the browser bundle. Reviewed exception in `scripts/license-check.mjs`. |

`pnpm licenses:check` fails the build if any other production dependency falls outside the allowlist (MIT, ISC, Apache-2.0, BSD-2/3-Clause, 0BSD, CC0-1.0, BlueOak-1.0.0, Unlicense, Python-2.0, CC-BY-4.0), or if this file or `NOTICE` stops crediting Lightweight Charts.

Not included, by policy: TradingView Advanced Charts / Trading Platform code or assets, and the sources of third-party indicator plugin packages.

## TradeX integration

Modified from [TradeX by Saifullah Shafin](https://github.com/saifullahshafin/open-source-tradingview),
commit `f8532c31af5060ee4dbafcce11fef0ea8f984aeb` (AGPL-3.0-only with the additional
attribution terms in `third_party/tradex/NOTICE`). The upstream snapshot, original
notices, license texts and SHA-256 inventory are in `third_party/tradex/`.
The modified application uses Joseph branding; upstream trademarks are not granted.

The drawing bridge uses `lightweight-charts-drawing@0.6.2` (MIT), the same version
pinned by that upstream snapshot. Its renderer-neutral scene, placement, drag and
hit-test core supplies 86 tools. Joseph owns documents, command history, locking,
visibility, persistence, permissions and the Python-authoritative trading pipeline.

The reference snapshot contains upstream Pine source and bundles for provenance.
They are not imported, served or executed by Joseph. No Pine editor, PineTS runtime,
TradingView script scraping, Supabase account service, upstream installer or SSH
restart endpoint is enabled. Python remains the only user script language.

Original MIT-licensed Joseph files retain their individual notices. Their prior
license is preserved in `licenses/MIT-joseph-original.txt`. Distribution of this
integrated application follows the root AGPL license. Source and build instructions
are available at https://github.com/apoman123/joseph-platform.
