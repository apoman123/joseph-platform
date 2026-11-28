# Python scripts, chart studies, heatmaps, and MCP

The market engine starts an authenticated Streamable HTTP MCP endpoint at
`/mcp` during its normal application lifespan. The terminal includes **Python
editor** and **MCP** bottom-panel tabs. Use the command palette to find them.

## Enable Python execution

Source authoring, validation, immutable versions, and the MCP endpoint are
available without Docker. To execute Python, build the trusted runner and enable
it in the market engine environment:

```sh
make script-runner
export JOSEPH_SCRIPT_RUNTIME=docker
make dev-market
```

`JOSEPH_SCRIPT_RUNNER_IMAGE` defaults to `joseph-python-runner:1`.
`JOSEPH_SCRIPT_TIMEOUT_S` defaults to 10 seconds and is bounded to 1–30 seconds.
These settings can also be placed in the selected `JOSEPH_MARKET_ENV_FILE`.
No additional market-data API keys are needed for scripts. They use the existing
CCXT, Alpaca, Fugle, and Treasury/FRED configuration described in the README.

For a containerized market engine, build the runner on the same Docker daemon
and add `deploy/docker-compose.scripts.yml` to the normal Compose command. Set
`DOCKER_GID` to the socket's numeric group. `JOSEPH_DOCKER_SOCKET` can select a
dedicated daemon socket. The market service's Docker access is privileged; use a
dedicated/rootless daemon for untrusted authors. User containers receive no
socket, host directories, credentials, or external network access. They run as
UID 65534 with a read-only filesystem, a private 16 MiB temporary filesystem,
256 MiB memory, one CPU, 32 processes, and bounded output. Containers are removed
after success, failure, timeout, or cancellation. The image provides Python
3.12's standard library and `joseph_script`; third-party packages are not
installed by user source.

## Five independent script classes

Every source file declares exactly one concrete class that inherits its role's
abstract base from `joseph_script`. A function-only declaration is rejected.

| Kind | Required override | Behavior |
| --- | --- | --- |
| Indicator | `Indicator.on_bar(self, ctx)` | Plots and drawings on the price chart or a separate pane. |
| Strategy | `Strategy.on_bar(self, ctx)` | Visual studies plus simulated entries/exits and performance. |
| Library | `Library.exports(self)` | Returns a nonempty mapping of public names to reusable objects. |
| Filter | `Filter.matches(self, ctx)` | Returns a boolean for each instrument in a selected market. |
| Alert | `Alert.on_bar(self, ctx)` | Publishes custom conditions through `ctx.alert` or `ctx.alertcondition`. |

```python
from joseph_script import Indicator


class Average(Indicator):
    """Plots an average using a user-selected lookback."""

    def on_bar(self, ctx):
        """Publishes one average value on the visible source bar."""
        length = int(ctx.input("length", 20, minimum=1, maximum=500))
        ctx.plot("Average", ctx.sma(length=length), color="#5cb8e6")
```

The editor creates working templates for all five kinds. Validate checks Python
syntax, inheritance, overrides, and owned library pins **without executing
source on the API host**. Save creates immutable versions. An update requires
`expected_version`; stale edits return 409. Archiving hides the catalog head but
retains pinned versions and dependent libraries. A source's kind cannot change;
create another script for a different role.

A library's `exports()` can return functions, classes, and constants. Declare an
alias in the importing version's **Pinned libraries** field:

```json
{"math": {"script_id": "my-math", "version": 1}}
```

Then use `from libraries.math import mean` or `from libraries import math`.
Imports resolve only to the current tenant's exact immutable library versions.
Cycles, role mismatches, graphs deeper than 16, and excessive source graphs are
rejected. Each filter candidate gets fresh script and library instances.

## Indicator API and legend

This is a Python API, not a Pine Script interpreter or a drop-in implementation
of every Pine built-in function. Python and reusable libraries supply the
calculation language. The visual categories from TradingView's indicator model
are available through the following context operations:

- `bar`, `history`, `series`, `sma`, `instrument`, `instrument_id`, `timeframe`,
  `inputs`, `input`, `state`, `bar_index`, `is_first`, `is_last`, `is_confirmed`.
- `plot`: line, step, area, histogram, columns, circles, crosses; colors, widths,
  gaps (`None`), and bar offsets. Up to 64 named plots per script.
- `hline`, `fill`, `line`, `linefill`, `polyline`, `box`, `label`, `delete`.
  Reuse the returned object ID to update a drawing. Up to 500 persistent objects.
- `plotshape`, `plotchar`, `plotarrow`, `plotcandle`, `plotbar`, `barcolor`,
  `bgcolor`, and fixed-position `table`. Per-bar visuals have a separate bound.
- `request(alias, field="close", intrabars=False)` for declared additional
  symbols/timeframes. Only source bars closed by the visible bar are exposed.
- `intrabars(alias)`, `closed_bar(alias)`, `period(tf)`, `optional_series(field)`
  and `plot(..., display="data_window")`; see
  [context extensions](#context-extensions-for-indicators).
- `alert` and `alertcondition` for preview events or explicitly enabled jobs.

**Other timeframes / symbols** declares up to four aliases before execution:

```json
{"daily": {"tf": "1d", "limit": 500}, "other": {"instrument_id": "nasdaq:stock:MSFT", "tf": "1h", "limit": 500}}
```

Built-in, plugin and owned indicators are one study system: every chart keeps
its studies (`ChartState.studies`: script id, pinned version for owned scripts,
inputs, plot colors, visibility) in the workspace. The indicator picker lists
**Built-ins**, **Plugins** and **My scripts** (owned indicator, strategy, and
alert sources; libraries and filters remain in the editor), and studies can be
saved in **My collection**. A chart's visible studies run in batches
(`POST /v1/scripts/run-batch`) over the bars the chart has loaded (500 through
5,000), after the chart's own candles have arrived.
Indicators refresh provisional values every five seconds with fresh replayed
state; strategies and alerts use closed bars. A provisional bar cannot emit an
alert. Data availability and missing-history errors remain visible.

Each active indicator has a top-left legend:

| Control | Action |
| --- | --- |
| Name | Identifies the study; managed studies show the pinned version. |
| Gear | Opens per-chart settings generated from the declared `INPUTS` (groups, bounds, options, `Style` colors) plus plot colors. |
| `{}` | Opens owned source in the Python editor, or read-only public built-in source. Protected external source is unavailable. |
| Eye | Hides or shows the study while retaining its settings and legend row. |
| Cross | Removes the study and clears its chart settings. Re-adding starts from defaults. |

These behaviors follow TradingView's [indicator customization](https://www.tradingview.com/support/solutions/43000506677-how-do-i-remove-or-customize-an-indicator/),
[indicator settings](https://www.tradingview.com/support/solutions/43000480333-how-to-adjust-the-indicator-settings/),
and [source availability](https://uk.tradingview.com/pine-script-docs/primer/first-steps/).
The API's categories follow its [visuals overview](https://www.tradingview.com/pine-script-docs/visuals/overview/).

### Context extensions for indicators

The built-in indicators use these additions; tenant and plugin scripts can use
them too.

- **Intrabar requests.** `Request(tf, intrabars=True, limit=...)` (up to 5,000
  bars) reads the lower-timeframe bars inside the chart bars with
  `ctx.intrabars(alias)` (closed intrabars that start in the current bar and
  end by its close; a cursor makes a whole run read each intrabar once).
  `tf="auto"` picks 1s on seconds charts, 1m intraday, 5m on daily and 1h on
  weekly and monthly charts (none on 1s and 1m charts); `tf_input` names an
  input whose value, unless `"auto"`, replaces `tf`. The host fetches
  intrabars only for the newest chart bars that need them: from the run's
  `window_from_ms` (the chart's visible window) when given, skipping bars
  that already carry the optional field named by `unless` (for example
  `taker_buy_volume`) while the boolean input `unless_input` is true.
- **Latest closed bar.** `ctx.closed_bar(alias)` returns the newest bar of a
  declared series that has closed by the current bar's close, or `None`.
  For an ordinary request `tf="auto"` (through `tf_input`) is the next
  calendar period above the chart: the day below daily charts, the week
  below weekly and the month below monthly charts.
- **Calendar periods.** `PERIODS = ("1d", "1w", "1M")` on the class makes the
  host compute each bar's period on the instrument's session calendar;
  `ctx.period("1d")` returns `(start_ms, next_start_ms)` (UTC days, ISO weeks
  and months on 24x7 markets; sessions, their weeks and months on exchanges).
- **Data-window values.** `ctx.plot(name, value, display="data_window")` lists
  a value in the legend's data window without drawing it.
- **Runs** read up to 5,000 bars (`limit`), with `from_ms`/`to_ms` bounds and
  `include_open` for a provisional last bar (indicators only).
  `POST /v1/scripts/run-batch` (`ScriptBatchRun`: the run fields plus `runs`,
  one through sixteen `{script_id, version?, inputs}` entries) runs several
  studies over one fetch of the chart candles and answers `ScriptBatchResult`,
  one `{script_id, result, error}` entry per study; one study's failure does
  not fail the others. Built-in and plugin classes run in process with a
  result cache keyed by exact input.

`GET /v1/scripts/catalog?kind=indicator` lists the built-in and plugin
indicators with their declared inputs and requests;
`GET /v1/scripts/catalog/{id}` adds the public source of a built-in.

## Strategy simulations

Python `Strategy` scripts are the only strategy format
([ADR 0010](adr/0010-python-strategy-scripts.md)).

`ctx.entry("long" | "short", quantity=1, stop=None, target=None, comment="")`
and `ctx.close(comment="")` queue an order; a later call on the same bar
replaces it. A stop must lie below the current close for a long entry and above
it for a short entry, a target on the other side; a wrong side raises
`ValueError`. The order fills on the **following available source bar's opening
price**, with the selected adverse slippage and commission. While a position is
open, `ctx.position_stop` and `ctx.position_target` hold the levels of the
entry that opened it; the simulator never exits on them by itself, so a
strategy closes the position when a bar trades through them (the built-ins
close at the next open). Arrows point to the fill bar; exits and reversals have
their own labels. Positions exceeding the simulated equity limit are rejected.

A strategy run returns, besides plots and the equity curve:

- `fills`: verified next-bar-open fills;
- `orders`: every queued order with its bar, action, quantity, `reference`
  (the bar close), stop, target, comment and outcome (`pending` on the last
  bar, `filled`, `rejected` or `unchanged`) and fill bar;
- `trades`: round trips the host rebuilds from the fills (direction, entry and
  exit bars and prices, fees, profit and return; the last one stays open while
  the position is);
- `position_size` after the last bar, and metrics including `fees`,
  `max_drawdown_pct`, `trade_count`, `win_rate_pct`, `rejected_orders` and
  `unfilled_intents`.

`POST /v1/scripts/{id}/run` is the backtest: it accepts tenant slugs and the
catalog ids `builtin:<id>` and `plugin:<entry point>:<id>`
(`GET /v1/scripts/catalog?kind=strategy` lists them). The built-in strategies
are `builtin:ema-trend-long`, `builtin:rsi-reversion-short` and
`builtin:pivot-bounce-long`; their source is public and can be copied into an
owned script.

### Strategy activations

`POST /v1/strategy-activations` evaluates a strategy live on closed bars:
`script_id` (and `version` for tenant scripts, the latest when omitted),
one to twenty `instrument_ids`, `tf`, `inputs`, `mode` (`ALERT_ONLY`, `PAPER`
or `SHADOW`; `AUTO_LIVE` returns 409 `TRADE_MODE_DISABLED`), notification
`channels` (default `UI`) and `history_bars` (50–2000, the warm-up). Activations
persist across restarts; `POST .../{id}/pause`, `.../resume` and `DELETE` manage
them, and `GET` shows the last evaluation and errors per instrument.

Every `JOSEPH_STRATEGY_INTERVAL_S` seconds the engine re-runs each active
activation. An order queued on the newest closed bar becomes a signal: an entry
that opens or reverses the position is `ENTRY_CONFIRMED` with direction,
planned entry (the bar close), stop, target and reward/risk; a close, or the
closing half of a reversal, is `POSITION_EXITED`. Deduplication includes
tenant, activation, source hash, instrument, bar and kind. Each instrument's
checkpoint starts at activation or resume time, so history never fires, and a
bar that closed more than twice the timeframe (at least two minutes) ago is
skipped. Signals use the normal routing: the UI and selected subscriptions, and
for `PAPER` and `SHADOW` the execution engine, whose deployments bind the
activation's `strategy_version_id` (`<script_id>:v<version>`, or
`<catalog id>:v<12 hash digits>` for built-in and plugin classes). A built-in or
plugin whose installed code changed reports an error until a new activation
adopts it. Tenant strategies need the isolated runtime.

These conventions follow TradingView's [strategy execution model](https://www.tradingview.com/pine-script-docs/concepts/strategies/).
This simulator does not submit broker orders. Existing live trading remains in
the account, preview, deployment, and consent workflow, with all previously
supported exchanges and brokers retained.

## Market filters and Python alerts

Every default filter and alert condition is a built-in script: one class
inheriting `joseph_script.Filter` (`matches(self, ctx) -> bool`) or
`joseph_script.Alert` (`on_bar(self, ctx)` publishing through `ctx.alert` or
`ctx.alertcondition`), listed by `GET /v1/scripts/catalog?kind=filter|alert` as
`builtin:<id>`. Built-ins (and installed plugins) run in process, so scans and
alerts on them need no Docker runtime; owned scripts still run only in the
isolated runner. The public source of a built-in can be copied into the editor
and saved as an owned script.

Built-in filters (inputs in parentheses; *operator* is `above`, `below` or
`between` with `value` and `value2`):

| Id | Reads | Inputs |
| --- | --- | --- |
| `liquidity-qualification` | snapshot, order book, open interest, funding | max data age (s), reject high funding risk |
| `price` | latest trade, else mark | operator, value, value2 |
| `change` | rolling 24h change % | operator, value, value2 |
| `volume` | rolling 24h / session base volume | operator, value, value2 |
| `turnover` | rolling 24h / session quote turnover | operator, value, value2 |
| `relative-volume` | latest bar volume / mean of the preceding bars | length, operator, value, value2 |
| `gap` | latest bar open vs previous close, % | operator, value, value2 |
| `price-vs-ma` | latest price vs SMA/EMA of closes | average type, length, above/below |
| `rsi` | Wilder RSI of closes | length, operator, value, value2 |
| `new-high-low` | latest bar sets the N-bar high or low | bars, high/low |

**Liquidity qualification** applies strict gates per asset class (a value equal
to a threshold fails): crypto and RWA perpetuals need 24h turnover above USD 50M,
equity perpetuals above USD 30M, both open interest above USD 10M, turnover/OI
above 0.20, spread below 0.10 % and USD 50K worst-side book-walk slippage below
0.25 %; US stocks/ETFs need session turnover above USD 20M (bond ETFs USD 10M);
TWSE and TPEx listings above TWD 300M (bond ETFs TWD 100M) with spread below
0.60 % and TWD 1.5M slippage below 1.00 %. Listed securities have no funding or
open-interest gates. High-risk funding (|8h| ≥ 0.30 %) excludes a perpetual
unless the input is turned off. Rate series never match; on Binance only
USDT-quoted contracts are eligible. A failed gate excludes; otherwise a missing
or stale input (older than the max data age) makes the instrument unavailable.

A filter that lacks data raises `joseph_script.Unavailable`; the scan lists the
instrument under `unavailable` with the reason, never as a match. A filter can
declare `METRICS = ("order_book", "open_interest", "funding")` (or a scan can
request `metrics`) to receive `ctx.snapshot["order_book"]` (100 levels per
side) and the open interest and funding of derivatives when the bulk snapshot
lacks them. `ctx.snapshot["evaluated_at_ms"]` is the evaluation time.

A scan selects a configured venue and market type, an optional search, a
maximum symbol count (1–10,000), optional closed history (0–500 bars) on one
resolution, and up to eleven filters combined with AND: `POST
/v1/script-scans` with `conditions: [{script_id, version?, inputs}]`, or `POST
/v1/scripts/{id}/scan` for a path filter plus optional further conditions.
`order_by: "turnover"` loads every quote first and scans the most active
instruments first, so `max_symbols` keeps them. An instrument is excluded as
soon as one filter returns False; a filter failure is listed under `errors`.
Every filter is pinned at submission (`conditions` with version and source hash;
catalog filters have version 0). Submission returns 202 immediately; progress,
matches (with their snapshot), unavailable instruments, errors, cancellation and
the total eligible universe are available under `/v1/script-scans/{job_id}`.
Scans evaluate 25 instruments per batch with bounded data requests. Interrupted
scans retain partial results and show a restart error rather than silently
resuming different data.

Built-in alert conditions (event names in parentheses):

| Id | Fires on a closed bar when | Inputs |
| --- | --- | --- |
| `price-cross` | the close crosses a level (`crossing-up`, `crossing-down`) | level, crossing / up / down |
| `channel` | the close enters, exits, closes inside or outside a band (`entering-channel`, …) | upper, lower, condition |
| `ma-cross` | a fast average crosses a slow one (`ma-cross-up`, `ma-cross-down`) | fast, slow, EMA/SMA, crossing / up / down |
| `rsi-level` | RSI rises above overbought or falls below oversold (`rsi-overbought`, `rsi-oversold`) | length, overbought, oversold, which |
| `volume-spike` | volume exceeds a multiple of the preceding mean (`volume-spike`) | length, multiple |
| `percent-change` | the close moves by at least a % over N bars (`move-up`, `move-down`) | bars, threshold %, direction |
| `breakout` | the high/low breaks the preceding N bars (`new-high`, `new-low`) | bars, high / low / either |

`ctx.alert(message, name="condition", severity="INFO")` records a condition.
Preview runs **do not send notifications**. An alert job pins its condition
script (owned version, or version 0 plus the installed source hash of a
built-in or plugin), symbol, timeframe, inputs, history and selected owned
notification destinations: create it in the terminal's **Alert manager** or
with `POST /v1/script-alerts`; owned scripts also offer **Enable new live
alert** in the editor. Inputs of catalog scripts are checked against their
declarations. Jobs poll every ten seconds and deliver only future confirmed
bars through the durable outbox. The checkpoint is based on the exact evaluated
window, not a later quote request. Deduplication includes tenant, job,
version (or source hash), condition name, and bar. If an installed built-in's
source changes, its jobs report the change and stop until saved again. UI/browser
delivery is implicit; Telegram, WhatsApp, Web Push, and other selected
destinations retain their existing enabled, strategy, signal-kind, severity, and
quiet-hours filters. Select **Alerts (built-in and Python)** in destination
filters. Their canonical kind is `CUSTOM_ALERT`, direction is `NONE`, mode is
always `ALERT_ONLY` and `condition` names the matched condition; they never
enter execution forwarding. These three kinds are the whole signal contract. The **Alert log**
filter of the Alerts panel lists them.

Run one market-engine worker per database for scan ownership and scheduling.
Jobs survive restart. Disabling/deleting stops future evaluation. Replacing or
re-enabling a configuration resets its future-bar checkpoint; it does not replay
historical notifications. Choose enough history for the condition and expected
outages. Evaluation diagnostics are visible in the Alert manager and editor.

In DEMO mode the screener and alert manager evaluate TypeScript mirrors of the
built-in filters and alerts on the synthetic DEMO instruments; results are DEMO
data and owned scripts need a connected market engine.

## Heatmaps

The plus button opens **New tab**, with **Candlestick chart** and **Heatmap**
choices. Stock, ETF and crypto heatmaps, their data sources, caching and the
options that free sources cannot supply are described in
[Heatmaps](HEATMAPS.md). MCP clients read them with `market_get_heatmap` and
`market_get_heatmap_catalog`.

## MCP connection and capability coverage

Open **MCP**, create a named connection, and configure the client with the shown
endpoint and its one-time bearer token. Read permission is the default. Write
and trading permissions are separate explicit choices; revoke connections in the
same panel. Ordinary API/service tokens are refused by `/mcp`. MCP credentials
are audience-bound, expire, and resolve to the original configured user on the
server; they are never passed upstream as API credentials.

Set `JOSEPH_MCP_PUBLIC_URL` to the canonical externally reachable URL, including
`/mcp`. The default is `http://localhost:8000/mcp`. Restrict CORS/Origins to the
terminal's real origin. Use HTTPS outside local development. Signing material
persists privately in the market database. Optionally set a secret of at least 32 bytes in
`JOSEPH_MCP_SIGNING_KEY` or its `_FILE` variant and retain it across replicas.
Manual bearer grants are supported; this project does not implement an OAuth
login/authorization server. Clients requiring an OAuth browser flow must use
manual bearer configuration or the stdio option below.

The official Python SDK is locked to **2.3.0**, targeting MCP **2026-07-28** with
SDK-supported legacy negotiation. Tools have stable names, strict argument
schemas, output schemas, permission checks, and side-effect annotations. The
catalog derives every user `/v1/` HTTP operation from both engines' OpenAPI:
market/history/feed health, the script catalog and study runs, Python sources/versions,
filters/alerts, strategy activations/signals, notification subscriptions,
accounts/orders/positions/fills/risk/deployments/consents. Internal service-token
routes and MCP credential bootstrap are deliberately excluded.

Use an official SDK client for the current HTTP transport: it supplies the
2026 revision's per-request protocol/capability envelope and matching
`Mcp-Method` and `Mcp-Name` routing headers. A hand-written JSON-RPC POST that
only sets `Mcp-Protocol-Version` is incomplete for this revision. Legacy
2025-11-25 clients remain covered by compatibility tests.

Resources provide `joseph://capabilities` and `joseph://scripts/sdk`; prompts
provide `analyze_market` and `review_script`. Returned source/market text is data,
not trusted agent instructions. Trading tools retain existing preview,
confirmation, account, risk, and live-consent enforcement. Mutations are never
retried automatically after a timeout or disconnect.

To control charts, enable **MCP browser control for this window**. The agent can
list owned sessions and inspect their registered commands, workspace, drawing
tools/documents, themes, replay state, templates, and feed provenance. Explicit
operations cover commands, validated workspace replacement/import, drawing
create/update/delete/select/undo/redo, replay, theme, template save/apply/delete,
workspace export, and PNG screenshots. Chart tabs, layout, instruments,
timeframes, indicators/settings and feature panels are represented by workspace
data and registered commands. Arbitrary JavaScript is refused. Clipboard,
pop-outs, native permission dialogs, and similar browser gestures require direct
user interaction. Turning control off disconnects that window; backend tools
remain governed by their grants. Browser sessions are scoped to both user and
tenant and are not restored automatically after server restart.

For clients using stdio, both engines must be running:

```sh
cd joseph-market-engine
export JOSEPH_MCP_USER_TOKEN='<existing ordinary user API token>'
uv run python -m joseph.mcp
```

The stdio adapter uses server-selected localhost backend URLs by default;
`--market-url` and `--execution-url` select fixed origins at process launch.
It defaults to read-only. Add `--allow-write`, then `--allow-trading` only when
those capabilities are intended. Stdout is reserved for protocol messages;
diagnostics go to stderr. See the [stable specification](https://modelcontextprotocol.io/specification/2026-07-28)
and [official SDK](https://py.sdk.modelcontextprotocol.io/).

## Validation

CI builds the actual isolated runner and tests all five roles, immutable imports,
next-bar fills, filter progress, alert checkpoints/deduplication, tenant
isolation, network/filesystem/credential isolation, and process timeouts. The
terminal tests cover legends, heatmap layout, and browser-control validation.
Full-stack Playwright tests run the terminal against both engines; Docker-backed
script tests are enabled with `JOSEPH_SCRIPT_TESTS=1`.
