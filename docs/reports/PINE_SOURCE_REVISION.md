# Pine source revision record

| Item | Value |
|---|---|
| Baseline named in V5 | `apoman123/PineScripts` @ `cf1ea56c059719c3c384de65776ac3a9c1831a09` |
| Access check (2026-11-22) | `git ls-remote https://github.com/apoman123/PineScripts.git` with the locally authenticated GitHub account returned *Repository not found* — the account has no access, or the repository was moved |
| HEAD read | Not possible |
| Consequence | All seven indicators and the qualification filter were implemented **clean-room from the V5 §5–§6 functional text**. Every plugin reports `licensing_status: CLEAN_ROOM_FROM_SPEC` and `parity_status: SPEC_ONLY`. Ambiguities are recorded as `ASSUMPTION:` in each plugin README |

## To reach `GOLDEN_VERIFIED`

1. Grant the build account read access and record the HEAD commit here.
2. For each script, export per-bar values/events from TradingView for a pinned instrument and range into `joseph-market-engine/fixtures/{venue}/{symbol}/{tf}/{range}.jsonl` (format in `fixtures/indicators/README.md`).
3. Run `uv run pytest tests/replay -k parity`; the harness compares bar by bar and skips while exports are absent.
4. Diff any `ASSUMPTION:` against the Pine source; change behavior only through a new `indicator_version`.

The OTE script carries a LuxAlgo CC BY-NC-SA 4.0 notice. The clean-room implementation copies no Pine code; see `LICENSING_REPORT.md` before any public or commercial release.
