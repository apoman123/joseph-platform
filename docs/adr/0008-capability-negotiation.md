# ADR-0008: Native exchange capability negotiation; no fake support

- **Status:** Accepted (2026-11-22)
- **Context:** Venues differ in WebSocket coverage, order types, reduce-only, brackets and account eligibility (e.g. MEXC API eligibility by region/policy).
- **Decision:** Market-data adapters publish `VenueCapabilities` listing only implemented and verified features; trading adapters publish `TradingCapabilities` per account. NATIVE_WS mode returns UNSUPPORTED rather than polling; AUTO falls back to REST polling only for display data and labels it POLL. Missing capabilities fail closed (e.g. AUTO_LIVE requires native stop-loss).
- **Alternatives:** Lowest-common-denominator abstraction (rejected: hides real limits); silent emulation (rejected: false sense of protection).
- **Consequences:** The UI renders capability-driven controls and provenance badges; contract tests run against captured venue fixtures and opt-in live probes.
- **Migration:** Capability flags are additive; removed capabilities make dependent features UNAVAILABLE.
- **Rollback:** Pin the adapter to the previous capability set.
