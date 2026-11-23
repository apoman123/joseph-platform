# Test report — 2026-11-23

Results of every test tier on the final code, for contributors and reviewers. Environment: Linux, Python 3.13 (uv), Node 24, pnpm 10.34, Chromium 1243 via Playwright 1.63. Every number is from an actual run.

## Summary

| Tier | Project | Result |
|---|---|---|
| Unit, integration, contract, architecture, property, replay, in-process system | Market engine | **1,879 passed**, 47 skipped (45 opt-in network, 1 SinoPac data test that needs keys, 1 Pine parity test awaiting TradingView exports) · 96% line coverage |
| Unit, scenario, property, code standards | `smc_indicators` 1.1.0 | **393 passed** · ruff, `mypy --strict` clean |
| Live venue (opt-in, public APIs) | Market engine | **44 passed**, 1 skipped (no Alpaca key), 1 deselected (SinoPac data, needs keys): five crypto venues including MEXC Protobuf and the Lighter archive, TWSE, TPEx, Nasdaq Trader, Treasury. One Binance assertion failed once at a minute boundary; the test now allows 10 s of venue lag and passed 8/8 on reruns |
| Unit, integration, contract, architecture, security, chaos, property, in-process system | Execution engine | **1,028 passed**, 9 skipped (7 need real credentials: 5 venue testnets, IBKR paper, SinoPac simulation; 2 opt-in network) · 97% line coverage. Venues run against mocks and official signature vectors; IBKR and Shioaji against fake clients |
| Live venue (opt-in) | Execution engine | **2 passed**: MEXC private-stream Protobuf decoding against the live public stream |
| Unit, component, contract, architecture (Vitest) | Terminal | **904 passed** (106 files) · 83% line coverage (the browser tiers cover the canvas adapter) |
| Browser system, DEMO (Playwright + axe) | Terminal | **47 passed** |
| Full-stack browser: production build + both engines | All three | **12 passed** |
| Cross-service system (real processes, HTTP + WebSocket) | Both engines | **70 passed** |

## GitHub CI

| Run on `9cfd72a` (`main`) | Result |
|---|---|
| `ci-cd.yml` (every push) | **Green.** Every test and build job; all three images built and published (engine packages private); staging and production deploys skipped (no hosts configured) |
| `security.yml` (on `main`, pull requests and weekly) | **Green.** Full-history secret scan; CodeQL for Python, JavaScript/TypeScript and Actions; npm and Python audits |
| CodeQL alerts | **0 open:** 15 fixed, 2 dismissed as false positives (venue-mandated HMAC request signing; a redaction test that logs a fake secret) |

## Static checks

| Check | Result |
|---|---|
| ruff format + ruff (Google docstrings) | Clean in both engines and system-tests |
| `mypy --strict` | 169 files (market), 105 (execution), 15 (`smc_indicators`): no issues |
| Google style tests | Pass |
| import-linter | Market 8/8 contracts; execution 7/7 |
| ESLint (Google TS, JSDoc), Prettier, `tsc` | 0 problems |
| Contracts and OpenAPI drift | Up to date |
| dependency-cruiser | 368 modules, 0 violations |
| Secret scan | Pass |
| Licences | 65 production npm packages allowed (1 reviewed exception: `@img/sharp-libvips`, LGPL, server-only via Next.js); no GPL/AGPL Python dependencies |
| Production build | `next build` succeeds; 103 kB shared first-load JS |

## CI/CD and DevOps

| Check | Result |
|---|---|
| `actionlint` on all workflows | No issues |
| Market image with the private indicator library | Built in CI with the `pinescripts_token` BuildKit secret and locally with a forwarded ssh-agent; loads all 10 plugins, runs as uid 10001, holds no git, ssh, key or token |
| Indicator performance | 5,000 5m frames, 10 plugins, 11 series: 9.7 s (1.9 ms/frame) |
| Clean checkout (706 files, frozen installs, CI sequence) | Green on a cold run |
| Container images | market 287 MB, execution 479 MB (broker SDKs opt-in), terminal 412 MB; non-root, health-checked |
| Compose smoke test | All healthy; paper order FILLED, audit chain valid, unauthenticated → 401 |
| gitleaks, full history | No leaks. Allowlisted: Binance's and MEXC's documented example keys, fake adapter credentials, a test TOTP secret, MIS record keys, an Alpaca page token, one redaction-test credential |
| Dependency audits | npm clean after overriding PostCSS to 8.5.33; Python clean after `cryptography` 46.0.7 → 50.0.2 |

## What the higher tiers prove

- **Indicators = Pine:** hand-derived golden scenarios pass; batch, incremental and restored runs agree; future bars never change the past.
- **Signals:** an always-true PAPER strategy fires only after activation and creates exactly one sized paper order per entry.
- **Trading safety:** idempotent replay (409 on a changed body), kill switch, LIVE fail-closed, price band, tenant isolation, audit chain, no duplicate after ACK loss.
- **Data and UI:** contract validation on every response; a disabled venue shows an error, never fake prices; no serious axe findings; CSP and security headers verified.
- **Performance:** 200 candle requests p50 2.6 ms, p95 3.0 ms; 5,000 bars plus intrabars through every plugin in 3.9 s.

## Defects found and fixed

| Defect | Found by | Fix |
|---|---|---|
| Indicator engine could read a future higher-timeframe snapshot while a series stalled | Property test | Per-series snapshot history |
| Dealing range inverted on stale anchors | Hypothesis | Stale anchors no longer reset it |
| `useStore` re-subscribed every render (endless fetch loop) | Browser test | Stable subscribe function |
| Control envelopes could make clients drop candles | Integration test | Sequence 0 for control envelopes |
| DEMO broker filled a reduce-only close after a partial fill | Browser + unit test | FILLED only when fully filled |
| Strategy card text squeezed to one word per line | Screenshot review | Full-width summary row |
| Icon buttons rendered 6 px glyphs | Screenshot review | No padding; SVGs never shrink |
| Confirm label clipped; pressed Buy/Sell label invisible | Screenshot review | Full-width confirm row; dark text |
| Search failed when the chart's venue was disabled | Full-stack test | Falls back to available venues |
| Web tests resolved React from the wrong root | Integration | Vitest root `apps/web` |
| Vulnerable `cryptography` and PostCSS | pip-audit / pnpm audit | Upgraded; audits block CI |
| Process fixtures could deadlock on stdout | Clean-checkout run | Log to file, detect early exit, 60 s cold start |
| Third-party actions referenced by mutable tags | CodeQL | Pinned to commit SHAs with version comments |
| Strategy-builder `slug()` trim was quadratic on long dash runs | CodeQL | Index scans; regression test |
| Secret scan checked a file's size and read it separately (race) | CodeQL | Size check and read through one file descriptor |

## Reproduce

```bash
make ci                                      # lint, types, every offline tier
make test-system                             # cross-service
cd joseph-terminal && pnpm test:e2e:fullstack
make test-network                            # live venues (needs internet)
```
