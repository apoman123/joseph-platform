# Joseph Terminal — user manual

Joseph charts crypto perpetuals, Taiwan stocks and ETFs (TWSE, TPEx), US stocks and ETFs (Nasdaq, NYSE, Cboe BZX) and US Treasury yields. It shows your drawings and the engine's SMC zones, screens perpetuals, runs rule-based strategies with alerts, and trades in paper, shadow and live mode. Futures and options are not offered.

## 1. Layout

- **Top bar:** instrument, timeframes, chart type, scale, *Layers*, *Layout*, data badge, mode, theme, ⌘K.
- **Left toolbar:** drawing tools. **Centre:** 1, 2, 4 or 8 chart panes; each header shows symbol, venue, timeframe, OHLC, "as of" time and provenance.
- **Side panel:** Market, Watchlist, Screener, Strategy, Order ticket. **Bottom dock:** Positions, Orders, Executions, Order history, Alerts, Notifications, Audit, Risk, Data health.
- **Status bar:** venue health, UTC clock, chart attribution. Panels collapse and remember their last tab.

## 2. Reading data safely

| You see | Meaning | Can it drive real orders? |
|---|---|---|
| Solid green `Live` | Native venue WebSocket | Yes |
| Dashed amber `Polled` | REST polling fallback | No, display only |
| Dotted violet `Replay` | Historical replay | Never |
| Red `Stale 42s` | Data older than its budget | New exposure is blocked |
| Hatched `Demo data` | Synthetic DEMO data | Never |
| Red `Unavailable` + message | Venue unreachable or not enabled | Chart stays empty, never filled with fake prices |

Magenta marks are yours (drawings, draft orders, rule cards); green and red are market direction. Zones and events come from the engine, stamped `as of` a time.

## 3. Charts

- **Instrument:** click the symbol. Tabs: *Crypto*, *Taiwan stocks*, *US stocks*, *Bonds & rates* (yields and bond ETFs such as `tpex:etf:00679B`), *All*. Search by symbol (`BTC`, `2330`, `6488`) or name, including Chinese (`台積電`, `美債`). Each result is one venue instrument: `binance:perp:BTCUSDT` and `hyperliquid:perp:BTC` are different contracts. Rows show the venue badge (*TWSE*, *TPEx*, *NSDQ*, *CBOE* …), listing exchange, type and currency. *Delayed 15 min* (amber): only delayed data for that venue. *No API key* (red): the data source (Alpaca for US stocks) is not configured, so charts stay empty.
- **Timeframe, chart type, scale:** `1s` to `1M`, ★ pins favorites; candles, bars, line, area, Heikin Ashi (display only, never used for signals); linear, log, percent.
- **Pane header:** feed mode (Auto, WebSocket, REST polling) and link group (*Link A–D*; linked charts follow each other).
- **Layout:** 1/2/4/8 charts; sync crosshair, instrument, timeframe and viewport. Workspaces autosave; *⌘K → Copy workspace share link* shares the layout. Drag dock edges and chart gaps to resize.
- **Layers:** per indicator, ⚙ sets engine inputs (validated by the engine), zone kinds, colors, markers and the *Delta %* pane. *Market structure* draws HH/HL/LH/LL/EQH/EQL, BOS/MSS/initial breaks and rays for unbroken swings. *Session breaks* draws separators. *Data window* shows OHLC, change, volume, Delta % and compared closes. *Show ended zones* reveals invalidated or swept zones; by default the 4 newest zones per kind and side are drawn.
- **Panes:** volume and *Delta %* (with ±`min_delta_share` lines) below price. A series the engine can't serve reads *unavailable*.
- **Compare:** up to 3 instruments on a left percentage axis from the first visible bar.
- **Templates** (▦) save style, scale, volume, layers and session breaks; applying one never changes the instrument.
- **Replay:** *Replay* → click a bar (or *Start 100 bars back*) → step or play at 1–10 bars/s. Later bars are hidden, zones are as of the cursor bar, and trading from that chart is off. *Back to live* restores it.
- **Screenshot** (📷): PNG with instrument, venue, timeframe, provenance and UTC time.

### Timeframes

- `1m` is a minute, `1M` a calendar month (from the 1st, 00:00 UTC). You can type `15`, `4H`, `1D`, `1W`, `5S` or `M`; anything the engine doesn't serve is refused, never approximated.
- Timeframes a venue doesn't publish are dimmed with the reason. Seconds marked *trades* are built from public trades, so their history is short (table below). A chip counts down to the bar close.

### Session markets (stocks, ETFs, yields)

- **Sessions:** TWSE and TPEx 09:00–13:30 Taipei (the closing auction is in the last bar); NYSE, Nasdaq and Cboe 09:30–16:00 New York (13:00 on early closes); yields on bond-market days 08:00–17:00 New York. Intraday bars start at the open and the last is cut at the close; daily, weekly and monthly bars hold whole sessions.
- **No empty time:** nights, weekends and holidays take no space; drawings beyond loaded bars are placed by counting session bars. Times are exchange time (`ET`, `TPE`); crypto stays UTC.
- **Status badge:** *Open · closes 16:00 ET*, *Closed · opens Mon 09:30 ET*, *Holiday*, *Early close today*. Until the engine's calendar loads, regular weekday hours are assumed.
- **Prices** follow the instrument's tick (Taiwan stock ladder, e.g. `48.55` below 50 and `585` from 500; Taiwan ETFs 0.01 below 50, else 0.05; US 0.01). **Volume:** Taiwan in **張** (1 張 = 1,000 shares) in the data window and Market panel, shares on the volume pane; US in shares.
- **US closing print:** on consolidated data (`sip`, `delayed_sip`) the last minute bar includes the official closing print, so 1m, 1h and 1d closes agree. Until the engine reads it, an older session's last minute shows as a gap.
- **Taiwan data:** official data gives coarse 1m bars, no seconds and intraday history only since the engine started recording. With SinoPac data enabled by the operator: exact 1m bars back to 2020-03-02, seconds bars and live 5-level books (board lots only). If SinoPac fails, official data takes over and the provenance says so.
- **Yields** (e.g. *US 10-Year Treasury yield*) are percent line charts with no volume, Delta pane or ticket.
- **DEMO:** `mock:stock:ACME` (NYSE hours), `mock:etf:TLTD` (bond ETF), `mock:stock:9901` (TWSE rules), `mock:etf:00999B` (TPEx bond ETF, no daily limit), `mock:rate:UST10Y`.

### Delta

The *Delta %* pane is per bar: **true** (solid) from venue taker volume; **classified** for stocks, whose exchanges publish no aggressor side, so each trade is classified against the quote (Lee-Ready); **estimate** (faint) from candle direction. The title and the data window's *Delta source* name it; true and classified bars also show buy and sell volume. Layers → ⚙ Order flow → *Delta source*: *auto* or *estimate*.

| Venue | Seconds bars and true delta come from |
|---|---|
| Binance | Daily archive (next day, about 07:30 UTC), REST 48 h, recorded trades |
| Bitget | Archive (UTC+8 days, about a day late), REST 90 days, recorded trades |
| Lighter | Archive from 2025-01-17 (about 7 h after each UTC day), REST about 53 min, recorded trades |
| MEXC, Hyperliquid | Recorded trades only |
| TWSE, TPEx | Nothing on official data; the newest 5 sessions with SinoPac data |
| US stocks | The last 30 minutes of trades |

Unrecorded time shows as a gap, never as invented bars.

### Detachable panes

- A chart or the Market, Watchlist, Screener or Alerts panel opens **in another window** (window button). It follows the main window (links, sync, crosshair, theme, workspace) and sends its changes back. Closing it or *Bring back* docks it; an unresponsive window docks automatically.
- Pop-outs survive a terminal reload and reconnect by themselves. After a browser restart, *Restore pop-outs* reopens them (one per click unless the site may open pop-ups) or *Keep here* docks them.
- Each window has the same trading guards: Paper by default, nothing from a replaying chart, Live only when the engine allows it.

### Drawing tools

- **Basic:** trendline, ray, extended line, horizontal line/ray, vertical line, parallel channel, rectangle, zone box, polyline (double-click or Esc ends it), Fibonacci retracement/extension, long/short position (never places orders), measure, text, arrow, brush.
- **Extended** (click each point in order): ellipse, circle, triangle, rotated rectangle, arc, curve, Andrews pitchfork, Fib time zones and circles, Gann fan and box, XABCD, ABCD, head and shoulders, Elliott impulse and correction, triangle pattern. Pattern ratios (from your anchor prices) appear in the Objects tab.
- The **style bar** sets color, width, dash, fill, extend, text, z-order, lock, hide, delete; the **Objects** tab lists every drawing. ⌘/Ctrl Z undoes; *Magnet* snaps to OHLC. Drawings are saved per instrument by time and price, and drawings of removed tools are kept as "unsupported".
- **Engine studies** (anchor icon), computed by the engine, never the browser: **Anchored VWAP** (click one bar; *Band 1–3* add ±0.5σ…±3σ) and **Regression channel** (first and last bar; *Deviations* 1σ–3σ, *Pearson's R*). They update on anchor moves and bar closes, follow replay, and show values in the data window. Anchors may be up to 50,000 bars back and snap to session bars. A refused anchor shows the engine's message; an unreachable engine shows *Engine values unavailable*.
- **Export/import:** *⌘K → Export workspace* saves layout, chart settings and every instrument's drawings as JSON. *Import a workspace* (≤2 MB) replaces the layout and the drawings of instruments in the file; unreadable entries are skipped and counted. Share links carry no drawings.

## 4. Watchlist and screener

- **Watchlist:** *My list* or *Qualified perpetuals* (read-only). Columns: last, mark, 24h, turnover, spread, 8h funding, OI, freshness; click headers to sort.
- **Screener:** **Qualified** only if all five gates pass (24h turnover, open interest, turnover/OI, spread, $50k slippage) with strict thresholds (equal fails) and funding not high-risk. Missing data never passes. *Watch*: |funding| ≥ 0.10%/8h; *High risk*: ≥ 0.30%/8h. *Preset* saves filters.

## 5. Strategies

1. *Strategy* → *Start from a template* (Long A / Short B) or blank.
2. Conditions are sentence cards in *All of* / *Any of* groups; *Negate* adds NOT, *Add alternatives* adds OR. Zones in one **range group** come from one dealing range.
3. *Validate* checks on the server and points at the failing card.
4. *Save strategy* creates an immutable version; activations never carry over to edits.
5. *Backtest* uses the live evaluator with conservative fills (next-bar entry, stop before target, fees).
6. *Activate* as **Alerts only**, **Paper** or **Shadow**. **Auto live** is locked in this release.

Signals fire only on bars closed after activation, once per setup. Missing or stale data makes a condition *unknown*, which blocks the entry.

## 6. Alerts and notifications

- **Alerts:** kind, instrument, R:R, data source and the expandable **reason tree**. *Show on chart* jumps to the bar and zone. *Sort*: newest, R multiple, confluence, bar volume (venue quote turnover; *—* sorts last), distance to AOI, oldest. *Acknowledge*, *Snooze* (15 min / 1 h / 4 h) and *Dismiss* are local; *Show snoozed and dismissed* restores them.
- **Notifications:** Telegram (chat id), LINE (user or group id), e-mail or an HTTPS webhook, filtered by kind and severity, with quiet hours (critical alerts still arrive). *Not delivered*: the server lacks credentials for that channel.

## 7. Trading

- **Accounts:** USD paper (crypto, US stocks), TWD paper (TWSE, TPEx), crypto venues, Interactive Brokers, SinoPac and Cathay, each with connection state, configured credentials, modes, equity and settled/unsettled cash. Credentials are set on the server, never in the browser. Cathay shows *Awaiting API SDK* and takes no orders; use SinoPac for Taiwan.
- **Routing:** the ticket uses an account serving the instrument's exchange (IBKR for US, SinoPac or Cathay for Taiwan, the venue's account for crypto). Yields have no ticket; trade a bond ETF.
- **Ticket:** choose **Paper**, **Shadow** (records only) or **Live**; side, type, quantity (*Size from risk*), leverage, reduce-only, SL/TP. **Preview** shows rounded values, notional, fee, margin, slippage, native or software protection and every risk check. The confirm button names the exact action; any edit invalidates the preview; a double-click sends once. Right-click the chart to prefill a limit; drag an SL/TP line to amend.
- **After sending:** *Unknown, reconciling* means the outcome is being checked; it is never assumed canceled and nothing is resent.
- **Stocks:** whole shares, no leverage or attached SL/TP, prices and cash in the instrument's currency. IBKR offers DAY or GTC, Taiwan brokers DAY; SinoPac has no stop orders. Taiwan: *Board lot (張)* takes any type; *Odd lot (零股)* (1–999 shares) is limit DAY where allowed; prices snap to the ladder and must be within ±10% of the previous close (not for most bond ETFs or a listing's first five days). Closed market: market orders are refused unless the account queues them; IOC/FOK need an open session. *Outside regular trading hours* applies to IBKR limit and stop-limit orders. Cash accounts can't sell more than they hold.
- **Live:** offered only when the engine enables it for the account. You must **arm** the account: confirm it is real money, pick 15 min, 1 h, 4 h or 8 h, and enter your authenticator code. The engine checks the code and caps arming at the operator's limit (8 h by default, never over 24 h). Paper and unverified accounts can't be armed; every grant, refusal and disarm is audited. A **Live armed** badge shows the end time and *Disarm*; expiry returns the ticket to paper, as does an order refused with `LIVE_NOT_ARMED`. Amending needs arming; closing, cancelling, the kill switch and reconciliation don't. Each live order needs a final real-money confirmation. Nothing in the browser can enable live trading; the engine re-checks every order. Automatic strategies use their deployment's own step-up consent instead.
- **Rehearsal:** in DEMO, `?demo-live-rehearsal=1` adds an *IBKR live rehearsal (demo)* account that accepts code `123456`; nothing reaches a broker.
- **Positions and risk:** *Close* is reduce-only; *Reverse* is two confirmed steps. The kill switch blocks new exposure; cancelling and closing are separate checkboxes. **Audit** lists every action with a hash-chain check.

## 8. Shortcuts

⌘/Ctrl K command palette · ⌘/Ctrl Z undo, ⇧⌘Z or ⌘Y redo · Delete removes the selected drawing · Esc cancels a drawing · Alt T / H / R / F / M trendline, horizontal, rectangle, Fib, measure · Alt 1 / 2 / 4 / 8 layout · Alt B / Alt S ticket buy / sell · Alt 0 fit all bars · Alt ⇧ R replay · Alt ⇧ S screenshot.

## 9. Troubleshooting

| Message | What to do |
|---|---|
| *Venue … is not enabled* | Pick another venue or ask the operator |
| *No API key* / *Delayed 15 min* | The operator's data plan; ask them |
| *Stale* ribbon | Feed lagging; new orders wait until it recovers |
| *Price is too far from the mark* | Limits must be within the account's band (default 10%) |
| *Preview expired* | *Preview again* |
| *Live trading is not enabled for this account* | Permissions, consent or broker connection missing; ask the operator |
