# TradingView parity exports (pending)

TradingView exports that prove the built-in indicators match TradingView's
own built-ins bar for bar. Until a file exists here, the parity test
(`test_pine_parity` in `tests/replay/indicators/test_golden_fixtures.py`)
skips and every built-in reports `parity_status = SPEC_ONLY`.

| Built-in | TradingView reference |
|---|---|
| `sma` | `ta.sma(source, length)` |
| `ema` | `ta.ema(source, length)` |
| `bollinger_bands` | `ta.bb(source, length, mult)` |
| `rsi` | `ta.rsi(source, length)` |
| `atr` | `ta.atr(length)` |
| `anchored_vwap` | `ta.vwap(source, anchor_change, stdev_mult)` |

1. In TradingView, plot the reference built-in with the plugin's default
   settings (or the settings named in the export header) on a pinned
   instrument and range, and export the per-bar values into
   `<venue>/<symbol>/<tf>/<range>.jsonl` here (format in
   `joseph.indicators.golden`; `pine_commit` names the script version used
   for the export).
2. Run `uv run pytest tests/replay -k parity`; it reports the first
   divergent bar.
3. Fix divergences in the built-in, then set its `parity_status` to
   `GOLDEN_VERIFIED`.
