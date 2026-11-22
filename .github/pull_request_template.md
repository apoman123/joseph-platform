## What changed

<!-- One paragraph. Link the issue or V5 section. -->

## Change safety scorecard (V5 §10)

- [ ] Public contracts changed? If yes: schemas + fixtures updated, `make contracts openapi` run, migration noted.
- [ ] Diff contained to the owning bounded context / package.
- [ ] Feature can be disabled or removed without touching unrelated features.
- [ ] Works with the mock/in-memory adapters and test fixtures.
- [ ] Replay and live semantics identical at confirmed-bar boundaries (no look-ahead).
- [ ] Tenant, account, order and strategy-version isolation preserved.
- [ ] Failure here fails safe without corrupting OMS or other modules.
- [ ] No new hidden direct imports or shared mutable state (`pnpm arch:check`, import-linter).
- [ ] Trading impact: does not add a path to a venue that bypasses intent → risk gateway → OMS; LIVE stays off.

## Evidence

<!-- Tests added/changed, screenshots for UI, ADR link if architecture changed. -->

## Known gaps / next PR
