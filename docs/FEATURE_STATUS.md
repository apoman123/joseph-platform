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
| Screening and indicators | **Partial** | Screener of built-in `Filter` scripts combined with AND (liquidity qualification with strict turnover, open-interest, spread, slippage and funding gates; price, change, volume, turnover, relative volume, gap, moving average, RSI, new high/low) and owned filters, scanned in batches with progress, cancellation and unavailable-data reporting; nine built-in indicators (SMA, EMA, VWAP, regression channel, Bollinger Bands, RSI, ATR, volume delta, pivot points); plugin packages discovered at startup with isolated failures, rendered from their metadata; batch == incremental == restore, no look-ahead | `GOLDEN_VERIFIED` parity needs TradingView exports |
| Strategies and alerts | **Done** | Python `Strategy` scripts only ([ADR 0010](adr/0010-python-strategy-scripts.md)): three built-ins (EMA trend long, RSI reversion short, pivot bounce long), plugin and tenant strategies; backtests with orders, trades, equity and metrics; persisted activations (ALERT_ONLY, PAPER, SHADOW; AUTO_LIVE refused) that signal entries and exits on newly closed bars and never on history; seven built-in `Alert` scripts (price crossing, channel, moving-average cross, RSI level, volume spike, percent change, breakout) run as alert jobs without Docker, managed in the Alert manager; alert outbox, quiet hours | External channels tested only against mocks; tenant strategies need the Docker runner; DEMO strategy results are labelled sample data |
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
- Equal-to-threshold fails; missing data never qualifies (liquidity qualification filter vectors, property and UI tests).
- Higher-timeframe bars visible only after close (property tests; `last_closed_start`).
- Built-in strategies on deterministic bars queue the expected orders, stops and targets and fill at the next open; activations dedupe signals, never replay history across pause, resume or restart, and forward only PAPER/SHADOW entries (unit, integration and system tests).
- One pathway intent → risk → OMS → adapter (import-linter; positive-control live-gate test); a lost ACK never duplicates an order (chaos tests).
- Kill switch blocks opening, allows reduce-only; tenant isolation over REST and WebSocket (unit, integration, security and system tests).
- Editing a strategy script creates a new immutable version that existing activations and deployments do not adopt; a changed built-in reports an error instead of evaluating; removing a plugin or tool keeps saved data.
- Pine parity procedure documented ([parity exports](../joseph-market-engine/fixtures/indicators/pine_exports/README.md)), licensing reviewed ([licensing](reports/LICENSING_REPORT.md)); indicators beyond the built-ins live in plugin packages outside the platform ([ADR 0009](adr/0009-external-indicator-library.md)).
- Signal sorting and snooze (Vitest + Playwright).
- **Partial — accessibility:** no serious or critical axe issues, but chart canvas captions are not readable by screen readers (pane headers are).
