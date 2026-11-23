# Pine parity exports (pending)

TradingView exports that prove the Python indicator ports match the Pine
scripts. Export the scripts at the pinned commit of the private
`apoman123/PineScripts` repository and place the files here as
`<venue>/<symbol>/<tf>/<range>.jsonl` (format in `joseph.indicators.golden`).
The parity test (`test_pine_parity` in
`tests/replay/indicators/test_golden_fixtures.py`) skips until a file exists,
then compares every bar and reports the first divergences. Until then every
plugin reports `parity_status = SPEC_ONLY`. Steps:
[Pine source record](../../../../docs/reports/PINE_SOURCE_REVISION.md#reaching-golden_verified).
