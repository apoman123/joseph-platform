# Feature status

What works in each area of the platform, the evidence for it and the known gaps. For users and contributors deciding what they can rely on; why the design differs from the original plan is in [Design decisions](DESIGN_DECISIONS.md).

**Done** means implemented and tested; **Partial** means it works, with the gaps listed.

## By area

| Area | Status | Evidence | Gaps |
|---|---|---|---|
| Foundation | **Done** | JSON Schema contracts and shared fixtures validated by both stacks; ADRs 0001–0009; Google-style lint; import-linter and dependency-cruiser; mock providers; secret, licence and vulnerability scans; CodeQL; CI/CD with GHCR images, gated deploys and rollback; OpenAPI/AsyncAPI | — |
| Chart core | **Done** | `ChartPort` + Lightweight Charts; chart types, scales, volume and Delta % panes, compare, session breaks, data window, replay, screenshot, templates, sync, provenance; 1s–1M; detachable panes. TradingView-style use cases 94/94 ([audit](reports/TRADINGVIEW_USE_CASE_AUDIT.md)) | — |
| Drawing and workspace | **Done** | 37 anchored tools including engine-backed VWAP and regression; undoable style and object commands; migrations; unsupported tools preserved; 1/2/4/8 layouts; autosave, import/export, share | — |
| Market data | **Partial** | Five crypto venues verified live (native WebSocket candles on four; Lighter is polled); seconds bars from recorded trades and the Binance, Bitget and Lighter archives; aggregated timeframes; true delta from taker volume or covering trades; MEXC Protobuf. TWSE/TPEx (official, optional SinoPac), Nasdaq/NYSE/Cboe BZX via Alpaca with official closing prints, Treasury yields; XTAI/XNYS/USBOND calendars; classified stock delta; persistent sparse-bar coverage | No crypto order-book streams; Lighter WebSocket candles unused; official Taiwan data has no seconds bars and history only since recording began; MEXC and Hyperliquid seconds bars only since recording began; Alpaca is tested against hand-written fixtures in its documented shapes, not yet live with a key |
| Screening and indicators | **Partial** | Strict 5-gate qualification and funding policy; nine built-in indicators (SMA, EMA, VWAP, regression channel, Bollinger Bands, RSI, ATR, volume delta, pivot points); plugin packages discovered at startup with isolated failures, rendered and offered to strategies from their metadata; batch == incremental == restore, no look-ahead | `GOLDEN_VERIFIED` parity needs TradingView exports; strategies use each plugin's default settings only |
| Strategies and alerts | **Done** | Rule AST v2, compiler, three-valued (Kleene) evaluator, setup state machine, immutable versions, backtests, live pipeline (fires once, never on history), alert outbox, quiet hours | External channels tested only against mocks |
| Paper and shadow trading | **Done** | Preview tokens, idempotency, outbox, partial fills, cancel/fill race, `UNKNOWN_RECONCILING`, brackets, reduce-only close, kill switch, audit chain, deployments | — |
| Execution hardening | **Partial** | Deny-by-default risk gateway (crypto and securities, date-aware Taiwan tax), AES-GCM vault, step-up, account arming, signed adapters and private streams for five venues, IBKR and SinoPac, LIVE reconciliation; Cathay fails closed | No runs with real keys; no KMS; no OIDC/MFA |
| Live pilot | **Partial** | Global gate on in the deploy configuration; accounts trade only after verification, arming or consent, preview and risk checks | Testnet, IBKR paper and SinoPac simulation runs with real credentials; a kill-switch drill |
| Production readiness | **Partial** | Extension guides, plugin lifecycle, latency smoke test | Load and disaster-recovery drills; PostgreSQL in CI; multi-worker leases |

## Cross-cutting requirements

All **Done** except accessibility:

- Same symbol on two venues = two instruments (`InstrumentId`, contract regex).
- Decimal strings for money, no floats on order paths (`decimals.py`, TS `Decimal`, fixtures reject numbers).
- No silent synthetic fallback (full-stack test "unavailable venue shows an error").
- NATIVE_WS returns UNSUPPORTED instead of polling (feed router tests).
- Equal-to-threshold fails; missing data never qualifies (screening property and UI tests).
- Higher-timeframe bars visible only after close (property tests; `last_closed_start`).
- AST ALL/ANY/NOT, SEQUENCE, zone groups, K-of-N state, AOI touch modes; unknown data blocks entries (evaluator and state-machine tests).
- One pathway intent → risk → OMS → adapter (import-linter; positive-control live-gate test); a lost ACK never duplicates an order (chaos tests).
- Kill switch blocks opening, allows reduce-only; tenant isolation over REST and WebSocket (unit, integration, security and system tests).
- Editing a strategy creates a new version without live permission (version tests, consent scope hash); removing a plugin, tool or node keeps saved data.
- Pine parity procedure documented ([parity exports](../joseph-market-engine/fixtures/indicators/pine_exports/README.md)), licensing reviewed ([licensing](reports/LICENSING_REPORT.md)); indicators beyond the built-ins live in plugin packages outside the platform ([ADR 0009](adr/0009-external-indicator-library.md)).
- Signal sorting and snooze (Vitest + Playwright).
- **Partial — accessibility:** no serious or critical axe issues, but chart canvas captions are not readable by screen readers (pane headers are).
