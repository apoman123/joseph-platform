# Licensing report

| Component | License | Obligation / status |
|---|---|---|
| `joseph-terminal` own code | MIT (`joseph-terminal/LICENSE`) | Publishable after secret and license scans pass |
| Lightweight Charts 5.3.0 | Apache-2.0 | Keep `NOTICE` and the attribution link to tradingview.com in the status bar; ship the license text with distributions |
| Next.js, React, lucide-react and transitive npm deps | MIT / ISC / Apache-2.0 / BSD family | Checked by `pnpm licenses:check` (allowlist; CI fails on anything else) |
| Python deps (FastAPI, Pydantic, httpx, websockets, SQLAlchemy, uvicorn, cryptography) | MIT / BSD / Apache-2.0 | Private services; list with `uv run pip-licenses` |
| `ib_async` 2.1 (optional extra `ibkr`) | BSD-2-Clause (LICENSE file; the PyPI classifier wrongly says "Other/Proprietary") | Execution engine only; pure Python, depends on `aeventkit`, `nest_asyncio`, `tzdata` |
| `shioaji` 1.7 (optional extra `sinopac`) | SinoPac proprietary SDK (binary abi3 wheels, no license metadata or file) | Not installed by default; use is governed by SinoPac's API agreement; never redistribute the wheel outside the private image |
| IB Gateway image `ghcr.io/gnzsnz/ib-gateway` (compose profile `ibkr`) | Image scripts MIT; IB Gateway IBKR proprietary; IBC GPL-3.0 | Runs unmodified in its own container (no linking with Joseph code); IBKR software terms apply |
| TradingView Advanced Charts / Trading Platform | Proprietary | **Not used.** No code, assets or styles copied |
| `apoman123/PineScripts` incl. `python_indicators/` (`joseph-smc-indicators`) | Private (owner's) | Indicator library; installed into the market engine as a pinned git dependency, never vendored into this repo or the public terminal. Plugins report `PINE_PORT_PENDING_REVIEW` |
| `smc_premium_discount_ote_zones.pine` and its port `smc_indicators/premium_discount_ote.py` | LuxAlgo, CC BY-NC-SA 4.0 | **The port is a derivative work.** Attribution (© LuxAlgo, licence link, change note) is kept in the module and in `python_indicators/LICENSES/`. **NonCommercial:** while the `premium_discount_ote` plugin is enabled, the market engine must not be used or offered commercially. **ShareAlike:** modified versions of that module must stay CC BY-NC-SA 4.0. For commercial use, get permission from LuxAlgo, or disable the plugin (`PluginRegistry.disable`) and remove it from strategies |
| Exchange market data | Venue terms of service | Redistributing venue data through a hosted public terminal needs per-venue ToS review |

Before publishing `joseph-terminal`: run `pnpm secrets:scan`, `pnpm licenses:check`, confirm `NOTICE`, and confirm no file references the private engines.
