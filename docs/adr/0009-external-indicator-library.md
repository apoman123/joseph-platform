# ADR 0009 — Indicators live in an external library

Status: accepted (2026-11-22). Supersedes the "first-party plugins" part of ADR 0004; the plugin SDK is unchanged.

## Context

The owner's Pine scripts in `apoman123/PineScripts` are the reference for every indicator. The market engine's seven clean-room plugins, written from the V5 text, drifted from the scripts (break rules, inversions, dealing-range resets). The owner wants a hard split: "平台歸平台，指標歸指標" (the platform is a platform, indicators are indicators).

## Decision

- Port each Pine script line by line into a standalone package, `PineScripts/python_indicators` (`joseph-smc-indicators`, import `smc_indicators`), with no platform dependency. Contract: the result for closed bar k equals the Pine state on bar k with `barstate.isconfirmed`. Floats are deliberate: Pine computes in doubles.
- The market engine depends on it as a git dependency **pinned to a commit** (`[tool.uv.sources]`, `uv.lock`); upgrading is an explicit, reviewable `rev` bump.
- `joseph/indicators/packaged/` holds only adapters: settings mirroring Pine inputs, data streams, decimal conversion, deterministic AOI ids, ended-AOI inference and the published-values contract. An import-linter contract keeps adapters free of infrastructure.
- Purity and checkpoints: library objects are mutable, so the newest state mutates shared live objects, while any state can rebuild its own from a pickled base taken every 32 bars plus a log of later bar inputs (the library is deterministic). Checkpoints are unpickled with an allowlist of `smc_indicators` / adapter classes and inert standard types.
- CI, Dependabot and image builds reach the private repository over HTTPS with a fine-grained token limited to PineScripts, Contents read-only (`PINESCRIPTS_TOKEN`, an Actions and a Dependabot secret): a URL rewrite scoped to that repository in CI, a `git` registry for Dependabot, a BuildKit secret for images. Local builds may use SSH. (Amended 2026-11-23: replaces the deploy key and SSH forwarding; Dependabot `git` registries accept only HTTPS credentials.)

## Consequences

- One source of truth per indicator; charts, strategies, backtests and alerts move together on the next `rev` bump.
- CI fails, with a clear message, until the token secret exists; the token expires and must be rotated (`docs/CI_CD.md`).
- The LuxAlgo-derived OTE module is CC BY-NC-SA 4.0: no commercial use while it is enabled (`docs/reports/LICENSING_REPORT.md`).
- All eight plugins take about 1.7 ms per 5m frame (5,000 frames in under 9 s, `test_performance.py`), enough for live frames and multi-month backtests.
