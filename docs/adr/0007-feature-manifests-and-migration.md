# ADR-0007: Feature manifests, deprecation and saved-document migration

- **Status:** Accepted (2026-11-22)
- **Context:** Drawing tools, indicators, rule nodes and venues are added and removed often; removal must not break the terminal or delete user data.
- **Decision:** Every terminal feature has a manifest (id, version, owner, slots, capabilities, dependencies, permissions, settings schema version, contract version, lifecycle, removal plan) and returns a disposer from `activate`. The registry resolves dependencies, rejects cycles/duplicates, gates on capabilities and disposes every registration on deactivate. Saved drawings, workspaces and strategies carry schema versions and migrations; documents from removed tools are preserved as opaque records and shown as unsupported.
- **Alternatives:** Runtime-fetched remote plugins (rejected: supply-chain risk); hard-coded feature switches (rejected: removal touches core).
- **Consequences:** Feature flags only change exposure; they cannot grant trading permission or bypass server risk.
- **Migration:** Lifecycle UNAVAILABLE → DISABLED → ENABLED → DEPRECATED → REMOVED with migration before removal.
- **Rollback:** Re-enable the previous manifest; opaque documents become editable again.
