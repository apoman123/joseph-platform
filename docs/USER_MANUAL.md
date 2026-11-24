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
| Top bar | Instrument, timeframes, style, scale, *Layers*, *Layout*, compare, templates, replay, screenshot, data badge, order mode, theme, ⌘K |
| Left toolbar | Drawing tools, undo/redo, magnet, lock all, hide all |
| Centre | 1, 2, 4 or 8 charts |
| Side panel | Market, Watchlist, Screener, Strategy, Objects, Order ticket |
| Bottom panel | Positions, Orders, Executions, Order history, Alerts, Notifications, Audit, Risk controls, Accounts, Data health |
| Status bar | Venue feed health, UTC clock, chart attribution, licences |

Pane headers show instrument, timeframe, bar-close countdown, market status, link group (*Link A–D*: linked charts follow each other), feed menu (*Auto feed*, *WebSocket*, *REST polling*), OHLC and data badge. Panels collapse; drag edges and chart gaps to resize.

### Data badges

| Badge (ribbon) | Meaning |
|---|---|
| *Live* (solid green) | Native venue stream |
| *Polled* (dashed amber) | REST fallback: fine for charts, not for order flow or automation |
| *Replay* (dotted violet) | Historical replay; no orders from this chart |
| *Stale for 42s* (red) | No fresh data; new exposure is blocked |
| *Demo data* (hatched) | Synthetic, not a real market |
| *Unavailable* (red dashes) | Venue unreachable or not enabled; the chart stays empty |

Magenta marks are yours (drawings, draft orders, rule cards); green and red show direction.

## Charts

- **Instrument:** click the symbol. Tabs: *All*, *Crypto*, *Taiwan stocks*, *US stocks*, *Bonds & rates*. Search by symbol (`BTC`, `2330`) or name (`台積電`). Each result is one venue's contract: `binance:perp:BTCUSDT` and `hyperliquid:perp:BTC` differ. *Delayed 15 min*: the deployment gets delayed data. *No API key*: the data source (Alpaca for US stocks) is not configured, so charts stay empty.
- **Style and scale:** candles, bars, line, area, Heikin Ashi (display only, never used for signals); linear, logarithmic, percent.
- **Compare** up to 3 instruments on a left percentage axis.
- **Bar replay:** click *Bar replay*, then a bar (or *Start 100 bars back*); step or play at 1–10 bars/s. Later bars are hidden, zones are as of the cursor bar and trading from that chart is off. *Back to live* ends it.
- **Screenshot:** PNG with instrument, venue, timeframe, data badge and UTC time.

### Timeframes

| Group | Timeframes |
|---|---|
| Seconds | `1s` `5s` `15s` `30s` |
| Minutes | `1m` `3m` `5m` `15m` `30m` |
| Hours | `1h` `2h` `4h` `6h` `12h` |
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
- **US closes:** on consolidated data (`sip`, `delayed_sip`) the last minute bar includes the official closing print.
- **Taiwan data:** official data gives coarse 1m bars, no seconds, and intraday history only since the engine started recording. Optional SinoPac data adds exact 1m bars back to 2020-03-02, seconds bars and 5-level books; if it fails, official data takes over.
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
- **Engine studies** are computed by the engine, never the browser. *Anchored VWAP*: click one bar; *Band 1–3* add 0.5σ–3σ bands. *Regression channel*: click the first and last bar; *Deviations* 1σ–3σ, *Pearson's R*. They update on anchor moves and bar closes, follow replay and accept anchors up to 50,000 bars back; a refused anchor shows the engine's message.

## Indicators

*Layers* has one layer per indicator plugin the engine serves.

- ⚙ opens a layer's settings: engine inputs (validated by the engine), zone kinds, colours, markers and the *Delta % pane*.
- The 4 newest zones per kind and side are drawn; *Show or hide ended zones* adds zones that have ended.
- The same menu toggles *Volume*, *Session breaks* and the *Data window* (OHLC, change, volume, Delta %, compared closes, study values).
- One optional layer derives from a CC BY-NC-SA 4.0 script: no commercial use while it is enabled ([licensing](reports/LICENSING_REPORT.md#luxalgo-derived-indicator-cc-by-nc-sa-40)).

### Delta

The *Delta %* pane shows each bar's buy minus sell volume as a share of its volume. Bars are **true** (solid, venue taker volume), **classified** (stocks: trades classified against the quote with Lee-Ready) or **estimate** (faint, candle direction); the data window's *Delta source* says which, and ⚙ *Order flow* → *Delta source* can force *estimate*. Seconds bars and true delta need trade history:

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

1. *Strategy* → *Start from a template* (Long A, Short B or blank).
2. Write conditions as sentence cards in *All of* / *Any of* groups; *Negate* adds NOT, *Add alternatives* adds OR. Zone conditions in one *range group* must match zones of the same range.
3. *Validate* checks on the server and points at the failing card.
4. *Save strategy* creates an immutable version; activations never carry over to edits.
5. Activate as **Alerts only**, **Paper trading** or **Shadow** (record only). *Automatic live* is locked: it needs an execution-engine deployment with limits, scoped consent and LIVE enabled on the server.

Signals fire once per setup, on bars closed after activation. Missing or stale data makes a condition *unknown*, which blocks the entry. The *JSON* tab exports and imports rules.

- **Alerts:** kind, instrument, R:R, data source and an expandable **reason tree**; *Show on chart* jumps to the bar and zone. Sort by time, R multiple, confluence, bar volume or distance to AOI. *Acknowledge*, *Snooze* and *Dismiss* are local.
- **Notifications:** Telegram, LINE, e-mail or an HTTPS webhook, filtered by kind and severity, with quiet hours (critical alerts still arrive). *Not delivered*: the server has no credentials for that channel.

## Backtests

*Strategy* → *Backtest* runs the latest version on the active instrument over 1–365 days of closed bars. It uses the production evaluator with conservative fills (next-bar entry with slippage, stop before target when a bar touches both, fees per side, one position at a time) and reports trades, win rate, expectancy, profit factor, drawdown, average hold and its assumptions. DEMO reports are synthetic and say so.

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
- **Chart templates** save style, scale, volume, layers and session breaks, never the instrument.
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
| *Venue … is not enabled* | Pick another venue or ask the operator |
| *No API key*, *Trading is not connected* | Ask the operator |
| *Price is too far from the mark* | Prices must be within the account's band (default 10%) |
| *The preview expired* | *Preview again* |
| *Live trading is not enabled for this account* | Approval, venue permissions or consent missing; ask the operator |
