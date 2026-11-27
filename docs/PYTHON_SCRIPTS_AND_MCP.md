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
- `alert` and `alertcondition` for preview events or explicitly enabled jobs.

**Other timeframes / symbols** declares up to four aliases before execution:

```json
{"daily": {"tf": "1d", "limit": 500}, "other": {"instrument_id": "nasdaq:stock:MSFT", "tf": "1h", "limit": 500}}
```

Inputs/styles and visibility are saved per chart with the immutable version pin.
**My scripts** in the indicator picker lists owned indicator, strategy, and alert
sources; libraries and filters remain in the editor. Managed studies can also be
saved in **My collection**. Attached studies evaluate up to 2,000 source bars.
Indicators refresh provisional values every five seconds with fresh replayed
state; strategies and alerts use closed bars. A provisional bar cannot emit an
alert. Data availability and missing-history errors remain visible.

Each active indicator has a top-left legend:

| Control | Action |
| --- | --- |
| Name | Identifies the study; managed studies show the pinned version. |
| Gear | Opens per-chart inputs and presentation settings. |
| `{}` | Opens owned source in the Python editor, or read-only public built-in source. Protected external source is unavailable. |
| Eye | Hides or shows the study while retaining its settings and legend row. |
| Cross | Removes the study and clears its chart settings. Re-adding starts from defaults. |

These behaviors follow TradingView's [indicator customization](https://www.tradingview.com/support/solutions/43000506677-how-do-i-remove-or-customize-an-indicator/),
[indicator settings](https://www.tradingview.com/support/solutions/43000480333-how-to-adjust-the-indicator-settings/),
and [source availability](https://uk.tradingview.com/pine-script-docs/primer/first-steps/).
The API's categories follow its [visuals overview](https://www.tradingview.com/pine-script-docs/visuals/overview/).

## Strategy simulations

`ctx.entry("long" | "short", quantity=1)` and `ctx.close()` queue an intent.
It fills on the **following available source bar's opening price**, with the
selected adverse slippage and commission. Arrows point to that fill bar; exits
and reversals have their own labels. The editor reports fills, fees, marked
equity, return, drawdown, rejected orders, and unfilled final-bar intents.
Positions exceeding the simulated equity limit are rejected.

These conventions follow TradingView's [strategy execution model](https://www.tradingview.com/pine-script-docs/concepts/strategies/).
This simulator does not submit broker orders. Existing live trading remains in
the account, preview, deployment, and consent workflow, with all previously
supported exchanges and brokers retained.

## Market filters and Python alerts

A filter selects a configured venue and market type, input overrides, a maximum
symbol count (1–10,000), and optional closed history (0–500 bars). Snapshot-only
filters avoid historical requests. Submission returns 202 immediately; progress,
matches, errors, cancellation, source hash, and the total eligible universe are
available under `/v1/script-scans/{job_id}`. Missing source data is reported
separately from false decisions. Interrupted scans retain partial results and
show a restart error rather than silently resuming different data.

`ctx.alert(message, name="condition", severity="INFO")` records a condition.
Preview runs **do not send notifications**. **Enable new live alert** pins a
saved source/version, symbol, timeframe, inputs, and selected owned notification
destinations. Jobs poll every ten seconds and deliver only future confirmed bars
through the durable outbox. The checkpoint is based on the exact evaluated
window, not a later quote request. Deduplication includes tenant, job, version,
condition name, and bar. UI/browser delivery is implicit; Telegram, WhatsApp,
Web Push, and other selected destinations retain their existing enabled,
strategy, signal-kind, severity, and quiet-hours filters. Select **Python alerts**
in destination filters. Their canonical kind is `CUSTOM_ALERT`, direction is
`NONE`, and mode is always `ALERT_ONLY`; they never enter execution forwarding.

Run one market-engine worker per database for scan ownership and scheduling.
Jobs survive restart. Disabling/deleting stops future evaluation. Replacing or
re-enabling a configuration resets its future-bar checkpoint; it does not replay
historical notifications. Choose enough history for the condition and expected
outages. Evaluation diagnostics are visible in the editor.

## Stock heatmaps

The plus button opens **New tab**, with **Candlestick chart** and **Stock heatmap**
choices. Saved tab type survives reload and export/import. There is no fixed
saved-tab limit; layouts still mount up to eight panes.

The heatmap shows the configured US or Taiwan stock/ETF universe, daily return
colors, weighted rectangles, exchange/sector grouping, search, group drill-down,
quote details, and click-to-open candle charts. It refreshes every 30 seconds.
The market request uses selected-symbol bulk quotes when supported and a bounded
shared cache otherwise. Partial quotes, unavailable sources, and timestamps are
shown; missing daily returns are gray. Regions keep USD and TWD sizing separate.
Alpaca IEX data retains its IEX coverage; it is not relabeled consolidated SIP.

Size supports equal, actual source volume/turnover, and market capitalization
when supplied. Current feeds do not provide a complete company-cap/sector table.
Those choices remain unavailable until dated, tenant-owned metadata is imported.
No index membership, sectors, or capitalization are fabricated. Import JSON:

```json
{"records":[{"instrument_id":"nasdaq:stock:AAPL","sector":"Technology","market_cap":"3000000000000","as_of_ms":1795737600000}]}
```

That example is an input-format illustration, not a current valuation. Supply
your own observation and date. Metadata remains separate from quote provenance.
Missing size values use a minimal fallback weight. The design follows
[TradingView heatmaps](https://www.tradingview.com/support/solutions/43000766446-tradingview-heatmaps-from-global-trends-to-details/)
and its [view modes](https://www.tradingview.com/support/solutions/43000794279-heatmap-view-mode/).

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
market/history/feed health, indicators/AOIs, screening, Python sources/versions,
filters/alerts, strategies/backtests/signals, notification subscriptions,
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
