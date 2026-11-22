# ADR 0009 — Indicators live in an external library

Status: accepted (2026-11-22). Supersedes the "first-party plugins" part of ADR 0004; the plugin SDK itself is unchanged.

## Context

The owner's Pine scripts in `apoman123/PineScripts` are the reference for every indicator. Until now the market engine carried seven clean-room plugins written from the V5 text, which drifted from the scripts (break rules, inversions, dealing-range resets). The owner wants a hard split: "平台歸平台，指標歸指標" — the platform is a platform, indicators are indicators.

## Decision

- Port each Pine script line by line into a standalone package, `PineScripts/python_indicators` (`joseph-smc-indicators`, import `smc_indicators`), with no platform dependency. Contract: the result for closed bar k equals the Pine state on bar k with `barstate.isconfirmed`. Floats are kept deliberately: Pine computes in doubles.
- The market engine depends on it as a git dependency **pinned to a commit** (`[tool.uv.sources]`, `uv.lock`). Upgrading indicators is an explicit, reviewable `rev` bump.
- `joseph/indicators/packaged/` contains only adapters to the SDK: settings mirroring Pine inputs, data streams, decimal conversion at the boundary, deterministic AOI ids, ended-AOI inference, and the published-values contract. An import-linter contract keeps adapters free of infrastructure.
- Purity and checkpoints: library objects are mutable, so the newest state mutates shared live objects (fast path) while every state can rebuild its own objects from a pickled base taken every 32 bars plus a persistent log of the bar inputs since (the library is deterministic). Checkpoints are unpickled with an allowlist that only admits classes defined in `smc_indicators` / the adapter package and inert standard types.
- CI and images reach the private repository with a dedicated read-only deploy key (`PINESCRIPTS_DEPLOY_KEY`), an ssh-agent and BuildKit SSH forwarding. GitHub's host key is pinned.

## Consequences

- One source of truth per indicator; Pine changes are ported once and every platform consumer (charts, strategies, backtests, alerts) moves together on the next `rev` bump.
- CI fails until the deploy key secret exists (clear error message). Builds need SSH access to GitHub.
- The LuxAlgo-derived OTE module is CC BY-NC-SA 4.0: the platform must stay non-commercial while it is enabled (see `reports/LICENSING_REPORT.md`).
- All eight plugins together take about 1.7 ms per 5m frame (5,000 frames in under 9 s, measured by `test_performance.py`), fast enough for live frames and multi-month backtests.
