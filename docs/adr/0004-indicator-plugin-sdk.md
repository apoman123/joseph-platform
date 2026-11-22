# ADR-0004: Indicator plugin SDK and versioned outputs

- **Status:** Accepted (2026-11-22)
- **Context:** Seven SMC indicators must be portable from Pine, testable bar by bar, and removable without touching each other or the OMS.
- **Decision:** Each indicator is an `IndicatorPlugin` package with `plugin_id`, `indicator_version`, input schema, warm-up, required streams and a pure `on_bar_closed(state, bar) -> (state, output)`. Plugins publish only `Aoi`, `StructureEvent` and documented scalar `values` inside an `IndicatorSnapshot`; consumers never read private state. Plugins never import each other (enforced). Outputs carry `method` (`pine_parity` vs `trade`) so enhanced models are never mistaken for parity.
- **Alternatives:** One monolithic indicator module (rejected: changes ripple); JavaScript indicators in the terminal (rejected: Python is authoritative).
- **Consequences:** Batch, incremental and checkpoint-replay runs must produce identical snapshots (property-tested). Plugins are adapters around the external Pine-port library (ADR 0009, which replaces the first clean-room port); golden Pine exports upgrade `parity_status` from `SPEC_ONLY` to `GOLDEN_VERIFIED`.
- **Migration:** A changed algorithm ships as a new `indicator_version`; saved strategies record the versions they were evaluated with.
- **Rollback:** Re-register the previous plugin version.
