<!-- Tick each item or explain under "Known gaps" why it does not apply. -->

## What changed

<!-- One paragraph. Link the issue. -->

## Change safety checklist

- [ ] Public contracts changed? If yes: schemas and fixtures updated, `make contracts openapi` run, migration noted.
- [ ] Diff contained to the owning bounded context or package.
- [ ] Feature can be disabled or removed without touching unrelated features.
- [ ] Works with the mock/in-memory adapters and test fixtures.
- [ ] Replay and live semantics identical at confirmed-bar boundaries (no look-ahead).
- [ ] Tenant, account, order and strategy-version isolation preserved.
- [ ] Failure here fails safe without corrupting the OMS or other modules.
- [ ] No new hidden direct imports or shared mutable state (`pnpm arch:check`, import-linter).
- [ ] Trading: no new path to a venue bypasses intent → risk gateway → OMS; the LIVE gate stays off by default in code.

## Evidence

<!-- Tests added or changed, screenshots for UI, ADR link if the architecture changed. -->

## Known gaps / next steps
