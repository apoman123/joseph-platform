# ADR-0006: Mandatory server-side risk gateway and OMS; fail-closed live trading

- **Status:** Accepted (2026-11-22)
- **Context:** Every order path (ticket, chart, quick trade, strategy) must be auditable and protected from duplicates, stale data and runaway automation.
- **Decision:** All orders become an immutable `OrderIntent` → deny-by-default Risk Gateway → OMS state machine → venue adapter. Only the OMS holds the execution port. Idempotency keys, a transactional outbox and reconcile-by-`client_order_id` replace any assumption of exactly-once delivery; uncertain submissions go to `UNKNOWN_RECONCILING` and are never blindly retried. LIVE requires a global flag, account capability, explicit scoped consent with step-up, and is OFF by default; in this build venue signing is not implemented, so live adapters report `TRADING_UNAVAILABLE`. The kill switch blocks new exposure; cancelling and flattening are separate explicit options; authorized reduce-only actions remain possible.
- **Alternatives:** Client-side risk checks (rejected: bypassable); per-adapter risk (rejected: inconsistent).
- **Consequences:** Chaos tests cover ACK loss, timeouts, duplicate/out-of-order events and cancel/fill races.
- **Migration:** Risk policy changes bump `policy_version`, recorded on every decision.
- **Rollback:** Engage the global kill switch; disable deployments; LIVE stays off.
