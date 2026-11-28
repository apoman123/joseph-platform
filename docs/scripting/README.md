# Script development guides

Every indicator, strategy, alert and filter in Joseph is a Python class that
inherits one abstract base from the `joseph_script` package. This applies
everywhere: the platform's default scripts, scripts you write in the terminal's
**Python editor**, and scripts installed from plugin packages. There is no
other way to define these four things.

| Guide | Base class | You implement |
| --- | --- | --- |
| [Indicators](INDICATORS.md) | `joseph_script.Indicator` | `on_bar(self, ctx)`: plots and drawings |
| [Strategies](STRATEGIES.md) | `joseph_script.Strategy` | `on_bar(self, ctx)`: plots plus simulated orders |
| [Alerts](ALERTS.md) | `joseph_script.Alert` | `on_bar(self, ctx)`: `ctx.alert` / `ctx.alertcondition` |
| [Filters](FILTERS.md) | `joseph_script.Filter` | `matches(self, ctx) -> bool` |
| [Plugin packages](PLUGIN_PACKAGES.md) | any of the above | ship scripts as an installable package |

A fifth base class, `joseph_script.Library`, shares code between your own
scripts (see [Libraries](#libraries)).

## Contents

- [Where scripts come from and where they run](#where-scripts-come-from-and-where-they-run)
- [Anatomy of a script](#anatomy-of-a-script)
- [Declarations](#declarations)
- [Inputs](#inputs)
- [Additional symbols and timeframes](#additional-symbols-and-timeframes)
- [The bar loop](#the-bar-loop)
- [Context reference](#context-reference)
- [Libraries](#libraries)
- [Limits](#limits)
- [Rules every script follows](#rules-every-script-follows)
- [Testing a script](#testing-a-script)
- [Built-in scripts as examples](#built-in-scripts-as-examples)

## Where scripts come from and where they run

| Origin | Catalog id | Source | Runs |
| --- | --- | --- | --- |
| Built-in (ships with the market engine) | `builtin:<ID>` | Public: the `{}` button shows it, so you can start your own script from it (the Strategy panel has **Copy to my scripts**) | In the market engine process |
| Plugin package (installed by the operator) | `plugin:<entry point>:<ID>` | Protected | In the market engine process |
| Your script (Python editor) | your slug, e.g. `my-ema` | Yours; immutable versions | Only in the isolated runner container, which must be enabled (`JOSEPH_SCRIPT_RUNTIME=docker`, see [Python scripts](../PYTHON_SCRIPTS_AND_MCP.md#enable-python-execution)) |

Built-in and plugin scripts are trusted code chosen by the operator. Your own
scripts are untrusted, so they run in a container with no network, no files,
no credentials and bounded CPU, memory and time. All three kinds share the same
base classes and the same `ctx` API, so a built-in copied into the editor runs
unchanged.

`GET /v1/scripts/catalog` lists the built-in and plugin scripts with their
declared inputs. `GET /v1/scripts/catalog/{id}` returns one, with source when it
is public.

## Anatomy of a script

```python
from __future__ import annotations

import joseph_script


class Momentum(joseph_script.Indicator):
    """Plots the close minus the close `length` bars ago."""

    ID = "momentum"
    TITLE = "Momentum"
    OVERLAY = False
    INPUTS = {
        "length": joseph_script.Input(10, title="Length", minimum=1, maximum=500),
    }

    def __init__(self):
        self.closes = []

    def on_bar(self, ctx):
        length = int(ctx.input("length"))
        self.closes.append(ctx.bar["close"])
        value = None
        if len(self.closes) > length:
            value = self.closes[-1] - self.closes[-1 - length]
        ctx.plot("Momentum", value, color="#26a69a", style="histogram")
```

A source file declares exactly one concrete class that inherits exactly one
role base. Saving fails when a file has zero or several script classes, mixes
roles, or misses the required method. Validation parses the source but does
not run it.

`joseph_script.Indicator` is preferred to `from joseph_script import
Indicator`. Both work; the platform's own code uses the first form.

## Declarations

Class attributes describe the script without running it. All are optional for
your own scripts, whose saved name and settings come from the editor. Built-in
and plugin scripts must set `ID`.

| Attribute | Type | Meaning |
| --- | --- | --- |
| `ID` | `str` | Catalog slug: lowercase letters, digits, `-`, `_`; starts with a letter |
| `TITLE` | `str` | Display name; the class name when empty |
| `SHORT_TITLE` | `str` | Compact legend name; `TITLE` when empty |
| `DESCRIPTION` | `str` | One or two plain sentences for catalogs |
| `CATEGORY` | `str` | Catalog section, such as `Trend` or `Volume` |
| `OVERLAY` | `bool` | `True` draws on the price pane; `False` opens a separate pane |
| `INPUTS` | `dict[str, Input]` | User settings, in display order |
| `REQUESTS` | `dict[str, Request]` | Other symbols, timeframes or intrabars read with `ctx.request`, `ctx.closed_bar` and `ctx.intrabars` |
| `PERIODS` | `tuple[str, ...]` | Calendar periods (`"1d"`, `"1w"`, `"1M"`) whose bounds `ctx.period` returns |
| `METRICS` | `tuple[str, ...]` | Filters only: `order_book`, `open_interest`, `funding` loaded before `matches` |

## Inputs

```python
INPUTS = {
    "length": joseph_script.Input(14, title="Length", minimum=1, maximum=500, step=1),
    "source": joseph_script.Input("close", title="Source",
                                  options=("open", "high", "low", "close")),
    "show_bands": joseph_script.Input(True, title="Show bands"),
    "color": joseph_script.Input("#2962ff", title="Line", group="Style"),
}
```

`Input(default, *, title="", minimum=None, maximum=None, options=None,
step=None, group="", tooltip="")`. The settings dialog is generated from these
declarations: a boolean default gives a checkbox, `options` a choice list, a
default starting with `#` a colour picker, a number a numeric field checked
against `minimum`/`maximum`, and anything else a text field. Inputs in the
same `group` share a section.

For built-in and plugin scripts the dialog comes from `INPUTS`. For your own
scripts, the host does not run the source to read declarations: the dialog
edits the inputs stored in the version's **Default inputs (JSON)** field in
the Python editor (the templates fill it in, for example `{"length": 20}`),
and each `ctx.input(name, default, ...)` call supplies the default and the
bounds at run time.

Read a value with `ctx.input("length")`. The host merges the declared defaults
with the user's chart, alert or activation settings before the run starts.
`ctx.input(name, default, minimum=..., maximum=..., options=...)` also checks
a value at run time and raises `ValueError` when it is out of range.

## Additional symbols and timeframes

```python
REQUESTS = {
    "daily": joseph_script.Request("1d", limit=200),
    "index": joseph_script.Request("1h", instrument_id="nasdaq:etf:QQQ"),
}

def on_bar(self, ctx):
    daily_closes = ctx.request("daily")              # closed daily bars so far
    index_highs = ctx.request("index", "high")
```

A script declares at most four requests. Reading them:

| Call | Returns |
| --- | --- |
| `ctx.request(alias, field="close")` | That field of every source bar closed by the current bar's close |
| `ctx.closed_bar(alias)` | The latest source bar closed by now (a dict), or None |
| `ctx.intrabars(alias)` | For a `Request(..., intrabars=True)`: the lower-timeframe bars inside the current bar |

A higher timeframe never leaks a value that was not yet known.

`Request(tf, *, instrument_id=None, limit=500, intrabars=False, tf_input="",
unless="", unless_input="")`:

- `tf="auto"` with `intrabars=True` lets the host pick the lower timeframe
  for the chart: 1s on seconds charts, 1m intraday, 5m on daily, 1h on weekly
  and monthly.
- `tf_input` names an input whose value replaces `tf`, unless that value is
  `"auto"`.
- `unless="taker_buy_volume"` fetches intrabars only for bars that lack that
  field; `unless_input` makes this depend on a boolean input.
- `limit` is at most 500 bars, or 5,000 for intrabars. Only the newest bars'
  intrabars are fetched.

For session-aware periods declare `PERIODS = ("1d", "1w")`. Then
`ctx.period("1d")` returns `(start_ms, next_start_ms)` of the period that
contains the current bar, using the instrument's session calendar.

## The bar loop

The host loads the requested history, creates one fresh instance of the class,
and calls the role's method once per bar, oldest first:

1. `ctx.history` grows by one bar; `ctx.bar` is that newest bar.
2. Your method runs. It sees only bars up to and including `ctx.bar`.
3. Strategy orders queued on this bar fill on the next bar's open.

Keep running state on `self` (set it up in `__init__`) or in the `ctx.state`
dictionary. Incremental state, such as a running sum or the previous average,
keeps a 2,000-bar run fast. Recomputing everything from `ctx.series(...)` on
every bar is quadratic and slow.

Bars are closed and confirmed. The exception is an indicator drawn on a chart,
whose last bar may be provisional (`ctx.is_confirmed` is `False`). Alerts and
strategy orders never act on a provisional bar.

## Context reference

Available to every role unless noted.

**Data**

| Member | Returns |
| --- | --- |
| `ctx.bar` | Current bar: `time_ms`, `end_ms`, `open`, `high`, `low`, `close`, `volume`, `is_final`; optional `quote_turnover`, `trade_count`, `taker_buy_volume`, `taker_buy_method` (None when the source does not publish them) |
| `ctx.history` | Bars up to and including the current one |
| `ctx.series(field="close")` | One OHLCV field over `history` |
| `ctx.optional_series(field)` | `quote_turnover`, `trade_count` or `taker_buy_volume`, with None gaps |
| `ctx.sma(field="close", length=20)` | Simple average over the last `length` bars, or None |
| `ctx.request(alias, field)`, `ctx.closed_bar(alias)`, `ctx.intrabars(alias)` | Declared additional series |
| `ctx.period(tf)` | Bounds of the declared calendar period containing the bar |
| `ctx.bar_index`, `ctx.is_first`, `ctx.is_last`, `ctx.is_confirmed`, `ctx.total_bars` | Position in the run |
| `ctx.instrument_id`, `ctx.instrument`, `ctx.timeframe` | What is being evaluated |
| `ctx.inputs`, `ctx.input(...)` | Settings |
| `ctx.state` | A dictionary kept for the whole run |
| `ctx.snapshot` | Filters: the instrument's current market snapshot |

**Plots** (indicators, strategies; alerts may plot for previews)

| Call | Draws |
| --- | --- |
| `ctx.plot(name, value, color=, style=, width=, offset=, display=)` | A series. Styles: `line`, `step`, `area`, `histogram`, `columns`, `circles`, `crosses`. `None` leaves a gap. `display="data_window"` shows the value only in the data window. Up to 64 series |
| `ctx.hline(name, price, color=, style=)` | A horizontal level |
| `ctx.fill(first, second, color=, opacity=)` | Shading between two plots or levels |
| `ctx.bgcolor(color, opacity=)`, `ctx.barcolor(color)` | Colour the current bar's background or candle |
| `ctx.plotshape(shape, text=, position=, color=)`, `ctx.plotchar`, `ctx.plotarrow(value)` | Markers on the current bar |
| `ctx.plotcandle(name, o, h, l, c)`, `ctx.plotbar(...)` | Custom OHLC series |

**Drawings** (persist until deleted; reuse the returned id to update; up to
500 objects)

`ctx.line(from_ms, from_price, to_ms, to_price, extend=)`,
`ctx.polyline(points, closed=, fill=)`,
`ctx.box(from_ms, to_ms, lower, upper, text=)`,
`ctx.label(text, time_ms=, price=, position=)`,
`ctx.linefill(first_line, second_line)`, `ctx.table(name, rows, position=)`,
`ctx.delete(id)`.

**Events**

| Call | Role | Meaning |
| --- | --- | --- |
| `ctx.alert(message, name=, severity=)` | indicator, strategy, alert | Records a condition on the current closed bar |
| `ctx.alertcondition(condition, title, message, severity=)` | indicator, strategy, alert | `alert` when `condition` is true |
| `ctx.entry(side, quantity)`, `ctx.close()` | strategy | Queue simulated orders; see [Strategies](STRATEGIES.md) |

Colours are hex strings (`#rgb`, `#rrggbb`, `#rrggbbaa`). Text is plain text;
the terminal never renders HTML from a script.

## Libraries

A `joseph_script.Library` returns reusable objects from `exports()`:

```python
class TA(joseph_script.Library):
    def exports(self):
        return {"ema_step": ema_step, "true_range": true_range}
```

Pin it in the importing script's **Pinned libraries** field, e.g.
`{"ta": {"script_id": "my-ta", "version": 3}}`, then
`from libraries.ta import ema_step`. Libraries are available to your own
scripts. Built-in scripts are self-contained and import nothing but
`joseph_script` and the standard library.

## Limits

| Limit | Value |
| --- | --- |
| Source size | 64 KiB, 10,000 syntax nodes |
| Bars per run | 2 to 5,000 (chart studies, backtests) |
| Plot series | 64 |
| Persistent drawings | 500; per-bar visuals 16,000 |
| Alert events per run | 2,000 newest (older events are dropped) |
| Requests | 4 aliases, 500 bars each (5,000 intrabars) |
| Your scripts in the runner | 10 s wall clock (operator setting, at most 30 s), 256 MiB, 1 CPU, no network |

## Rules every script follows

- **No look-ahead.** Use only `ctx.bar`, `ctx.history` and `ctx.request`.
  Never index bars after the current one.
- **Deterministic.** The same bars and inputs give the same output. Do not
  read the clock, random numbers without a fixed seed, or the environment.
- **Missing is not zero.** Optional fields and warm-up values are `None`.
  Plot `None`, and keep a filter's answer honest rather than inventing data.
- **Closed bars act.** Alerts and orders happen on confirmed bars only; the
  host enforces this as well.
- **Bounded output.** Reuse drawing ids instead of creating a new object on
  every bar.

## Testing a script

In the terminal, **Preview on closed bars** (strategies: **Run backtest**) in
the Python editor evaluates the saved version on the chart's symbol and shows
plots, alerts, fills and errors. From code or MCP, run any script on real
closed bars with:

```http
POST /v1/scripts/{script_id}/run
{"instrument_id": "binance:perp:BTCUSDT", "tf": "1h", "limit": 500,
 "inputs": {"length": 20}}
```

The response contains `plots`, `visuals`, `alerts`, `fills`, `orders`,
`trades`, `equity_curve`, `metrics` and `matched` (filters), plus provenance
(`data_source`, `feed_state`, `complete`). `POST /v1/scripts/run-batch` runs up
to 16 studies on one candle fetch, which is how the terminal draws a chart's
studies.

Built-in and plugin classes can also be unit-tested directly with
`joseph.scripts.worker.run_class(cls, bundle)`; see
`joseph-market-engine/tests/unit/scripts/` for examples.

## Built-in scripts as examples

Each built-in is one short module in
`joseph-market-engine/src/joseph/scripts/builtin/{indicators,strategies,alerts,filters}/`.
They are the reference examples for each guide. Adding a new built-in to the
platform (registration, DEMO mirror, generated catalog) is described in
[Extending Joseph](../EXTENDING.md#add-or-change-an-indicator-python).
