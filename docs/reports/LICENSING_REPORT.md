# Licensing report

The licences that cover this repository and its dependencies, and the obligations they create. For anyone who uses, redistributes or hosts Joseph.

## This repository

- **MIT:** the root [`LICENSE`](../../LICENSE) covers the whole repository; identical copies sit in both engines and ship in their images.
- **Terminal:** `joseph-terminal` keeps its own MIT [`LICENSE`](../../joseph-terminal/LICENSE) and [`NOTICE`](../../joseph-terminal/NOTICE) so it can be split into its own repository.
- **Built-in indicators:** MIT like the rest of the repository. They implement the published definitions of TradingView's built-in functions without copying TradingView or Pine code, and report licensing status `CLEAN_ROOM_FROM_SPEC` in `GET /v1/indicators/plugins`.
- **Not covered:** optional third-party indicator plugin packages, installed by operators at build time through `PLUGIN_REQUIREMENTS`, are not part of this repository and keep their own licences. A market image that contains one is subject to that licence; CI publishes engine images only as private packages (either may also contain SinoPac's SDK), and `MARKET_IMAGE_LICENSES` sets the market image's licence label.
- **Checks:** on every push, `ci-cd.yml` runs `pnpm secrets:scan`, `pnpm licenses:check` (which also fails if `NOTICE` or `THIRD_PARTY_NOTICES.md` stops crediting Lightweight Charts) and dependency-cruiser (no browser code imports engine paths). Run them again before splitting the terminal out.

## Dependencies and data

| Component | Licence | Obligation / status |
|---|---|---|
| Lightweight Charts 5.3.0 | Apache-2.0 | Keep `NOTICE` and the tradingview.com attribution link in the status bar; ship the licence text |
| Next.js, React, lucide-react and transitive npm dependencies | MIT / ISC / Apache-2.0 / BSD | `pnpm licenses:check` allowlist; CI fails on anything else |
| Python dependencies (FastAPI, Pydantic, httpx, websockets, SQLAlchemy, uvicorn, cryptography) | MIT / BSD / Apache-2.0 | Server-side services; list with `uv run pip-licenses` |
| `protobuf` (both engines) · `types-protobuf` (development only) | BSD-3-Clause · Apache-2.0 | MEXC Protobuf decoding; no generated code |
| MEXC `.proto` files, vendored in `mexc_proto/` of each engine | Apache-2.0 (`mexcdevelop/websocket-future-proto`) | Unmodified copies with LICENSE and `NOTICE` (commit and hashes); keep both when updating |
| `pyarrow` (market engine) | Apache-2.0 | Reads Lighter's public Parquet archive |
| Execution `venues` extra: eth-account, eth-utils/keys/abi, msgpack, lighter-sdk (+ aiohttp, requests, pycryptodome, ckzg, regex …) | MIT / Apache-2.0 / BSD / PSF. lighter-sdk metadata says UNKNOWN but ships Apache-2.0 (`dist-info/licenses/LICENSE`); its signer is built from lighter-go (Apache-2.0) | Hyperliquid EIP-712 and Lighter signing; `pip-licenses --fail-on="GPL;AGPL" --partial-match` passes |
| `ib_async` 2.1 (execution extra `ibkr`) | BSD-2-Clause (LICENSE file; the PyPI classifier wrongly says "Other/Proprietary") | Pure Python; depends on `aeventkit`, `nest_asyncio`, `tzdata` |
| `shioaji` 1.7 (extra `sinopac` in both engines) | SinoPac proprietary SDK (binary abi3 wheels, no licence file) | Not installed by default; governed by SinoPac's API agreement; never redistribute the wheel outside the private images |
| IB Gateway image `ghcr.io/gnzsnz/ib-gateway` (compose profile `ibkr`) | Image scripts MIT; IB Gateway proprietary (IBKR); IBC GPL-3.0 | Runs unmodified in its own container, not linked with Joseph; IBKR terms apply |
| TradingView Advanced Charts / Trading Platform | Proprietary | **Not used.** No code, assets or styles copied |
| Indicator plugin packages (optional) | Their own | Not distributed by this repository |
| Exchange and data-provider market data | Venue and provider terms (including Alpaca, SinoPac) | Redistributing data through a hosted public terminal needs a per-source terms review |
