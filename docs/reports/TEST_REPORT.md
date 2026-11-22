# Test report — 2026-11-22

All suites below were run on the final build (Linux, Python 3.13 via uv, Node 24, pnpm 10.34, Chromium 1243 via Playwright 1.63). Every number is from an actual run; nothing is estimated.

## Summary

| Tier | Project | Result |
|---|---|---|
| Unit, integration, contract, architecture, property, replay, in-process system | Market engine | **1,043 passed**, 10 skipped (9 opt-in network tests, 1 Pine parity test awaiting TradingView exports) · 96% line coverage |
| Unit, scenario, property (batch == streaming, no look-ahead), code standards | `smc_indicators` (PineScripts `python_indicators/`) | **306 passed** · ruff, `mypy --strict` clean |
| Live venue (opt-in, real public APIs) | Market engine | **9 passed**: Binance, Hyperliquid, Lighter, Bitget and MEXC |
| Unit, integration, contract, architecture, security, chaos, property, in-process system | Execution engine | **533 passed** · 95% line coverage |
| Unit, component, contract, architecture (Vitest) | Terminal | **398 passed** (56 files) · 78% line coverage (the canvas chart adapter is covered by the browser tiers) |
| Browser system, DEMO mode (Playwright + axe) | Terminal | **27 passed** (incl. panes, structure, settings, compare, replay, drawings + style editor + object list, alert triage, resizing) |
| Full-stack browser: production build + both real engines | All three | **6 passed** (incl. Delta % series and `market_structure` output from the real engine) |
| Cross-service system (real processes, HTTP + WebSocket) | Both engines | **26 passed** |

## Static checks

| Check | Result |
|---|---|
| ruff format + ruff (Google docstrings) on src, tests and scripts | Clean in both engines and system-tests |
| `mypy --strict` | 141 files (market), 76 files (execution), 13 files (`smc_indicators`): no issues |
| Google style tests (docstring on every def/class, module-only imports) | Pass (part of the architecture tier) |
| import-linter | Market 8/8 contracts kept (incl. "indicator adapters stay thin"); execution 7/7 kept |
| ESLint (Google TS rules, JSDoc everywhere), Prettier, `tsc` per package | 0 problems |
| Contracts drift (`contracts:check`), OpenAPI drift (`export_openapi.py --check`) | Up to date |
| dependency-cruiser | 268 modules, 0 violations |
| Secret scan | Pass |
| Licenses | 65 production npm packages on the allowlist (1 reviewed exception: `@img/sharp-libvips`, LGPL, server-only via Next.js); Python dependencies have no GPL/AGPL |
| Production build | `next build` succeeds; 103 kB shared first-load JS |

## CI/CD and DevOps verification

| Check | Result |
|---|---|
| `actionlint` on all workflows (`ci-cd`, `security`, `nightly`, `redeploy`, standalone terminal CI) and PineScripts `python-indicators` | No issues (fixed an unquoted comma in the deploy action's input description) |
| Market-engine image with the private indicator library | Builds with only a forwarded ssh-agent (`docker build --ssh default`); runtime image loads all 8 plugins, runs as uid 10001, contains no git, ssh client or key |
| Indicator performance | 5,000 5m frames through all 8 plugins (1m intrabars, 1h/4h/1d aux): 8.6 s (1.7 ms/frame); 18.5 s under coverage tracing |
| Clean-checkout rehearsal: a fresh copy holding only the 706 committable files, frozen installs, then the CI command sequence | All green on a cold first run (it exposed a cold-start fixture flake, now fixed) |
| Container images built from the Dockerfiles | market 285 MB, execution 303 MB, terminal 412 MB; all run as non-root with health checks |
| Compose smoke test of the three containers | All healthy. Over the container network: paper market order FILLED, audit chain valid, unauthenticated request → 401 |
| gitleaks on committable files | No leaks (one deliberate fake credential in a redaction test is allowlisted) |
| Dependency audits | npm: no known vulnerabilities after overriding PostCSS to 8.5.33 (fixes 2 high and 2 moderate advisories inherited from Next.js). Python: none after upgrading `cryptography` 46.0.7 → 50.0.2 (fixes 3 advisories) |

## What the higher tiers prove

- **Indicators = Pine:** golden scenarios re-derived by hand from the Pine source pass (e.g. a structure break needs both pivots known; an FVG closed through inverts rather than invalidates; the OTE band of a continued leg is [102.682, 103.396]). The adapters publish exactly the library's zones and events, survive a failing bar without corrupting earlier states, and checkpoints restore through an allowlisted unpickler.
- **Signal chain:** an always-true 1m PAPER strategy, activated live, produces signals only for bars closed after activation. The forwarder delivers each entry to the execution engine, which creates exactly one sized paper order (plus bracket legs).
- **Trading safety:**
  - A repeated idempotency key replays the original result; the same key with a different body returns 409.
  - The kill switch blocks opening orders.
  - A LIVE order fails closed with `LIVE_TRADING_DISABLED`.
  - A limit order outside the price band is rejected by `size.price_band`.
  - Tenant B can neither see nor cancel tenant A's orders.
  - The audit hash chain verifies.
  - A lost acknowledgement (ACK loss) never causes a duplicate submit (chaos tier).
- **Data integrity:**
  - Contract validation runs on every API response.
  - A venue that is not enabled shows an error in the browser, never synthetic prices.
  - Batch, incremental and checkpoint-restore indicator runs agree, and adding future bars never changes past results (property tier).
- **Accessibility and security:** the axe scan reports no serious or critical violations, and CSP and security headers are verified in the browser.
- **Performance:** 200 candle requests had p50 2.6 ms and p95 3.0 ms. Five thousand 5m bars plus intrabars ran through every plugin in 3.9 s.

## Defects found by these tests and fixed

| Defect | Found by | Fix |
|---|---|---|
| Indicator engine could pick up a higher-timeframe snapshot from the future while one series stalled (look-ahead) | Property test | Per-series snapshot history; a frame reads the latest snapshot at or before its close |
| Dealing range inverted (high < low) on stale anchors | Hypothesis | Stale anchors no longer reset the range |
| `useStore` gave React a new subscribe function every render, causing an endless fetch/render loop | Browser test | Stable subscribe function per store |
| Control envelopes could make clients drop real candles when deduplicating by sequence | Integration test | Control envelopes carry sequence 0 |
| DEMO broker marked a reduce-only close FILLED after its first partial fill, leaving a stray position | Browser test, then a deterministic unit test | FILLED only when fully filled; remainder canceled once flat |
| Strategy cards squeezed their sentence to one word per line in the side panel | Browser test + screenshot review | The summary gets its own full-width row |
| Small icon buttons rendered 6 px glyphs (shared CSS padding) | Screenshot review | Icon buttons drop their padding and SVGs never shrink |
| Order-ticket confirm label clipped; pressed Buy/Sell label invisible | Screenshot review | Full-width confirm row; pressed side buttons keep dark text |
| Instrument search failed when the chart's venue was not enabled on the backend | Full-stack test | Falls back to all available venues |
| Web component tests resolved React from the wrong root | Integration | Vitest `web` project root set to `apps/web` |
| Vulnerable `cryptography` (vault crypto) and PostCSS versions | pip-audit / pnpm audit | Upgraded / overridden; audits now block CI |
| Process-test fixtures could deadlock on an unread stdout pipe and hid server logs on a cold start | Clean-checkout CI rehearsal | Log to a file, detect early exit, 60 s cold-start budget |

## Reproduce

```bash
make ci                                      # lint, types and every offline tier
make test-system                             # cross-service
cd joseph-terminal && pnpm test:e2e:fullstack
make test-network                            # live venues (needs internet)
```
