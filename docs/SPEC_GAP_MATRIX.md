# V5 spec gap matrix

Legend: **Done** (implemented and tested) · **Partial** (works; listed gaps remain) · **Not done**. "Tests" names the strongest evidence.

## Phases

| Phase | Status | Evidence / gaps |
|---|---|---|
| P0 Foundation | **Done** | JSON Schema contracts + generated TS + shared fixtures validated by both stacks; ADR 0001–0008; Google-style lint and docstring/import tests; import-linter and dependency-cruiser boundaries; mock providers; secret, license and vulnerability scans; GitHub Actions CI/CD (all tiers on every push, GHCR images with SBOM and provenance, gated staging/production deploys, rollback); OpenAPI/AsyncAPI |
| P1 Chart core | **Done** | `ChartPort` + Lightweight Charts adapter; candles/bars/line/area/Heikin Ashi; linear/log/percent; volume pane and Delta % sub-pane (engine series); compare up to 3 symbols; session breaks; data window; bar replay (point-in-time AOIs, trading off); screenshot; chart templates; crosshair sync; provenance ribbon. Timeframes 1s–1M (seconds built from venue trades, calendar months), true/estimate delta labelling, detachable panes (pop-out windows synced over BroadcastChannel). TradingView use cases: 94/94 done (`reports/TRADINGVIEW_USE_CASE_AUDIT.md`; residual limits listed there) |
| P2 Drawing & workspace | **Done** | 37 anchored tools (P0–P3 incl. ray, extended line, arrow, polyline, brush, zone box, ellipse, Gann fan/box, pitchfork, harmonic and Elliott patterns; anchored VWAP and regression channel drawn from engine series), style editor and object tree (hide/lock/z-order/delete) through undoable commands, magnet, migrations, unsupported-tool preservation, 1/2/4/8 layouts with sync and link groups, resizable splitters and docks, autosave, import/export/share link |
| P3 Market data | **Partial** | Crypto: 5 venues verified live (REST + native WS candles on 4; Lighter polls); native WS trade streams on all 5; seconds bars (`1s`–`30s`) from public trades, recorded and persisted, with Binance and Bitget public archives; aggregated timeframes where a venue lacks one (`1M` on Hyperliquid/Lighter); true delta from taker volume (Binance) or fully covering recorded trades (others). Securities and rates: TWSE and TPEx (official sources, live MIS snapshots), Nasdaq/NYSE through Alpaca (needs the owner's key; documented shapes only until a key is configured), Treasury par/real yields; session calendars (XTAI, XNYS, USBOND) for every bar boundary; classified (Lee-Ready) delta on stocks; sparse-bar coverage so quiet minutes are not gaps. Gaps: WS order books not wired; Lighter's WS candle channel and public Parquet archive not used; TWSE/TPEx intraday history only since recording began (5 s-class snapshots, no seconds bars); MEXC/Hyperliquid seconds history only since recording began |
| P4 Filter & indicators | **Partial** | Strict 5-gate qualification + funding policy; 8 indicators ported line by line from the Pine scripts into `PineScripts/python_indicators` (heat scores included) and adapted as plugins; batch == incremental == restore, no look-ahead (property tests). Gap: `GOLDEN_VERIFIED` needs TradingView exports (`SPEC_ONLY` until then) |
| P5 Strategy & alerts | **Done** | Rule AST v2, compiler, Kleene evaluator with reason trees, setup FSM, immutable versions, backtests on engine frames, live pipeline (fires once, never on history), alert outbox → UI/Telegram/LINE/e-mail/webhook, subscriptions with quiet hours. Gap: external channels only tested against mocks |
| P6 Paper / shadow OMS | **Done** | Preview tokens, idempotency, transactional outbox, partial fills, cancel/fill race, `UNKNOWN_RECONCILING` reconcile, brackets, reduce-only close, kill switch, audit chain, deployments + signal intake |
| P7 Private execution hardening | **Partial** | Deny-by-default risk gateway (crypto and securities checks: sessions, TWSE/TPEx ticks and ±limits, board/odd lots, cash-only), AES-GCM vault, step-up gate, server-side account LIVE arming, signed live adapters and private streams for 5 crypto venues, Interactive Brokers (IB Gateway) and SinoPac Shioaji brokers with runtime capability negotiation; Cathay fail-closed pending its SDK. Not done: testnet/paper runs with real keys, KMS, OIDC/MFA |
| P8 Live pilot gate | **Partial** | Owner enabled the global gate in deploy config; accounts trade only after verification, account arming (manual) or deployment consent (AUTO_LIVE), preview and the risk gateway. Pending: testnet runs per venue, IBKR paper and SinoPac simulation runs with the owner's credentials, and a kill-switch drill |
| P9 Production & ecosystem | **Partial** | Extension guides, plugin lifecycle, latency smoke. Not done: load/DR drills, PostgreSQL CI, multi-worker leases |

## Requirements spot-check

| V5 requirement | Status | Where |
|---|---|---|
| Same symbol on two venues is two instruments | Done | `InstrumentId`, contract regex, selector shows exact ids |
| Decimal strings for money; no floats in order paths | Done | `decimals.py`, `Decimal` (TS), contract fixtures reject numbers |
| No silent synthetic fallback | Done | Full-stack browser test "unavailable venue shows an error" |
| NATIVE_WS returns UNSUPPORTED instead of polling | Done | Feed router tests |
| Equal-to-threshold fails; missing data never qualifies | Done | Screening property tests; screener UI test |
| HTF bars visible only after close | Done | Engine property tests; `last_closed_start` |
| AST: ALL/ANY/NOT, SEQUENCE, range binding, K-of-N bias, AOI touch modes | Done | Evaluator unit tests, Long A / Short B scenarios |
| Unknown data blocks entries | Done | Evaluator + FSM tests |
| One pathway: intent → risk → OMS → adapter | Done | import-linter (adapters can't import OMS/risk); positive-control live-gate test |
| ACK loss never duplicates an order | Done | Execution chaos tests |
| Kill switch blocks opening, allows reduce-only | Done | Unit, integration and system tests |
| Tenant isolation (REST + WS) | Done | Execution security tests; system tests; signal stream test |
| Strategy edit ⇒ new version, no inherited live permission | Done | Version service tests; consent scope hash |
| Removing a plugin/tool/node keeps saved data | Done | Registry, drawing-core and compiler tests |
| Accessibility | Partial | axe scan has no serious or critical issues; chart canvas captions aren't readable by screen readers (pane headers are) |
| Pine source HEAD recorded, licensing reviewed | Done | HEAD `4091278` ported into `PineScripts/python_indicators` @ `89aa506` (`reports/PINE_SOURCE_REVISION.md`); LuxAlgo CC BY-NC-SA obligations in `reports/LICENSING_REPORT.md` |
| Indicators separate from the platform | Done | Platform holds adapters only; library pinned by commit (ADR 0009) |
| Signal dashboard sorting and snooze | Done | Alerts sort by newest / R / confluence / distance / age; snooze and dismiss (Vitest + Playwright) |

Design revisions to the proposal are listed in `PROPOSAL_REVISIONS.md`.
