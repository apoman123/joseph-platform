# Pine parity exports (pending)

TradingView exports that prove the Python indicator ports match the Pine
scripts. Until a file exists here, the parity test (`test_pine_parity` in
`tests/replay/indicators/test_golden_fixtures.py`) skips and every plugin
reports `parity_status = SPEC_ONLY`.

1. In TradingView, export per-bar values and events of the scripts at the
   pinned commit of the private `apoman123/PineScripts` repository, for a
   pinned instrument and range, into `<venue>/<symbol>/<tf>/<range>.jsonl`
   here (format in `joseph.indicators.golden`).
2. Run `uv run pytest tests/replay -k parity`; it reports the first divergent
   bar.
3. Fix divergences in the library, bump its pinned `rev` in `pyproject.toml`,
   then set the plugin's `parity_status` to `GOLDEN_VERIFIED`.
