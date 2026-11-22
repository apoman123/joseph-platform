# Pine parity exports (pending)

Place exports from the pinned private `apoman123/PineScripts` commit here as
`<venue>/<symbol>/<tf>/<range>.jsonl` (see `joseph.indicators.golden`). The
parity harness (`tests/replay/indicators/test_pine_parity.py`) skips until a
file exists, then compares every bar and reports the first divergences.
Until then every plugin reports `parity_status = SPEC_ONLY`.
