# Indicators (market engine, private)

Backend-authoritative SMC indicators. The terminal only renders these
outputs; it never recomputes a tradable signal.

| Plugin | Publishes | Needs |
|---|---|---|
| `daily_bias` | BOS / CHoCH / initial-break events, `trend`, PDH/PDL, NY session | chart bars |
| `fvg_ifvg` | FVG / IFVG zones, retest decay, inversion | chart bars |
| `order_breaker_blocks` | OB / breaker zones | chart bars |
| `liquidity_sweeps` | BSL / SSL / EQH / EQL pools, sweeps vs breaks | chart bars |
| `order_flow_pressure` | estimated delta, buyer/seller failure & breakout | chart bars + `intrabars:1m` |
| `auction_value_areas` | VAH / POC / VAL (chart and 1H profiles) | chart bars + `aux:1h` |
| `premium_discount_ote` | dealing range, premium / discount / EQ / OTE | chart bars |

All seven are **clean-room implementations of the V5 §6.2 text**
(`licensing_status = CLEAN_ROOM_FROM_SPEC`). The private Pine repository was
not accessible, so `parity_status = SPEC_ONLY` until Pine exports exist in
`fixtures/indicators/pine_exports/` (format in `golden.py`).

## Layout

- `sdk/protocol.py` – `IndicatorPlugin` protocol, `BarInput`, stream names.
- `sdk/base.py` – shared implementation (settings, snapshot, checkpoint).
- `sdk/engine.py` – `IndicatorEngine`: multi-timeframe, watermark-gated,
  no-look-ahead `BarFrame` builder (batch == streaming == replay).
- `sdk/registry.py` – build-time registry with lifecycle (disable/remove
  keeps metadata so saved strategies fail validation with a reason).
- `sdk/ta.py` – Wilder ATR, Pine-style pivots, score helpers.
- `catalog.py` – `build_default_registry()`.
- `service.py` – `IndicatorQueryService` + `CandleSource` port for the API.
- `golden.py` – scenario fixtures and Pine parity harness.
- `testing.py` – deterministic candle builders for every context's tests.

## Shared conventions

- Bars are closed `[start_ms, end_ms)` candles; events confirm at `end_ms`.
- ATR = Wilder RMA of true range, seeded with the SMA of the first
  `length` true ranges (Pine `ta.atr`). `None` until seeded.
- Pivots mirror `ta.pivothigh/pivotlow(left, right)`: known only at the
  close of the `right`-th following bar. **ASSUMPTION:** comparison is
  strict on both sides, so equal highs/lows inside the window form no pivot.
- Scores are plugin-specific 0–100 formation-quality integers (half-up
  rounding). They are not win rates and must not be summed across plugins.
- Ids come from `ids.stable_id`, so batch, streaming and replay agree.

## Adding a plugin

Create `plugins/<id>/plugin.py` + `README.md`, register it in `catalog.py`,
add a golden scenario under `fixtures/indicators/<id>/`, unit tests, and a
renderer in the terminal if it publishes new AOI kinds. No existing plugin
or the OMS changes (import-linter enforces plugin independence).
