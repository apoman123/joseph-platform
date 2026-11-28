# Developing alerts

An alert is a `joseph_script.Alert` subclass. It evaluates closed bars and
publishes named **conditions** with `ctx.alert` or `ctx.alertcondition`. An
enabled alert job delivers new conditions to your notification destinations.
Alerts never place orders. Read the [overview](README.md) first.

## Contents

- [Minimal alert](#minimal-alert)
- [Conditions](#conditions)
- [Crossing logic](#crossing-logic)
- [Messages and severity](#messages-and-severity)
- [Turning a script into a running alert](#turning-a-script-into-a-running-alert)
- [Delivery and deduplication](#delivery-and-deduplication)
- [Alerts from indicators and strategies](#alerts-from-indicators-and-strategies)
- [Checklist](#checklist)

## Minimal alert

```python
import joseph_script


class CloseAbove(joseph_script.Alert):
    """Notifies when a bar closes above a level."""

    ID = "close_above"
    TITLE = "Close above level"
    INPUTS = {"level": joseph_script.Input(100.0, title="Level")}

    def on_bar(self, ctx):
        level = float(ctx.input("level"))
        ctx.alertcondition(
            ctx.bar["close"] > level,
            "close_above",
            f"{ctx.instrument_id} closed at {ctx.bar['close']} above {level}",
        )
```

## Conditions

| Call | Meaning |
| --- | --- |
| `ctx.alert(message, name="alert", severity="INFO")` | Records the condition `name` on the current closed bar |
| `ctx.alertcondition(condition, title, message, severity="INFO")` | Same, only when `condition` is true |

- `name`/`title` is the stable condition id (1–64 characters). It is part of
  the deduplication key, so keep it constant and do not put prices in it.
- One event per condition name per bar; a second call on the same bar
  replaces the first.
- Calls on a provisional (unconfirmed) bar are ignored.

## Crossing logic

"Crossing" means the relation changed between the previous bar and this one.
Keep the previous relation on `self`:

```python
def __init__(self):
    self.was_above = None

def on_bar(self, ctx):
    above = ctx.bar["close"] > float(ctx.input("level"))
    if self.was_above is False and above:
        ctx.alert("Crossed up", name="cross_up")
    if self.was_above is True and not above:
        ctx.alert("Crossed down", name="cross_down")
    self.was_above = above
```

Comparing only the current bar (`close > level`) fires on every bar above the
level. Usually you want the crossing.

## Messages and severity

Messages are plain text up to 500 characters. Include the symbol, the value
that triggered and the threshold. Severity is `INFO`, `WARNING`, `ENTRY` or
`CRITICAL`. Destinations can filter by severity, and quiet hours respect it.

## Turning a script into a running alert

In the terminal, open the **Alert manager** panel and choose **Create
alert**:

1. **Condition**: a built-in alert (see the table below), or one of your own
   alert, indicator or strategy scripts.
2. **Symbol** and **timeframe**.
3. **Inputs**: generated from the script's `INPUTS`.
4. **History**: closed bars evaluated per check (2–2,000). Choose enough for
   the warm-up of averages and oscillators.
5. **Destinations**: your notification subscriptions (Telegram, WhatsApp, Web
   Push and others). The in-app alert log always receives the event.
6. **Enabled**.

The same through the API:

```http
POST /v1/script-alerts
{"script_id": "builtin:ma-cross", "version": 0,
 "instrument_id": "binance:perp:BTCUSDT", "tf": "15m",
 "inputs": {"fast_length": 9, "slow_length": 21, "ma_type": "EMA"},
 "history_bars": 300,
 "subscription_ids": ["<subscription id>"], "enabled": true}
```

`version` pins your own scripts to an immutable version. Use `0` for built-in
and plugin scripts, which are pinned to their installed source hash instead.
If that code changes, the job reports an error and stops until you save it
again. List, update and delete jobs with `GET /v1/script-alerts`,
`PUT /v1/script-alerts/{job_id}` and `DELETE /v1/script-alerts/{job_id}`.

Built-in alerts:

| Id | Conditions (event names) |
| --- | --- |
| `builtin:price-cross` | `crossing-up`, `crossing-down` a level |
| `builtin:channel` | entering, exiting, inside or outside a price channel |
| `builtin:ma-cross` | fast/slow moving-average cross (EMA or SMA) |
| `builtin:rsi-level` | `rsi-overbought`, `rsi-oversold` |
| `builtin:volume-spike` | `volume-spike` versus the average volume |
| `builtin:percent-change` | `move-up`, `move-down` over N bars |
| `builtin:breakout` | `new-high`, `new-low` over N bars |

Built-in and plugin alerts run inside the market engine. Your own scripts
need the isolated runtime enabled.

## Delivery and deduplication

- The scheduler checks enabled jobs about every 10 seconds and evaluates
  only bars that closed after the job's checkpoint. Enabling, editing or
  restarting never replays history.
- Each event becomes one signal of kind `CUSTOM_ALERT` in mode `ALERT_ONLY`.
  Its key combines tenant, job, version (or source hash), condition name and
  bar, so an event is delivered once even across restarts.
- Delivery goes through the durable outbox to the selected destinations,
  respecting each destination's severity filter and quiet hours.
- Alert signals are never forwarded to the execution engine. An alert cannot
  place an order.

## Alerts from indicators and strategies

Indicators and strategies can call `ctx.alert` and `ctx.alertcondition` too.
An alert job can run them, so a study's conditions can notify you without a
separate alert script.

## Checklist

- [ ] One class, inherits `joseph_script.Alert`, implements `on_bar(self, ctx)`.
- [ ] Stable condition names; data goes in the message, not the name.
- [ ] Crossings compare against the previous bar.
- [ ] Enough history requested for the warm-up of averages and oscillators.
