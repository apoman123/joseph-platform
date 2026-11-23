# Licensing report

| Component | License | Obligation / status |
|---|---|---|
| `joseph-terminal` own code | MIT (`joseph-terminal/LICENSE`) | Publishable after secret and license scans pass |
| Lightweight Charts 5.3.0 | Apache-2.0 | Keep `NOTICE` and the tradingview.com attribution link in the status bar; ship the license text |
| Next.js, React, lucide-react and transitive npm deps | MIT / ISC / Apache-2.0 / BSD | `pnpm licenses:check` allowlist; CI fails on anything else |
| Python deps (FastAPI, Pydantic, httpx, websockets, SQLAlchemy, uvicorn, cryptography) | MIT / BSD / Apache-2.0 | Private services; list with `uv run pip-licenses` |
| `protobuf` (both engines) · `types-protobuf` (dev only) | BSD-3-Clause · Apache-2.0 | MEXC Protobuf decoding; no generated code |
| MEXC `.proto` files, vendored in `mexc_proto/` of each engine | Apache-2.0 (`mexcdevelop/websocket-future-proto`) | Unmodified copies with LICENSE and `NOTICE` (commit and hashes); keep both when updating |
| `pyarrow` (market engine) | Apache-2.0 | Reads Lighter's public Parquet archive |
| Execution `venues` extra: eth-account, eth-utils/keys/abi, msgpack, lighter-sdk (+ aiohttp, requests, pycryptodome, ckzg, regex …) | MIT / Apache-2.0 / BSD / PSF. lighter-sdk metadata says UNKNOWN but ships Apache-2.0 (`dist-info/licenses/LICENSE`); its signer is built from lighter-go (Apache-2.0) | Hyperliquid EIP-712 and Lighter signing; `pip-licenses --fail-on="GPL;AGPL" --partial-match` passes |
| `ib_async` 2.1 (execution extra `ibkr`) | BSD-2-Clause (LICENSE file; the PyPI classifier wrongly says "Other/Proprietary") | Pure Python; depends on `aeventkit`, `nest_asyncio`, `tzdata` |
| `shioaji` 1.7 (extra `sinopac` in both engines) | SinoPac proprietary SDK (binary abi3 wheels, no license file) | Not installed by default; governed by SinoPac's API agreement; never redistribute the wheel outside the private images |
| IB Gateway image `ghcr.io/gnzsnz/ib-gateway` (compose profile `ibkr`) | Image scripts MIT; IB Gateway proprietary (IBKR); IBC GPL-3.0 | Runs unmodified in its own container, not linked with Joseph; IBKR terms apply |
| TradingView Advanced Charts / Trading Platform | Proprietary | **Not used.** No code, assets or styles copied |
| `apoman123/PineScripts` incl. `python_indicators/` (`joseph-smc-indicators`) | Private (owner's) | Pinned git dependency of the market engine; never vendored here or in the public terminal. Plugins report `PINE_PORT_PENDING_REVIEW` |
| `smc_premium_discount_ote_zones.pine` and its port `smc_indicators/premium_discount_ote.py` | LuxAlgo, CC BY-NC-SA 4.0 | **Derivative work.** Attribution (© LuxAlgo, licence link, change note) is kept in the module and `python_indicators/LICENSES/`. **NonCommercial:** while the `premium_discount_ote` plugin is enabled, the market engine must not be used or offered commercially. **ShareAlike:** modified versions stay CC BY-NC-SA 4.0. For commercial use, get LuxAlgo's permission or disable the plugin (`PluginRegistry.disable`) and remove it from strategies |
| Exchange and data-provider market data | Venue and provider terms (incl. Alpaca, SinoPac) | Redistributing data through a hosted public terminal needs a per-source terms review |

Before publishing `joseph-terminal`: run `pnpm secrets:scan` and `pnpm licenses:check`, confirm `NOTICE`, and confirm no file references the private engines.
