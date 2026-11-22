# ADR-0003: Event-time and confirmed-bar canonical data contracts

- **Status:** Accepted (2026-11-22)
- **Context:** Signals computed on unfinished bars or unclosed higher timeframes produce look-ahead bias and non-reproducible alerts.
- **Decision:** Bars are UTC-aligned half-open intervals `[start_ms, end_ms)` with `is_final`, `revision`, `source`, `feed_state`, `received_at_ms`. Only final bars confirm signals. A higher-timeframe bar is visible to a lower-timeframe evaluation only when `htf.end_ms <= ltf.end_ms`. Instruments are keyed `{venue}:{market_type}:{venue_market_id}`; the same base asset on two venues is two instruments. Money, prices and quantities are decimal strings on the wire.
- **Alternatives:** Symbol-only keys such as `BTCUSDT` (rejected: cross-venue collisions); float prices (rejected: rounding errors in orders).
- **Consequences:** Every indicator and strategy test asserts no look-ahead; contract fixtures are validated by both stacks.
- **Migration:** Schema changes bump `schema_version` with a migration note.
- **Rollback:** Older schema versions stay readable; producers can be pinned to the previous version.
