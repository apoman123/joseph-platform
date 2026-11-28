# Developing indicators

An indicator is a `joseph_script.Indicator` subclass. It reads closed bars and
draws on the chart: plots, levels, fills, markers and drawings. It cannot
place orders. Read the [overview](README.md) first for declarations, inputs
and the full `ctx` reference.

## Contents

- [Minimal indicator](#minimal-indicator)
- [Price pane or separate pane](#price-pane-or-separate-pane)
- [Keeping state between bars](#keeping-state-between-bars)
- [Bands and fills](#bands-and-fills)
- [Levels and drawings](#levels-and-drawings)
- [Higher timeframes and other symbols](#higher-timeframes-and-other-symbols)
- [Provisional bars](#provisional-bars)
- [Alert conditions from an indicator](#alert-conditions-from-an-indicator)
- [Using it on a chart](#using-it-on-a-chart)
- [Checklist](#checklist)

## Minimal indicator

```python
import joseph_script


class Midpoint(joseph_script.Indicator):
    """Plots the midpoint of each bar's range."""

    ID = "midpoint"
    TITLE = "Bar midpoint"
    OVERLAY = True
    INPUTS = {"color": joseph_script.Input("#ff9800", title="Color", group="Style")}

    def on_bar(self, ctx):
        bar = ctx.bar
        ctx.plot("Mid", (bar["high"] + bar["low"]) / 2, color=ctx.input("color"))
```

`on_bar` runs once per bar, oldest first. Each `ctx.plot(name, value)` call
sets the series value at the current bar; calling it again on the same bar
replaces the value.

## Price pane or separate pane

`OVERLAY = True` draws on the candles, sharing the price scale (moving
averages, bands, levels). `OVERLAY = False` opens a pane below the chart with
its own scale (oscillators, volume studies). Reference lines such as 30/70
belong in `hline`:

```python
class RelativeStrength(joseph_script.Indicator):
    ID = "my_rsi"
    TITLE = "Relative strength"
    OVERLAY = False
    INPUTS = {
        "length": joseph_script.Input(14, title="Length", minimum=1, maximum=500),
        "upper": joseph_script.Input(70, title="Upper band", minimum=50, maximum=100),
        "lower": joseph_script.Input(30, title="Lower band", minimum=0, maximum=50),
    }

    def __init__(self):
        self.previous = None
        self.gain = None
        self.loss = None
        self.count = 0

    def on_bar(self, ctx):
        length = int(ctx.input("length"))
        close = ctx.bar["close"]
        if self.previous is not None:
            change = close - self.previous
            up, down = max(change, 0.0), max(-change, 0.0)
            self.count += 1
            if self.gain is None:
                self.gain, self.loss = up, down
            else:  # Wilder smoothing
                self.gain = (self.gain * (length - 1) + up) / length
                self.loss = (self.loss * (length - 1) + down) / length
        self.previous = close
        value = None
        if self.count >= length:
            value = 100.0 if self.loss == 0 else 100 - 100 / (1 + self.gain / self.loss)
        ctx.plot("RSI", value, color="#7e57c2")
        ctx.hline("Upper", float(ctx.input("upper")))
        ctx.hline("Lower", float(ctx.input("lower")))
        ctx.fill("Upper", "Lower", color="#7e57c2", opacity=0.08)
```

## Keeping state between bars

A new instance is created for every run, so instance attributes are safe
running state. Prefer O(1) updates per bar:

| Need | Pattern |
| --- | --- |
| Rolling window | `collections.deque` plus a running sum |
| Recursive average (EMA, Wilder) | keep the previous value |
| Highest/lowest of the last N | a monotonic deque, or `max(self.window)` for small N |
| Values from earlier bars | your own list, or `ctx.series("close")[-n]` |

`ctx.series(...)` builds a new list each call. Use it sparingly inside
`on_bar`.

## Bands and fills

```python
upper = mean + width * deviation
lower = mean - width * deviation
ctx.plot("Basis", mean, color="#2962ff")
ctx.plot("Upper", upper, color="#f23645")
ctx.plot("Lower", lower, color="#089981")
ctx.fill("Upper", "Lower", color="#2962ff", opacity=0.1)
```

`fill` shades between two named plots or `hline` levels. Per-bar colours go in
the `color` argument of `plot` (for example green above zero, red below for a
histogram).

## Levels and drawings

Drawings are objects anchored to time and price. They persist from the bar
that created them until you delete or replace them. Give each one a stable
`id` so it is updated in place instead of creating a new object on every bar:

```python
def on_bar(self, ctx):
    if self.session_changed(ctx):
        high, low, close = self.previous_session
        pivot = (high + low + close) / 3
        start = ctx.bar["time_ms"]
        ctx.line(start, pivot, start + self.session_ms, pivot,
                 id=f"P:{start}", color="#ffb74d")
        ctx.label("P", time_ms=start, price=pivot, id=f"P-label:{start}")
```

Available: `line` (with `extend="right"` for rays), `polyline`, `box` for
zones, `label`, `linefill` between two lines, and fixed-position `table` for
dashboards. Remove an object with `ctx.delete(id)`. At most 500 drawings
exist at once, so delete old ones when you keep a rolling set (for example,
the last 20 zones).

Markers for the current bar: `plotshape`, `plotchar`, `plotarrow`. Colour the
bar or its background with `barcolor` and `bgcolor`.

## Higher timeframes and other symbols

Declare what you need, then read it with `ctx.request`:

```python
REQUESTS = {"daily": joseph_script.Request("1d", limit=300)}

def on_bar(self, ctx):
    closes = ctx.request("daily")
    if len(closes) >= 20:
        ctx.plot("Daily SMA 20", sum(closes[-20:]) / 20, style="step")
```

Only daily bars that had **closed** by the current chart bar are returned.
The current day's forming bar is never visible. `ctx.closed_bar("daily")`
returns the latest closed daily bar as a dict.

To look **inside** each chart bar (for example, to classify buying and
selling volume), declare an intrabar request and read it per bar:

```python
REQUESTS = {
    "lower": joseph_script.Request(
        "auto", intrabars=True, limit=3000, unless="taker_buy_volume"
    )
}

def on_bar(self, ctx):
    taker = ctx.bar.get("taker_buy_volume")
    if taker is None:
        parts = ctx.intrabars("lower")     # lower-timeframe bars in this bar
        ...
```

`unless="taker_buy_volume"` skips the extra download wherever the venue
already reports taker volume. The built-in Volume Delta is the full example.

For periods on the exchange calendar (sessions, trading weeks, months),
declare `PERIODS = ("1d", "1w", "1M")` and call `ctx.period("1d")` to get the
current period's start and the next period's start in UTC milliseconds. The
built-in Pivot Points uses this.

## Provisional bars

On a chart, the newest bar is still forming. Indicators are re-run every few
seconds with that bar included and `ctx.is_confirmed == False`, so lines
follow the live price. When the bar closes, the next run sees it confirmed.
Do not keep state that assumes a bar is final when `ctx.is_confirmed` is
false. Alerts called on a provisional bar are ignored.

## Alert conditions from an indicator

An indicator may publish conditions that users can turn into alerts:

```python
ctx.alertcondition(
    crossed_up, "Cross above", f"{ctx.instrument_id} crossed above the band",
)
```

See [Alerts](ALERTS.md) for how conditions become notifications.

## Using it on a chart

1. Open the **Python editor**, choose **Indicator**, paste or write the source,
   **Validate**, then **Save version** (each save is an immutable version).
2. **Preview on closed bars** runs it on the active chart.
3. Add it from the indicator picker under **My scripts**. Each chart keeps its
   own inputs, style and visibility. The legend's gear opens the settings
   generated from `INPUTS`, `{}` opens the source, the eye hides it and the
   cross removes it.

The picker has **Built-ins**, **Plugins**, **My scripts** and **My
collection**, with search and categories (from `CATEGORY`). Open a built-in's
source with `{}`, paste it into a new indicator in the editor, and change it
from there. Operator-installed packages appear under **Plugins** (see [Plugin
packages](PLUGIN_PACKAGES.md)).

Built-in indicators: `builtin:sma`, `builtin:ema`, `builtin:bollinger_bands`,
`builtin:rsi`, `builtin:atr`, `builtin:volume_delta`, `builtin:pivot_points`,
`builtin:anchored_vwap`, `builtin:regression_channel`. New charts start with
EMA, Volume Delta and Pivot Points. The anchored VWAP and regression channel
drawing tools run their built-ins with the anchor as an input.

## Checklist

- [ ] One class, inherits `joseph_script.Indicator`, implements `on_bar(self, ctx)`.
- [ ] `OVERLAY` matches the scale of the values.
- [ ] Every tunable number is an `Input` with sensible bounds.
- [ ] Warm-up bars plot `None`, never a made-up value.
- [ ] O(1) work per bar; no full recomputation of history.
- [ ] Drawings use stable ids; old ones are deleted.
- [ ] No look-ahead: no future bars, higher-timeframe data only from `ctx.request`.
