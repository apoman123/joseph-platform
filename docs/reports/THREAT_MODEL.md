# Threat model (STRIDE, compact)

**Assets:** venue credentials, ability to place orders, strategy IP, tenant data, audit integrity.
**Trust zones:** browser (untrusted) → market engine (private, no trading credentials) → execution engine (private, holds encrypted credentials) → venues.

| Threat | Example | Mitigation | Verified by |
|---|---|---|---|
| Spoofing | Forged request or WebSocket message places an order | Bearer auth on every execution endpoint; service token on `/internal/v1/signals`; user WS requires token; LIVE needs the global flag plus step-up: scoped deployment consent (strategies) or an expiring, server-side account arming (manual orders, `LIVE_NOT_ARMED` otherwise) | Execution security tests (auth on all routes, forged WS/HTTP cannot reach a live adapter); `integration/test_live_arming.py` |
| Tampering | Edited preview parameters at submit; altered audit rows | HMAC preview token bound to normalized parameters and expiry; hash-chained append-only audit with `chain_valid` | Preview tamper/expiry tests; audit tamper detection test |
| Repudiation | "I never sent that order" | Every intent, decision, submit/cancel/amend, kill switch, consent, LIVE arming (granted, denied, revoked) and credential change is audited with actor and trace id | Audit integration tests |
| Information disclosure | API secret in logs, responses or the JS bundle | AES-GCM envelope encryption; redaction log filter; secrets never serialized; browser holds only a session token; secret scan in CI | Vault/redaction tests; `pnpm secrets:scan`; dependency-cruiser bans server code in browser |
| Denial of service | Venue rate limits, WS floods | Per-venue throttling at documented weights (requests refused before sending when the budget is exhausted), jittered reconnect, bounded queues and ring buffers; 429/418 mapped to `RATE_LIMITED` and pause the venue | Adapter 429/timeout tests, `unit/venues/test_venue_infra.py` |
| Key misuse | Withdrawal-capable or master keys used for trading | Connection refuses withdrawal-capable keys by attestation; Binance verifies `enableWithdrawals=false`; Hyperliquid refuses the master key as agent; Lighter refuses reserved key indexes; secrets are redacted in reprs, errors and logs | `unit/venues/`, `integration/test_live_venues.py` |
| Elevation of privilege | Tenant A reads or cancels tenant B's orders; feature flag enables LIVE | Tenant scoping in every repository query (foreign ids → 404); feature flags cannot grant trading permission; risk gateway is deny-by-default | Tenant-isolation tests (REST + WS); mode-gate tests |
| Duplicate / runaway orders | ACK lost, network partition, replayed signal | Idempotency keys, transactional outbox, `UNKNOWN_RECONCILING` + reconcile by `client_order_id`, once-per-signal deployment intake, kill switch | Chaos and property tests |
| Bad data → bad trades | Stale book, unclosed HTF bar, DEMO data | Provenance on every value; stale/unknown → BLOCK; confirmed-bar evaluation; DEMO/REPLAY never tradable live | Indicator no-look-ahead properties; risk data-quality tests |

**Residual risks (open):** production identity (OIDC + MFA) and a BFF are not implemented (dev tokens only); KMS integration is a local master key; venue keys are decrypted in process for each signed request (no HSM/remote signer); MEXC has no sandbox, so its adapter is verified only against documented payloads; no WAF/rate limiting at an edge gateway.
