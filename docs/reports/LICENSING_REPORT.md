# Licensing report

The licences that cover this repository and its dependencies, and the obligations they create. For anyone who uses, redistributes or hosts Joseph.

## This repository

- **MIT:** the root [`LICENSE`](../../LICENSE) covers the whole repository; identical copies sit in both engines and ship in their images.
- **Terminal:** `joseph-terminal` keeps its own MIT [`LICENSE`](../../joseph-terminal/LICENSE) and [`NOTICE`](../../joseph-terminal/NOTICE) so it can be split into its own repository.
- **Not covered:** the private `apoman123/PineScripts` library. The market engine installs it at build time, so the market-engine image contains it; CI therefore publishes both engine images only as private packages (either may also contain SinoPac's SDK).
- **Checks:** on every push, `ci-cd.yml` runs `pnpm secrets:scan`, `pnpm licenses:check` (which also fails if `NOTICE` or `THIRD_PARTY_NOTICES.md` stops crediting Lightweight Charts) and dependency-cruiser (no browser code imports engine paths). Run them again before splitting the terminal out.

## LuxAlgo-derived indicator (CC BY-NC-SA 4.0)

One optional indicator in the external library (its Pine script and Python port) is a **derivative work** of a LuxAlgo script licensed under CC BY-NC-SA 4.0. Its plugin's `description` in `GET /v1/indicators/plugins` says "Derived from LuxAlgo (CC BY-NC-SA 4.0)".

- **Attribution:** © LuxAlgo, the licence link and a change note are kept in the library module and in `python_indicators/LICENSES/`.
- **NonCommercial:** while that plugin is enabled, the market engine must not be used or offered commercially.
- **ShareAlike:** modified versions stay CC BY-NC-SA 4.0.
- **Commercial use:** get LuxAlgo's permission, or disable the plugin (`PluginRegistry.disable`) and remove it from strategies.

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
| `apoman123/PineScripts` including `python_indicators/` | Private | Pinned git dependency of the market engine; never vendored in this repository. Plugins report `PINE_PORT_PENDING_REVIEW` |
| Exchange and data-provider market data | Venue and provider terms (including Alpaca, SinoPac) | Redistributing data through a hosted public terminal needs a per-source terms review |
