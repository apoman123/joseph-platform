# ADR-0001: Modular monolith + hexagonal architecture

- **Status:** Accepted (2026-11-22)
- **Context:** Joseph must support five venues, seven indicators, user rules, alerts and an OMS that change independently, but a 7–15 service mesh would multiply failure modes before any interface is stable.
- **Decision:** Three deployables: `joseph-terminal` (public Next.js), `joseph-market-engine` (private Python: market data, screening, indicators, strategy, signals, alerts, backtest) and `joseph-execution-engine` (private Python: accounts, vault, risk, OMS, reconciliation, audit). Inside each, bounded contexts follow domain → app/ports → adapters, wired only in a composition root (`bootstrap.py`, `apps/web/src/composition`). Events are in-process with a durable outbox; a broker (NATS JetStream/Kafka) is added only when measured load requires it.
- **Alternatives:** Microservice per context (rejected: premature distribution); single flat app (rejected: no enforceable boundaries).
- **Consequences:** Boundaries are enforced by `import-linter` (Python) and `dependency-cruiser` (TS) in CI. Extracting a context later means moving a package behind its existing port.
- **Migration:** Not applicable (initial decision).
- **Rollback:** Re-merge an extracted context behind its port; no data migration needed because contexts share no tables.
