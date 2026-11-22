# Licensing report

| Component | License | Obligation / status |
|---|---|---|
| `joseph-terminal` own code | MIT (`joseph-terminal/LICENSE`) | Publishable after secret and license scans pass |
| Lightweight Charts 5.3.0 | Apache-2.0 | Keep `NOTICE` and the attribution link to tradingview.com in the status bar; ship the license text with distributions |
| Next.js, React, lucide-react and transitive npm deps | MIT / ISC / Apache-2.0 / BSD family | Checked by `pnpm licenses:check` (allowlist; CI fails on anything else) |
| Python deps (FastAPI, Pydantic, httpx, websockets, SQLAlchemy, uvicorn, cryptography) | MIT / BSD / Apache-2.0 | Private services; list with `uv run pip-licenses` |
| TradingView Advanced Charts / Trading Platform | Proprietary | **Not used.** No code, assets or styles copied |
| `apoman123/PineScripts` | Private | Not accessed and not included anywhere (see `PINE_SOURCE_REVISION.md`) |
| `smc_premium_discount_ote_zones.pine` | Contains LuxAlgo CC BY-NC-SA 4.0 notice | Non-commercial + share-alike. The Python plugin is a clean-room functional implementation from the V5 text; obtain legal review or permission before commercial use or publishing derived logic |
| Exchange market data | Venue terms of service | Redistributing venue data through a hosted public terminal needs per-venue ToS review |

Before publishing `joseph-terminal`: run `pnpm secrets:scan`, `pnpm licenses:check`, confirm `NOTICE`, and confirm no file references the private engines.
