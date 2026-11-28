# Developing filters

A filter is a `joseph_script.Filter` subclass that decides, for one
instrument at a time, whether it belongs in a screen. The screener runs a
filter over a market (for example every Binance perpetual or every Nasdaq
stock) and lists the matches. Read the [overview](README.md) first.

## Contents

- [Minimal filter](#minimal-filter)
- [What a filter can read](#what-a-filter-can-read)
- [Using history](#using-history)
- [Missing data](#missing-data)
- [Running a scan](#running-a-scan)
- [Checklist](#checklist)

## Minimal filter

```python
import joseph_script


class LiquidMovers(joseph_script.Filter):
    """Keeps instruments with enough turnover and a large daily move."""

    ID = "liquid_movers"
    TITLE = "Liquid movers"
    INPUTS = {
        "min_turnover": joseph_script.Input(10_000_000.0, title="Min 24h turnover"),
        "min_change": joseph_script.Input(5.0, title="Min |change| %", minimum=0),
    }

    def matches(self, ctx):
        turnover = ctx.snapshot.get("turnover_24h_quote")
        change = ctx.snapshot.get("change_24h_pct")
        if turnover is None or change is None:
            return False
        return (float(turnover) >= float(ctx.input("min_turnover"))
                and abs(float(change)) >= float(ctx.input("min_change")))
```

`matches` runs once per instrument and returns `True` or `False`. Any other
return value is an error.

## What a filter can read

| Member | Content |
| --- | --- |
| `ctx.snapshot` | The instrument's current market snapshot (last price, change, volume, turnover and the other metrics its source provides; absent fields are missing) |
| `ctx.instrument` | Instrument metadata: venue, market type, symbol, tick size and similar |
| `ctx.instrument_id` | Canonical id, such as `nasdaq:stock:AAPL` |
| `ctx.history`, `ctx.series(...)`, `ctx.sma(...)` | Closed bars, when the scan requests history |
| `ctx.input(...)` | The filter's inputs |

`ctx.snapshot` fields: `instrument_id`, `last`, `mark`, `index`, `best_bid`,
`best_ask`, `spread_pct`, `turnover_24h_quote`, `volume_24h_base`,
`change_24h_pct`, `funding`, `open_interest`, `feed_state`, `source`,
`as_of_ms`, `data_age_ms`, `stale`, plus `evaluated_at_ms` (when the scan
evaluated it). Prices and volumes are decimal strings; convert them with
`float(...)` after checking for `None`.

To read data beyond the snapshot, declare it, and the scanner loads it into
the snapshot before calling `matches`:

```python
METRICS = ("order_book", "open_interest", "funding")

def matches(self, ctx):
    book = ctx.snapshot.get("order_book")   # bids/asks, or None when unavailable
```

## Using history

A scan can request up to 500 closed bars per instrument at one timeframe. The
host replays them through `on_bar` (if your filter defines it), then calls
`matches`. Snapshot-only scans skip history and are much faster. Request
history only when the rule needs it.

```python
class AboveAverage(joseph_script.Filter):
    ID = "above_average"
    INPUTS = {"length": joseph_script.Input(50, title="Length", minimum=1, maximum=500)}

    def matches(self, ctx):
        average = ctx.sma(length=int(ctx.input("length")))
        return average is not None and ctx.bar["close"] > average
```

## Missing data

The scanner keeps three outcomes apart: **match**, **no match** and
**unavailable**. When a value your rule needs is missing or stale, raise
`joseph_script.Unavailable` with a reason instead of guessing:

```python
def matches(self, ctx):
    turnover = ctx.snapshot.get("turnover_24h_quote")
    if turnover is None:
        raise joseph_script.Unavailable("no 24h turnover from this venue")
    return float(turnover) >= float(ctx.input("minimum"))
```

The instrument is then listed under **Unavailable** with your message, never
as a match. Return `False` only when the data is there and the rule fails.
Any other exception is reported as a filter error for that instrument; the
rest of the scan continues.

## Running a scan

In the terminal, open **Screener**:

1. Choose the venue, market type, bar timeframe (for filters that use
   history) and the maximum number of symbols.
2. Add conditions: built-in filters with their inputs, or your own Filter
   scripts. All conditions must match (AND).
3. **Scan**. Progress is shown and the scan can be cancelled; partial results
   stay. Results list real snapshot columns; clicking a row opens the chart.
   Unavailable instruments are listed separately with their reasons.
4. Save the setup as a preset.

Built-in filters:

| Id | Rule (main inputs) |
| --- | --- |
| `builtin:liquidity-qualification` | Liquidity gates per asset class (turnover, spread, depth, open interest, funding risk, data age) |
| `builtin:price`, `builtin:change`, `builtin:volume`, `builtin:turnover` | Value `above`, `below` or `between` |
| `builtin:relative-volume` | Volume versus its N-bar average |
| `builtin:gap` | Opening gap % |
| `builtin:price-vs-ma` | Price above or below an SMA/EMA |
| `builtin:rsi-range` | RSI in a range |
| `builtin:new-high-low` | New N-bar high or low |

Through the API, a scan of several conditions is submitted with:

```http
POST /v1/script-scans
{"venue": "binance", "market_type": "perp", "max_symbols": 300,
 "history_bars": 100, "tf": "1h", "order_by": "turnover",
 "conditions": [
   {"script_id": "builtin:turnover", "inputs": {"operator": "above", "value": 50000000}},
   {"script_id": "builtin:price-vs-ma", "inputs": {"ma_type": "EMA", "length": 50, "position": "above"}}
 ]}
```

It returns `202` with a job. Poll `GET /v1/script-scans/{job_id}` for
`status`, `scanned`/`total`, `matches`, `unavailable` and `errors`, and stop
it with `POST /v1/script-scans/{job_id}/cancel`. A single filter can also be
scanned with `POST /v1/scripts/{script_id}/scan`. Every condition is pinned
when the scan starts: own scripts by version, catalog scripts by source hash.
`order_by: "turnover"` evaluates the most active instruments first, which
matters when `max_symbols` is smaller than the market.

Scans run as background work, so they never delay chart loading. Built-in
and plugin filters run in the market engine; your own filters need the
isolated runtime.

## Checklist

- [ ] One class, inherits `joseph_script.Filter`, implements `matches(self, ctx) -> bool`.
- [ ] Thresholds are `Input`s.
- [ ] Missing values raise `joseph_script.Unavailable`; they never pass.
- [ ] History requested only when needed, and long enough for the warm-up.
