# Developing strategies

A strategy is a `joseph_script.Strategy` subclass. Like an indicator it reads
closed bars and can draw, and it also queues **orders** on a simulated
account. The same class is used for backtests on the chart, for alert-only
signals, and for paper, shadow and (with consent) live deployments. Read the
[overview](README.md) first.

## Contents

- [Minimal strategy](#minimal-strategy)
- [How orders fill](#how-orders-fill)
- [Order API](#order-api)
- [Position and account state](#position-and-account-state)
- [Stops, targets and risk](#stops-targets-and-risk)
- [Backtesting](#backtesting)
- [From backtest to signals and orders](#from-backtest-to-signals-and-orders)
- [Checklist](#checklist)

## Minimal strategy

```python
import joseph_script


class MovingAverageCross(joseph_script.Strategy):
    """Goes long when the fast average crosses above the slow one."""

    ID = "ma_cross"
    TITLE = "Moving average cross"
    OVERLAY = True
    INPUTS = {
        "fast": joseph_script.Input(10, title="Fast length", minimum=1, maximum=500),
        "slow": joseph_script.Input(30, title="Slow length", minimum=2, maximum=1000),
        "quantity": joseph_script.Input(1.0, title="Quantity", minimum=0),
    }

    def __init__(self):
        self.previous = None

    def on_bar(self, ctx):
        fast = ctx.sma(length=int(ctx.input("fast")))
        slow = ctx.sma(length=int(ctx.input("slow")))
        ctx.plot("Fast", fast, color="#2962ff")
        ctx.plot("Slow", slow, color="#ff9800")
        if fast is None or slow is None:
            return
        above = fast > slow
        if self.previous is False and above and ctx.position_size <= 0:
            ctx.entry("long", float(ctx.input("quantity")))
        elif self.previous is True and not above and ctx.position_size > 0:
            ctx.close()
        self.previous = above
```

## How orders fill

An order queued while processing bar *N* fills at the **open of bar N + 1**,
adjusted by the configured slippage against you, minus commission. The
strategy never sees a fill at a price it could not have traded at.
Consequences:

- An order queued on the last bar stays unfilled. Backtest results report it
  as an unfilled intent.
- `ctx.position_size` changes on the bar after `entry`, not immediately.
- Signals for live use are produced on the bar where you call `entry`, so the
  execution engine can act on the next bar, as in the backtest.

## Order API

| Call | Effect |
| --- | --- |
| `ctx.entry("long" \| "short", quantity=1.0, *, stop=None, target=None, comment="")` | Targets a long or short position of `quantity`, reversing an opposite one. `stop` and `target` are protective prices: `stop` must be below the close for a long and above it for a short, and `target` the other way round. `comment` (at most 120 characters) labels the order |
| `ctx.close(comment="")` | Targets a flat position |

Each call replaces the order queued earlier on the same bar, so the last call
on a bar wins. Every order is recorded in the result's `orders` list with its
outcome (filled, rejected or still pending at the end of the data).

## Position and account state

| Member | Meaning |
| --- | --- |
| `ctx.position_size` | Signed simulated quantity (positive long, negative short) |
| `ctx.cash` | Simulated cash |
| `ctx.equity` | Cash plus the position marked at the current close |
| `ctx.pending` | Orders waiting for the next bar |
| `ctx.fills` | Fills so far |

Orders that would exceed the simulated equity are rejected and counted in
`rejected_orders`.

## Stops, targets and risk

The stop and target you pass to `entry` belong to the position they open:
after the fill, `ctx.position_stop` and `ctx.position_target` return them
(None when not given or when flat).

- **In a backtest** the simulator does not exit by itself. The strategy
  decides, usually by checking the bar's range against the levels:

  ```python
  if ctx.position_size > 0:
      if ctx.position_stop is not None and ctx.bar["low"] <= ctx.position_stop:
          ctx.close("stop reached")
      elif ctx.position_target is not None and ctx.bar["high"] >= ctx.position_target:
          ctx.close("target reached")
      return
  ```

  The exit fills at the next bar's open, so a gap past the stop costs what it
  would cost in reality.
- **In PAPER, SHADOW and LIVE** the stop and target travel with the entry
  signal, and the execution engine places them as venue-held stop-loss and
  take-profit orders. Your `close()` produces an exit signal
  (`POSITION_EXITED`) for notifications. Protection does not depend on the
  market engine staying up.

Size positions from inputs. The built-ins use a percentage of
`ctx.equity` divided by the close. The execution engine's risk gateway still
applies the account's own limits to every order.

## Backtesting

In the terminal, open **Strategy**, pick a built-in, plugin or own strategy,
adjust the inputs generated from `INPUTS`, set initial equity, commission and
slippage, and press **Backtest**. The panel shows the metrics, the equity
curve and the trade list; **Show on chart** draws fill arrows and exit labels.

The same run is available as an API call:

```http
POST /v1/scripts/{script_id}/run
{"instrument_id": "binance:perp:BTCUSDT", "tf": "1h", "limit": 2000,
 "inputs": {"ema_length": 50}, "initial_equity": 10000,
 "commission_bps": 4, "slippage_bps": 2}
```

The result contains:

- `fills`: time, side, quantity, price, fee and whether it was an entry, exit
  or reversal;
- `orders`: every queued order with its outcome;
- `trades`: round trips rebuilt from the fills;
- `equity_curve`;
- `metrics`: `initial_equity`, `final_equity`, `net_profit`, `return_pct`,
  `max_drawdown_pct`, `fill_count`, `fees`, `trade_count`, `win_rate_pct`,
  `unfilled_intents` and `rejected_orders`.

## From backtest to signals and orders

A strategy never sends orders itself. **Activate** it, and the market engine
evaluates it on every newly closed bar and turns the orders it queues on that
bar into **signals**:

| Order on the newest closed bar | Signal |
| --- | --- |
| `entry("long" \| "short", ...)` | `ENTRY_CONFIRMED` with direction, stop and target |
| `close()` | `POSITION_EXITED` |
| reversal | both |

```http
POST /v1/strategy-activations
{"script_id": "builtin:ema-trend-long", "instrument_ids": ["binance:perp:BTCUSDT"],
 "tf": "1h", "inputs": {}, "mode": "PAPER", "channels": ["UI"],
 "history_bars": 500}
```

| Field | Meaning |
| --- | --- |
| `script_id`, `version` | A built-in, plugin or own strategy. Own scripts are pinned to `version` (the latest when omitted); catalog scripts are pinned to their installed source hash |
| `instrument_ids` | One to twenty instruments, evaluated independently |
| `tf`, `history_bars` | Signal timeframe; closed bars per evaluation (50–2,000), enough for the warm-up |
| `mode` | `ALERT_ONLY` (notify only), `PAPER` or `SHADOW` (forwarded to the execution engine). `AUTO_LIVE` is refused here (`TRADE_MODE_DISABLED`); live trading goes through an execution-engine deployment with consent |
| `channels` | Notification channels |

Pause, resume and delete with `POST /v1/strategy-activations/{id}/pause`,
`.../resume` and `DELETE /v1/strategy-activations/{id}`. The Strategy panel
does the same and lists recent signals.

Behaviour you can rely on:

- **No replay.** Each instrument keeps a checkpoint. Activating, resuming or
  restarting the engine never emits signals for bars that closed earlier.
  Bars older than twice the timeframe (at least 120 s) are skipped.
- **No duplicates.** A signal is keyed by tenant, activation, source hash,
  instrument, bar, kind and direction.
- **Pinned code.** The execution engine sees `strategy_version_id` =
  `<script>:v<version>` for own scripts and
  `builtin:<id>:v<first 12 hash characters>` for catalog scripts. Create
  deployments for that exact id. If an installed built-in or plugin changes,
  its activation stops with a visible error and must be re-created.
- **Own scripts need the runner.** Built-in and plugin strategies run without
  Docker; your own strategies need the isolated runtime enabled.

The execution engine turns a forwarded `ENTRY_CONFIRMED` signal into an order
only for an enabled deployment of that exact strategy version, after the
risk gateway approves it. LIVE additionally needs the global live gate, an
armed account and your step-up consent
([LIVE enablement](../OPERATIONS.md#live-enablement)).

## Checklist

- [ ] One class, inherits `joseph_script.Strategy`, implements `on_bar(self, ctx)`.
- [ ] Decisions use only the current bar and earlier bars.
- [ ] Every parameter is an `Input`, including quantity and risk settings.
- [ ] Entries check `ctx.position_size` to avoid repeated entries.
- [ ] Stops and targets are passed to `entry`, and the backtest exits on them.
- [ ] Plots show what the rules use, so a backtest can be checked visually.
- [ ] Backtested with realistic commission and slippage before activation.
