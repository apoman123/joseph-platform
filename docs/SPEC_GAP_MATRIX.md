# V5 spec gap matrix

**Done** = implemented and tested · **Partial** = works, gaps listed. Design changes: [PROPOSAL_REVISIONS](PROPOSAL_REVISIONS.md).

| Phase | Status | Evidence and gaps |
|---|---|---|
| P0 Foundation | **Done** | JSON Schema contracts and shared fixtures validated by both stacks; ADRs 0001–0009; Google-style lint; import-linter and dependency-cruiser; mock providers; secret, license and vulnerability scans; CI/CD with GHCR images, gated deploys and rollback; OpenAPI/AsyncAPI |
| P1 Chart core | **Done** | `ChartPort` + Lightweight Charts; chart types, scales, volume and Delta % panes, compare, session breaks, data window, replay, screenshot, templates, sync, provenance; 1s–1M; detachable panes. TradingView use cases 94/94 ([audit](reports/TRADINGVIEW_USE_CASE_AUDIT.md)) |
| P2 Drawing and workspace | **Done** | 37 anchored tools incl. engine-backed VWAP and regression; undoable style and object commands; migrations; unsupported-tool preservation; 1/2/4/8 layouts; autosave, import/export, share |
| P3 Market data | **Partial** | Five crypto venues verified live (native WS candles on 4; Lighter polls); seconds bars from recorded trades and the Binance, Bitget and Lighter archives; aggregated timeframes; true delta from taker volume or covering trades; MEXC Protobuf. TWSE/TPEx (official, optional SinoPac), Nasdaq/NYSE/Cboe via Alpaca (fixtures follow documented shapes until a key is set) with official closing prints, Treasury yields; XTAI/XNYS/USBOND calendars; classified stock delta; persistent sparse-bar coverage. **Gaps:** no crypto book streams; Lighter WS candles unused; official Taiwan data has no seconds and history only since recording; MEXC/Hyperliquid seconds only since recording |
| P4 Filter and indicators | **Partial** | Strict 5-gate qualification and funding policy; 8 Pine scripts ported to `PineScripts/python_indicators` and adapted; batch == incremental == restore, no look-ahead. **Gap:** `GOLDEN_VERIFIED` needs TradingView exports |
| P5 Strategy and alerts | **Done** | Rule AST v2, compiler, Kleene evaluator, setup FSM, immutable versions, backtests, live pipeline (fires once, never on history), alert outbox, quiet hours. **Gap:** external channels tested only against mocks |
| P6 Paper / shadow OMS | **Done** | Preview tokens, idempotency, outbox, partial fills, cancel/fill race, `UNKNOWN_RECONCILING`, brackets, reduce-only close, kill switch, audit chain, deployments |
| P7 Execution hardening | **Partial** | Deny-by-default risk gateway (crypto and securities, date-aware Taiwan tax), AES-GCM vault, step-up, account arming, signed adapters and private streams for 5 venues, IBKR and SinoPac, LIVE reconciliation; Cathay fail-closed. **Not done:** runs with real keys, KMS, OIDC/MFA |
| P8 Live pilot gate | **Partial** | Global gate on in deploy config; accounts trade only after verification, arming or consent, preview and risk checks. **Pending:** testnet, IBKR paper and SinoPac simulation runs with the owner's credentials; a kill-switch drill |
| P9 Production | **Partial** | Extension guides, plugin lifecycle, latency smoke. **Not done:** load/DR drills, PostgreSQL CI, multi-worker leases |

## Requirements spot-check

All **Done** except accessibility:

- Same symbol on two venues = two instruments (`InstrumentId`, contract regex).
- Decimal strings for money, no floats on order paths (`decimals.py`, TS `Decimal`, fixtures reject numbers).
- No silent synthetic fallback (full-stack test "unavailable venue shows an error").
- NATIVE_WS returns UNSUPPORTED instead of polling (feed router tests).
- Equal-to-threshold fails; missing data never qualifies (screening property and UI tests).
- HTF bars visible only after close (property tests; `last_closed_start`).
- AST ALL/ANY/NOT, SEQUENCE, range binding, K-of-N bias, AOI touch modes; unknown data blocks entries (evaluator and FSM tests).
- One pathway intent → risk → OMS → adapter (import-linter; positive-control live-gate test); ACK loss never duplicates (chaos tests).
- Kill switch blocks opening, allows reduce-only; tenant isolation over REST and WS (unit, integration, security and system tests).
- Strategy edit ⇒ new version without live permission (version tests, consent scope hash); removing a plugin, tool or node keeps saved data.
- Pine source recorded ([record](reports/PINE_SOURCE_REVISION.md)), licensing reviewed ([licensing](reports/LICENSING_REPORT.md)); indicators separate from the platform (ADR 0009).
- Signal sorting and snooze (Vitest + Playwright).
- **Partial — accessibility:** no serious or critical axe issues, but chart canvas captions are not screen-reader readable (pane headers are).
