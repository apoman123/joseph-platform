# ADR-0005: Declarative strategy AST and deterministic evaluator

- **Status:** Accepted (2026-11-22)
- **Context:** Users define their own long/short rules; arbitrary code would be unsafe and unreplayable.
- **Decision:** The builder emits Rule AST v2 (JSON Schema `StrategyDefinition`): ALL/ANY/NOT, BIAS, PRICE_IN_ZONE with range binding, AOI_TOUCH/OVERLAP, EVENT, SEQUENCE, COMPARE, IN_RANGE, CROSSES, PERSIST_FOR, WITHIN_BARS, SESSION, RISK_REWARD. A pure tri-state (TRUE/FALSE/UNKNOWN) evaluator runs over `BarFrame`s; UNKNOWN blocks entries. A setup state machine (WAITING → AOI_TOUCHED → CONFIRMING → READY → FIRED, exits EXPIRED/INVALIDATED/CANCELLED/BLOCKED) deduplicates by scoped key. Versions are immutable and hashed; editing creates a new version without carrying activation.
- **Alternatives:** String expressions or user Python (rejected: injection, non-determinism); UI-only rules (rejected: not authoritative).
- **Consequences:** Backtest, replay, paper, shadow and live share one evaluator. Removing a node type makes old strategies fail validation with actionable errors; conditions are never silently dropped.
- **Migration:** AST `schema_version` migrations (v1 starter config → v2 provided in `strategy-builder`).
- **Rollback:** Keep the previous evaluator version available for historical replay.
