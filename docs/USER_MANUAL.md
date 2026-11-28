# Joseph Terminal user manual

How to use Joseph Terminal, the platform's browser app, to chart, draw, screen, run strategies, backtest and trade. Operators start with the [operations runbook](OPERATIONS.md).

## Orientation

The terminal charts crypto perpetuals (Binance, Bitget, Hyperliquid, Lighter, MEXC), Taiwan stocks and ETFs (TWSE, TPEx), US stocks and ETFs (Nasdaq, NYSE, Cboe BZX) and US Treasury yields; futures and options are not offered. Zones, screening, signals and backtests come from the market engine; orders pass the execution engine's risk gateway.

| Mode | When | What you see |
|---|---|---|
| **DEMO** | Built without a market engine URL | Synthetic `mock` instruments and a simulated paper broker; a hatched *Demo data* ribbon and *Demo mode* in the status bar. Nothing reaches a venue. |
| **Connected** | Built with the engine URLs | Real data. A failing feed shows *Stale* or *Unavailable*, never invented prices. Without an execution engine, trading says *Trading is not connected*. |

DEMO instruments: `mock:perp:BTCUSD`, `ETHUSD`, `SOLUSD`; `mock:stock:ACME` (NYSE); `mock:etf:TLTD` (US bond ETF); `mock:stock:9901` (TWSE); `mock:etf:00999B` (TPEx bond ETF); `mock:rate:UST10Y`.

### Screen layout

| Area | Contents |
|---|---|
| Top bar | Instrument, timeframes, style, scale, *Indicators*, *Layout*, compare, templates, replay, screenshot, data badge, order mode, theme, ⌘K |
| Left toolbar | Drawing tools, undo/redo, magnet, lock all, hide all |
| Centre | 1, 2, 4 or 8 charts |
| Side panel | Market, Watchlist, Screener, Strategy, Objects, Order ticket |
| Bottom panel | Positions, Orders, Executions, Order history, Alerts, Notifications, Audit, Risk controls, Accounts, Data health |
| Status bar | Venue feed health, UTC clock, chart attribution, licences |

Pane headers show instrument, timeframe, bar-close countdown, market status, link group (*Link A–D*: linked charts follow each other), feed menu (*Auto feed*, *WebSocket*, *REST polling*), OHLC and data badge. Panels collapse; drag edges and chart gaps to resize.

### Chart tabs

The tab strip above the charts keeps one saved chart per market tab. Click
**+** to add a tab, then choose its market with the top-bar instrument picker.
Each tab retains its own symbol, timeframe, chart style, indicators and viewport.
Use the close button to remove a tab; the final tab stays open. Arrow keys,
Home and End select tabs, and Delete closes the focused tab.

There is no fixed saved-tab count. Tabs scroll horizontally and restore after
reload, subject to browser storage and memory capacity. Single-chart mode mounts
only the selected tab. Split layouts show a page of up to eight saved charts
containing the active tab, so inactive pages do not keep chart subscriptions open.

### Heatmaps

Choose **+** → **Heatmap** for a market map instead of a chart. Pick the data
set in the toolbar (stocks of an index or market, ETFs, or crypto coins), then
*Size by*, *Color by* and *Group by*. Click a sector header to zoom in and the
breadcrumb to go back; click a legend segment to hide those tiles; click a
tile to open its chart, or right-click it to add it to the watchlist. The
cogwheel sets the tile title, logo badge, shown value and colour scheme; the
camera saves or copies a PNG or a link to the view. Options the data sources
cannot supply are disabled with the reason. Details:
[Heatmaps](HEATMAPS.md).

### Indicator library

Click **Indicators** to open the searchable picker:

- **Built-ins** lists the engine's built-in indicators.
- **Plugins** lists indicators from plugin packages the operator installed.
- **My scripts** lists your own Python indicator, strategy and alert scripts.
- **My collection** lists indicators saved with the star button. The collection
  is saved with the workspace and shared by its chart tabs.

Search matches names, categories and descriptions; with several categories a
*Category* list narrows Built-ins or Plugins. **Add** puts a study on the
current chart (add one more than once to compare settings). Escape or the
close button dismisses the picker and returns focus to the toolbar. Chart
options list the chart's studies with show/hide and settings, plus Volume,
Session breaks and Data window.

### Data badges

| Badge (ribbon) | Meaning |
|---|---|
| *Live* (solid green) | Native venue stream |
| *Polled* (dashed amber) | REST fallback: fine for charts, not for order flow or automation |
| *Replay* (dotted violet) | Historical replay; no orders from this chart |
| *Stale for 42s* (red) | No fresh data; new exposure is blocked |
| *Demo data* (hatched) | Synthetic, not a real market |
| *Unavailable* (red dashes) | Venue unreachable or not enabled; the chart stays empty |

Magenta marks are yours (drawings, draft orders); green and red show direction.

## Charts

- **Instrument:** click the symbol. Tabs: *All*, *Crypto*, *Taiwan stocks*, *US stocks*, *Bonds & rates*. Search by symbol (`BTC`, `2330`) or name (`台積電`). Each result is one venue's contract: `binance:perp:BTCUSDT` and `hyperliquid:perp:BTC` differ. *Delayed 15 min*: the deployment gets delayed data. *No API key*: the data source (Alpaca for US stocks) is not configured, so charts stay empty.
- **Style and scale:** candles, bars, line, area, Heikin Ashi (display only, never used for signals); linear, logarithmic, percent. Hover over the right price scale and scroll to zoom prices vertically.
- **Compare** up to 3 instruments on a left percentage axis.
- **Bar replay:** click *Bar replay*, then a bar (or *Start 100 bars back*); step or play at 1–10 bars/s. Later bars are hidden, studies show values up to the cursor bar and trading from that chart is off. *Back to live* ends it.
- **Screenshot:** PNG with instrument, venue, timeframe, data badge and UTC time.

### Timeframes

| Group | Timeframes |
|---|---|
| Seconds | `1s` `5s` `15s` `30s` |
| Minutes | `1m` `3m` `5m` `10m` `15m` `30m` `45m` |
| Hours | `1h` `2h` `4h` `6h` `8h` `12h` |
| Days | `1d` `1w` `1M` (calendar month from the 1st, 00:00 UTC) |

★ pins a favourite. *Type a resolution*: a bare number is minutes (`15`), `S` seconds (`5S`), uppercase `M` months; unserved resolutions are refused, never approximated. Timeframes a venue lacks are dimmed with the reason; seconds marked *trades* are built from public trades, so their history is short (see [Delta](#delta)).

### Stock, ETF and yield sessions

| Market | Regular session |
|---|---|
| TWSE, TPEx | 09:00–13:30 Taipei; the closing auction is in the last bar |
| NYSE, Nasdaq, Cboe BZX | 09:30–16:00 New York (13:00 on early closes) |
| US Treasury yields | 08:00–17:00 New York, bond-market days |

- **Bars** start at the open and the last ends at the close; nights, weekends and holidays take no space. Times are exchange time (`ET`, `TPE`); crypto stays UTC.
- **Status badge:** *Open · closes 16:00 ET*, *Closed · opens Mon 09:30 ET*, *Holiday*, early-close notices.
- **Prices** follow the tick ladder (Taiwan stocks e.g. `48.55` below 50, `585` from 500; Taiwan ETFs 0.01 below 50, else 0.05; US 0.01). **Volume:** Taiwan in **張** (1,000 shares) in the data window and Market panel, shares on the volume pane.
- **US closes:** the free Alpaca IEX feed excludes the consolidated closing print.
- **Taiwan data:** official data gives coarse 1m bars, no seconds, and intraday history only since the engine started recording. With a Fugle free-tier key, per-symbol 1m history begins 2023-05-23 and live charts poll Fugle REST; official lists, bulk screening snapshots and MIS books stay separate. Official daily history is available for older dates.
- **Yields** are percent line charts without volume, Delta pane or ticket; trade a bond ETF.

## Drawing tools

| Group | Tools |
|---|---|
| Lines | Trendline, ray, extended line, horizontal line and ray, vertical line, parallel channel, Andrews pitchfork |
| Shapes | Rectangle, zone box, polyline, ellipse, circle, triangle, rotated rectangle, arc, curve |
| Fibonacci, Gann | Fib retracement, extension, time zones, circles; Gann fan, Gann box |
| Patterns | XABCD, ABCD, head and shoulders, Elliott impulse and correction, triangle pattern |
| Positions, Measure | Long and short position (never places orders); measure |
| Engine studies | Anchored VWAP, regression channel |
| Annotations | Text note, arrow, brush |

- Click each point in order (drag for the brush); a polyline ends with a double-click or Esc. The **Objects** tab lists drawings and pattern ratios.
- The **style bar** sets colour, width, line style, fill, extend, text, inputs, z-order, lock, hide and delete. *Magnet* snaps to OHLC (hold Ctrl to invert).
- Drawings are saved per instrument by time and price; drawings of a removed tool are kept as unsupported.
- **Engine studies** are computed by the engine, never the browser. *Anchored VWAP*: click one bar; *Band 1–3* add 0.5σ–3σ bands. *Regression channel*: click the first and last bar; *Deviations* 1σ–3σ, *Pearson's R*. They run the built-in *VWAP* and *Linear Regression Channel* scripts from the anchor bar, update on anchor moves and bar closes, follow replay and accept anchors up to 5,000 bars back; a farther anchor shows a notice instead of values drawn from the wrong bar.

## Indicators

Indicators are Python scripts run by the market engine, never by the browser: the built-ins, plugin indicators the operator installed and your own scripts all work the same way. A new chart starts with **EMA**, **Volume Delta** and **Pivot points**.

| Built-in | Draws |
|---|---|
| EMA, SMA | Moving average line (default length 20) |
| VWAP | VWAP restarting each session, week or month (or from a time) with optional ±σ bands; also the *Anchored VWAP* drawing tool |
| Linear Regression Channel | Least-squares channel over the last bars or from an anchor; also the *Regression channel* drawing tool |
| Bollinger Bands | Basis (SMA 20) and bands 2 standard deviations away, shaded between |
| RSI, ATR | Own pane; RSI with its upper, middle and lower bands |
| Volume Delta | The [*Delta %* pane](#delta) |
| Pivot points | Floor pivots P, R1–R3 and S1–S3 of the previous day, week or month (*auto* picks the next period above the chart), drawn as labelled dashed lines for each period; the first touch of a level raises an alert on that bar |

The **legend** at the chart's top left lists each study with its inputs (e.g. *EMA 20 close 0*):

- ⚙ opens its settings, generated from the inputs the script declares: inputs by group, then *Style* (colours and each plot's colour). *Reset to defaults* restores them. Settings apply to this chart only.
- `{}` shows the public Python source of a built-in (plugin source stays protected) or opens your own script in the editor.
- 👁 hides the study and keeps its settings; ✕ removes it.
- An error (for example missing candles) is shown under the study's row.

The *Data window* lists, besides OHLC, change, volume and compared closes, every visible study's values on the hovered bar, including values a script lists only there (such as the volume delta's buy and sell volume and coverage). A study from a plugin package follows that package's licence; ask your operator.

### Delta

The *Delta %* pane shows each bar's buy minus sell volume as a share of its volume. Bars are **true** (solid: venue taker volume, or on stocks trades classified against the quote with Lee-Ready) or an **estimate** from lower-timeframe bars by candle direction (translucent); a table in the pane names the method and coverage of the last bar, and an estimate below the minimum intrabar count or volume coverage is left empty rather than guessed. Intrabars are fetched only for the bars on screen that need them. ⚙ *Volume Delta* → *Use venue buy volume* off forces the estimate; *Intrabar timeframe* picks its resolution. Seconds bars and true delta need trade history:

| Venue | Trade history |
|---|---|
| Binance | Daily archive (next day, about 07:30 UTC), REST 48 h, recorded trades |
| Bitget | Archive (UTC+8 days, about a day late), REST 90 days, recorded trades |
| Lighter | Archive from 2025-01-17 (about 7 h after each UTC day), REST about 53 min, recorded trades |
| MEXC, Hyperliquid | Recorded trades only |
| TWSE, TPEx | None on official data; newest 5 sessions with SinoPac data |
| US stocks | Last 30 minutes |

Unrecorded time shows as a gap, never as invented bars.

## Watchlist and screener

- **Watchlist:** *My list* or *Qualified perpetuals* (read-only, from the engine). Columns *Last*, *Mark*, *24h*, *Turnover*, *Spread*, *Fund 8h*, *OI*, *Data*; pick them with the column button, click a header to sort.
- **Screener:** *Qualified* only if every gate passes strictly (equal fails); *Missing data* never passes. Perpetuals: 24h turnover, open interest, turnover/OI, spread, slippage for a USD 50,000 order, and no high-risk funding (8h funding from |0.30%|; *Watch* from |0.10%|). Stocks and ETFs: session turnover, spread and slippage. Filter, sort and save a *Preset*.

## Strategies and alerts

Strategies are Python `Strategy` scripts.

1. *Strategy* → pick a script: the built-ins *EMA Trend Long*, *RSI Reversion Short* and *Pivot Bounce Long*, installed plugin strategies, or your own Strategy scripts.
2. Edit the inputs the script declares (lengths, thresholds, stop and target settings, cooldown).
3. *Backtest* (below) shows how the inputs behave on the active instrument.
4. Activate on the active instrument and timeframe as **Alerts only**, **Paper trading** or **Shadow** (record only). *Automatic live* is locked: it needs an execution-engine deployment with limits, scoped consent and LIVE enabled on the server.
5. *Activations* lists each activation with its mode, last evaluation and errors; *Pause* and *Resume* it. *Recent signals* shows its entries and exits.

An activation evaluates bars that close after it was created or resumed, never history, also not after a server restart. An order the strategy queues on a newly closed bar becomes an entry or exit signal carrying the strategy's stop and target; Paper and Shadow entries go to the execution engine, where a deployment of the activation's strategy version sizes and places them. A built-in's *Open source* shows its code; *Copy to my scripts* saves it as your own editable script. Editing a script creates a new version, which existing activations keep ignoring until you activate it again. Your own scripts run only when the server enables the isolated script runtime.

- **Alerts:** kind, instrument, R:R, data source and an expandable **reason tree**; *Show on chart* jumps to the bar and zone. Sort by time, R multiple, confluence, bar volume or distance to AOI. *Acknowledge*, *Snooze* and *Dismiss* are local.
- **Notifications:** Telegram, LINE, e-mail or an HTTPS webhook, filtered by kind and severity, with quiet hours (critical alerts still arrive). *Not delivered*: the server has no credentials for that channel.

## Backtests

*Strategy* → *Backtest* runs the selected script with the edited inputs on the active instrument and timeframe over the latest closed bars, with initial equity, commission and slippage. Orders fill at the next bar's open with adverse slippage and per-fill fees; the built-ins exit at the next open after a bar trades through their stop or target. It reports return, drawdown, fees, the number of trades and win rate, the equity curve and every trade, and marks entries and exits on the chart. Live activations use the same simulator. DEMO results are labelled sample data.

## Trading

### Accounts

The *Accounts* tab shows each account's connection, modes, equity and settled/unsettled cash; credentials stay on the server. Orders route to an account serving the instrument: the crypto venue's, Interactive Brokers (US), SinoPac (Taiwan), or the USD and TWD paper accounts. Cathay shows *Awaiting API SDK* and takes no orders.

### Order ticket

1. Choose the account and mode: **Paper**, **Shadow** (records only) or **Live**.
2. Set side, type (*Market*, *Limit*, *Stop*), quantity (or *Size from risk*), leverage, reduce-only, SL and TP.
3. **Preview** shows rounded values, notional, fee, margin or cash, slippage, whether the stop is venue-native or software-managed, and every risk check.
4. Confirm with the button naming the exact action. Any edit invalidates the preview; a double-click sends once.

Right-click the chart to order at that price; drag draft lines to change price, SL or TP, or an open order's line to propose an amendment. *Unknown, reconciling*: the outcome is being checked; nothing is resent.

**Stocks and ETFs:** whole shares, no leverage or attached SL/TP; cash accounts cannot sell more than they hold. IBKR offers market, limit and stop with DAY, GTC or IOC; SinoPac market and limit with DAY, IOC or FOK. Taiwan *Odd lot (零股)* (1–999 shares) is limit DAY only; prices must be within ±10% of the previous close (except most bond ETFs and new listings). In a closed market, market orders are refused unless the account queues them (IBKR does). *Allow outside regular trading hours (IBKR)* lets limit orders fill pre- and post-market.

### Live trading

*Live* is offered only when the execution engine enables it for the account. To arm the account:

1. Choose *Live* in the ticket.
2. Confirm it is real money, pick 15 minutes, 1, 4 or 8 hours, and enter your authenticator code.
3. The engine checks the code and caps arming at the operator's limit (8 h by default, never over 24 h).

A *Live armed* badge shows the end time and *Disarm*. Each live order needs a final real-money confirmation. Expiry or a `LIVE_NOT_ARMED` refusal returns the ticket to Paper. Amending needs arming; closing, cancelling and the kill switch do not. Every grant, refusal and disarm is audited.

Nothing in the browser enables live trading. The engine checks every order against all gates: LIVE enabled on the deployment, a verified account, arming (strategies: a consented automatic deployment), a valid preview, the risk gateway and no kill switch. Operators: [LIVE enablement](OPERATIONS.md#live-enablement), [Brokers](BROKERS.md).

**Rehearsal:** in DEMO, `?demo-live-rehearsal=1` adds an *IBKR live rehearsal (demo)* account that accepts code `123456`; nothing reaches a broker.

### Positions, kill switch, audit

*Close* is reduce-only; *Reverse* is two confirmed steps. In *Risk controls*, a kill switch (one account or all) blocks new opening orders; cancelling and closing are separate options. *Audit* lists every action with a hash-chain check.

## Workspaces and pop-outs

- **Layout:** one chart, two side by side or stacked, four or eight; *Sync* crosshair, instrument, timeframe and viewport.
- The workspace autosaves in the browser. ⌘K → *Export workspace as JSON* saves layout, chart settings and every instrument's drawings; *Import a workspace from a JSON file* (≤2 MB) replaces the layout and the drawings of instruments in the file. *Copy workspace share link* carries no drawings or account data.
- **Chart templates** save style, scale, volume, studies and session breaks, never the instrument.
- **Pop-outs:** the window button opens a chart or the Market, Watchlist, Screener or Alerts panel in another window, which stays in sync with the main one. Closing it or *Bring back* docks it, as does a window that stops responding. Pop-outs survive a reload; after a browser restart, *Restore pop-outs* reopens them (one per click unless pop-ups are allowed) or *Keep here* docks them. Every window keeps the trading guards (Paper by default, no orders from replay).

## Keyboard shortcuts

⌘ is Ctrl on Windows and Linux.

| Keys | Action |
|---|---|
| ⌘K | Command palette |
| ⌘Z; ⇧⌘Z or ⌘Y | Undo; redo |
| Delete, Backspace | Delete the selected drawing |
| Esc | Cancel a drawing or clear the selection |
| Alt T / H / R / F / M | Trendline / horizontal line / rectangle / Fib / measure |
| Alt 1 / 2 / 4 / 8 | One / two side by side / four / eight charts |
| Alt B / Alt S | Buy / sell ticket |
| Alt 0 | Fit all bars |
| Alt ⇧ R / Alt ⇧ S | Replay / screenshot |

## Troubleshooting

| Message | What to do |
|---|---|
| *Candles didn’t load* / *GET /v1/candles timed out* | Reads stop after 30 seconds. Check the market engine connection and select **Retry candles**. Switching tabs, symbols, timeframes, or feed modes cancels obsolete chart reads. |
| *Connecting* | WebSocket opening attempts retry after 10 seconds. Check the market engine URL and connection. For stocks, check the market status badge: closed sessions have no new regular-session prices, but historical candles should still load. |
| *Venue … is not enabled* | Pick another venue or ask the operator |
| *No API key*, *Trading is not connected* | Ask the operator |
| *Price is too far from the mark* | Prices must be within the account's band (default 10%) |
| *The preview expired* | *Preview again* |
| *Live trading is not enabled for this account* | Approval, venue permissions or consent missing; ask the operator |
