# Joseph Terminal — user manual

Joseph is a multi-venue terminal for crypto perpetuals, Taiwan stocks (TWSE), US stocks (Nasdaq/NYSE) and US bonds and rates (Treasury yields and bond ETFs): charts with your drawings, backend-computed SMC zones, a qualification screener, rule-based strategies with alerts, and paper, shadow and live trading. Live orders go through crypto venues, Interactive Brokers (US) and SinoPac Shioaji (TWSE), only when the execution engine enables them; Cathay is listed as *awaiting API SDK*. Futures and options are not offered.

## 1. Screen layout

```text
┌ Top bar: instrument · timeframes · chart type · scale · Layers · Layout · data badge · mode · theme · ⌘K ┐
├─┬──────────────────────────── chart panes (1/2/4/8) ───────────────────────┬─ side panel ──────────┤
│✎│ pane header: symbol · venue · timeframe · OHLC · "as of" · provenance    │ Market · Watchlist ·   │
│ │ candles + volume + zones (FVG/OB/…) + events (BOS, sweeps) + drawings    │ Screener · Strategy ·  │
│ │                                                                          │ Order ticket           │
├─┴──────────────────────────────────────────────────────────────────────────┴───────────────────────┤
│ Positions · Orders · Executions · Order history · Alerts · Notifications · Audit · Risk · Data health│
└ status bar: venue health · UTC clock · chart attribution ─────────────────────────────────────────────┘
```

Every panel can be collapsed; the side panel and bottom dock remember the last tab.

## 2. Reading data safely

| You see | Meaning | Can it drive real orders? |
|---|---|---|
| Solid green ribbon, `Live` | Native venue WebSocket | Yes (when live trading exists) |
| Dashed amber ribbon, `Polled` | REST polling fallback, labelled | No — display only |
| Dotted violet ribbon, `Replay` | Historical replay | Never |
| Red ribbon, `Stale 42s` | Data older than its budget | New exposure is blocked |
| Diagonal hatch, `Demo data` | Synthetic DEMO data | Never |
| Red ribbon, `Unavailable` + message | Venue not reachable/enabled | Chart stays empty — never filled with fake prices |

Magenta marks are **yours** (drawings, draft orders, rule cards). Green/red are market direction. Zones and events are computed by the Python engine; the chart caption shows when (`as of 17:30 UTC`).

## 3. Charts

- **Instrument**: click the symbol to open the market picker. Tabs group instruments by asset class — **Crypto**, **Taiwan stocks**, **US stocks**, **Bonds & rates** (Treasury yields and bond ETFs) — and *All* lists every group in sections. Search by symbol (`BTC`, `AAPL`, `2330`) or by name, including Chinese names on TWSE (`台積電`); narrow to one venue with the venue menu. Results are exact venue instruments: `binance:perp:BTCUSDT` and `hyperliquid:perp:BTC` are different contracts. Each row shows the venue badge, the listing exchange (e.g. *NYSE Arca* for an ETF listed there), the type and the quote currency or *Yield, %*. An amber *Delayed 15 min* badge means this deployment only receives delayed data for that venue; a red *No API key* badge means the venue's data source (Alpaca for Nasdaq/NYSE) has no key configured, so its charts stay empty until the operator adds one.
- **Timeframe**: favorite buttons; *More* for every timeframe from `1s` to `1M`, ★ to pin favorites, or type a resolution (see *Timeframes* below). **Chart type**: candles, bars, line, area, Heikin Ashi (display only — never used for signals). **Scale**: linear, log, percent.
- **Pane header**: feed mode (Auto, WebSocket, REST polling) and link group (*Unlinked*, *Link A–D*: linked charts follow each other when sync is on).
- **Layouts**: *Layout* → 1, 2, 4 or 8 charts; toggle sync of crosshair, instrument, timeframe and viewport. Workspaces autosave; *⌘K → Export workspace* saves JSON (with your drawings), *Copy workspace share link* shares the layout.
- **Layers**: show/hide each indicator; ⚙ opens its settings for this chart — engine **inputs** (validated against the plugin's schema and sent with every query), which **zone kinds** to draw, per-kind and per-marker **colors**, event markers and, for order flow, the **Delta % pane**. *Market structure* draws HH/HL/LH/LL/EQH/EQL, BOS/MSS/initial-break markers with break lines, and dashed rays for the unbroken swing high/low. *Session breaks* draws UTC separators (minute/hour on seconds, day below 1d, week/month/year on 1d/1w/1M); *Data window* shows OHLC, change, volume, Delta % and compared closes under the crosshair. *Show or hide ended zones* reveals invalidated/swept history. By default the 4 most recent zones per kind and side are drawn.
- **Panes**: volume and the order-flow **Delta %** pane (see *True delta* below) sit below the price pane (drag the separator to resize). The Delta pane shows ±threshold lines from `min_delta_share`; if the engine can't serve the series it says *unavailable* instead of drawing anything.
- **Compare**: add up to 3 instruments; they share a percentage axis on the left, measured from the first visible bar. Remove with × on the chip.
- **Templates** (▦): save the chart's style, scale, volume, layers (inputs, colors) and session breaks under a name; apply to any chart. Templates never change the instrument.
- **Replay**: *Replay* → click a bar (or *Start 100 bars back*) → step, play/pause, change speed (1–10 bars/s). Later bars are hidden, zones are requested as of the cursor bar's close, the ribbon turns **Replay** and trading from that chart is off. *Back to live* restores everything.
- **Screenshot** (📷): downloads a PNG with a header naming instrument, venue, timeframe, data provenance and UTC time.
- **Resize**: drag the edge of the side or bottom dock, or the gap between charts in multi-chart layouts; sizes are kept.

### Timeframes

- *More* groups **Seconds** (`1s 5s 15s 30s`), **Minutes**, **Hours** and **Days** (`1d 1w 1M`). Case matters: `1m` is a minute, `1M` a calendar month (from the 1st, 00:00 UTC; 28–31 days).
- Type TradingView-style: `15` or `15m` (minutes), `4H`, `1D`, `1W`, `5S`/`1s` (seconds), `1M` or `M` (month). Anything the engine doesn't serve is refused, never approximated.
- Timeframes the chart's venue doesn't publish are dimmed with the reason. Seconds marked *trades* are built by the engine from the venue's public trades, so their history is short.
- Seconds charts show seconds on the time axis; session breaks become minutes (`1s`) or hours (`5s`–`30s`), and years on `1M`. The chip next to the timeframe counts down to the bar close; monthly bars close on the 1st, and the data window shows each bar's close (*Closes*).

### Session markets (stocks, ETFs, Treasury yields)

- Exchange-listed instruments trade in **sessions**: TWSE 09:00–13:30 Taipei, NYSE/Nasdaq 09:30–16:00 New York (13:00 on early-close days), Treasury yields on bond-market days 08:00–17:00 New York. Bars follow the exchange calendar exactly as the engine builds them: intraday bars start at the open and repeat every timeframe, and the last bar is cut at the close (NYSE `1h`: 09:30, 10:30 … 15:30–16:00); a daily bar is one session; weekly and monthly bars hold the sessions of a week or calendar month. TWSE's 13:30 closing auction belongs to the last bar.
- The chart has **no empty time between sessions**: every bar takes one slot, so Friday's last bar sits right next to Monday's first and nights, weekends and holidays don't stretch the axis. Drawings anchored in the future or further back than the loaded bars are placed by counting session bars too. *Layers → Session breaks* draws a dotted line at each new session (week on `1d`, month on `1w`).
- Times on the axis, the crosshair, the data window (*Closes*) and the status badge are **exchange time** (`ET`, `TPE`); crypto charts stay in UTC.
- The **market status badge** in the chart header says *Open · closes 16:00 ET* or *Closed · opens Mon 09:30 ET* (with a countdown within 12 hours), *Holiday* on exchange holidays and *Early close today at 13:00 ET*. The bar countdown chip runs while the market is open. Calendars come from the market engine (`/v1/calendars/…/sessions`, loaded around what you look at); until they arrive, regular weekday hours are assumed.
- **Prices** use the instrument's tick: TWSE prices follow the exchange's tick ladder (e.g. `48.55` below 50, `585` between 500 and 1000), US stocks two decimals. The header shows the listing exchange and currency (*NYSE · USD*, *TWSE · TWD*).
- **Volume**: TWSE volume in the data window and Market panel is in **board lots (張, 1 張 = 1,000 shares)** — what Taiwanese quote screens and brokers show; hover the row for the unit. The volume pane keeps shares in compact form (e.g. `25.3M`), as on TradingView. US stocks and ETFs show shares (`52,300 sh`).
- **Yields** (e.g. *US 10-Year Treasury yield*) open as a **line chart** in percent (`4.253%`), with no volume and no Delta pane. Yields are published rates, not tradable instruments: the order ticket says so instead of showing a form, and right-click trading is off.
- In DEMO mode, `mock:stock:ACME` (NYSE hours), `mock:etf:TLTD` (a bond ETF), `mock:stock:9901` (示範半導體, TWSE hours, tick ladder, ±10% limit, 1,000-share lots) and `mock:rate:UST10Y` exercise these paths offline.

### True delta

- The **Delta %** pane draws the engine's value per bar. **True** bars (solid) come from the venue's taker buy/sell volume; **classified** bars come from stock exchanges, which publish no aggressor side: the engine classifies each trade as a buy or sell against the prevailing quote (Lee–Ready); **estimate** bars (faint) sign the bar volume by candle direction when neither is available. The pane title says which you see: *Delta % (true)*, *(classified)*, *(estimate)* or a mix such as *(true; faint = estimate)*.
- The data window adds *Delta source* — *True (taker volume)*, *Classified (Lee–Ready)* or *Estimate* — and, for true and classified bars, *Buy volume* and *Sell volume*.
- Layers → ⚙ Order flow → **Delta source**: *auto* (true when available) or *estimate* (always the estimate).

### Detachable panes

- Open a chart (window button in its header) or the Market, Watchlist, Screener or Alerts panel (window button beside the panel tabs) **in another window**. The slot shows *Open in another window* with **Focus window** and **Bring back**.
- The window follows the main one: link groups and sync (symbol, timeframe), crosshair, theme and workspace changes. Changes made there (symbol, timeframe, drawings, layers) come back to the main window.
- Closing the window, or *Bring back* in either window, docks the pane again; a window that stops responding is docked automatically.
- Reloading the terminal doesn't close detached windows: they keep running, show *Reconnecting to the window that kept running…* for a moment and link up again by themselves — no click needed. While the main window reloads, a pop-out's link dot shows an amber ring; changes you make there (symbol, timeframe, drawings, layers) are kept and sent to the main window when it is back.
- After a full browser restart the windows are gone, but the layout remembers them: click **Restore pop-outs** in the banner (or *⌘K → Restore pop-outs*) to open them again where they were, or *Keep here* to dock them.
- Browsers only open windows from a click: Chrome, Edge, Firefox and Safari open one window per click unless pop-ups are allowed for the site. Allow pop-ups for this site (the blocked-pop-up icon in the address bar) and one click restores every window; otherwise click *Restore pop-outs* once per window — the terminal says how many it could open.
- Each window has its own data connection (DEMO or engine) and the same trading guards: Paper by default, nothing from a replaying chart, Live only when the execution engine allows it. In DEMO each window simulates its own paper broker.

### Drawing tools (left toolbar)

Grouped flyouts (› opens a group): trendline, ray, extended line, horizontal line/ray, vertical line, parallel channel · rectangle, zone box (FVG-style box with 50% line and name), polyline (click each point; double-click or Esc to finish) · Fibonacci retracement/extension · long/short position (risk/reward boxes, tick-rounded, never places orders) · measure · text note, arrow, brush (freehand). Selecting a drawing opens a **style bar** (color, width, dash, fill, extend, text, z-order, lock, hide, delete); the **Objects** tab lists every drawing with show/hide, lock, reorder and delete. All edits undo with ⌘/Ctrl Z. The extended tools, anchored VWAP and the regression channel are described below. Drawings are saved per instrument with time/price anchors, so they survive zoom, reload and app updates. *Magnet* snaps to candle OHLC; *Lock* prevents edits; *Hide* toggles visibility. Drawings from tools that were removed are kept and shown as "unsupported", never deleted.

### Extended drawing tools, anchored VWAP and regression channel

Place points by clicking (or tapping) each one; a two-point tool can also be dragged. Every tool supports the style bar, lock, hide, undo, the Objects tab and the command palette (*Draw ellipse*, *Draw gann fan*…).

| Group | Tools and points |
|---|---|
| Shapes | **Ellipse** (two corners) · **Circle** (centre, edge) · **Triangle** (three corners) · **Rotated rectangle** (one side, then a point setting the width) · **Arc** (start, a point on it, end) · **Curve** (start, control point, end) |
| Lines | **Andrews pitchfork** (pivot, then the two reaction points; the tines run to the chart edge) |
| Fibonacci | **Fib time zones** (zone 0, zone 1; lines at 1, 2, 3, 5, 8, 13… units) · **Fib circles** (centre, a point on the 1.0 circle) |
| Gann | **Gann fan** (origin, the point fixing 1×1; rays 1×8 to 8×1) · **Gann box** (two corners; grid at 0.25, 0.382, 0.5, 0.618, 0.75 and diagonals) |
| Patterns | **XABCD** (5 points; AB/XA, BC/AB, CD/BC and AD/XA ratios) · **ABCD** (4) · **Head and shoulders** (7: start, shoulder, neck, head, neck, shoulder, end; dashed neckline) · **Elliott impulse** (0–5) and **correction** (0, A, B, C) · **Triangle pattern** (4; boundaries run to the apex) |

Pattern ratios come from your own anchor prices; the Objects tab lists them (for example *AB/XA 0.618, …*).

**Engine studies** (anchor icon) draw values computed by the market engine, never by the browser:

- **Anchored VWAP**: click one bar. The engine's VWAP (hlc3) runs from that bar to the latest closed bar. *Band 1–3* in the style bar add ±0.5σ to ±3σ bands; *Fill* shades band 1.
- **Regression channel**: click (or drag from) the first bar, then the last bar. The engine fits the closes between them. *Deviations* sets the channel width (1σ–3σ), *Pearson's R* shows the fit label and *Fill* shades the channel.

Both re-request their values when you move an anchor or a bar closes, follow bar replay, and show their values in the data window (*Anchored VWAP*, *VWAP ±1σ*, *Regression end*, *Channel upper/lower*, *Pearson's R*) and the Objects tab. The engine computes from anchors up to 50,000 bars back. Only when the engine refuses an anchor does the drawing show the engine's own message (and the data window says *refused by the engine*); drag the anchor closer. If the engine can't be reached, the drawing reads *Engine values unavailable* and draws nothing. On session markets anchors snap to session bars.

Drawings are saved per instrument in this browser. *⌘K → Export workspace* saves a JSON file with the layout, chart settings **and the drawings of every instrument** in the workspace (format 2; drawings from tools that were removed or from a newer version are included unchanged). *⌘K → Import a workspace* accepts that file (up to 2 MB) as well as older exports without drawings: the layout is replaced, and each instrument in the file replaces that instrument's saved drawings — instruments not in the file keep theirs. Open charts show the imported drawings at once; unreadable entries are skipped and counted in the message. Share links still carry only the layout, never drawings.

## 4. Watchlist and screener

- **Watchlist**: *My list* (add, reorder) or *Qualified perpetuals* (the engine's current Qualified list, read-only). Columns ▥: last, mark, 24h, turnover, spread, 8h funding, OI, data freshness; click a header to sort (again to reverse, a third time to return to your order). Click a row to chart it.
- **Screener**: qualification per exact venue instrument. A contract is **Qualified** only if all five gates pass — 24h turnover, open-interest USD, turnover/OI, spread, and $50k slippage — with *strict* thresholds (a value equal to a threshold fails) and funding not high-risk. **Missing data** is never shown as a pass. Tags: *Watch* (|funding| ≥ 0.10%/8h), *High risk* (≥ 0.30%/8h). *Preset*: save the venue/status/sort filters under a name and recall them.

## 5. Strategies

1. *Strategy* tab → *Start from a template* (Long A / Short B) or build from blank.
2. Conditions are cards inside *All of* / *Any of* groups. Each card reads as a sentence ("4h bias is bullish", "Close is inside the 1h discount zone (range group A)"). Use ✎ to edit, *Negate* for NOT, *Add alternatives* for OR. Zones in the same **range group** must come from the same dealing range.
3. *Validate* checks the rule on the server; errors point at the card that caused them.
4. *Save strategy* creates an **immutable version**. Editing later creates a new version; activations never carry over.
5. *Backtest* runs the same evaluator as live with conservative fills (next-bar entry, stop assumed before target, fees).
6. *Activate* in **Alerts only**, **Paper** or **Shadow** mode. **Auto live** is locked: it requires an execution-engine deployment with explicit, scoped consent and is disabled in this release.

Signals fire only on **closed bars**, never on history that closed before activation, and at most once per setup (cooldown and once-per-zone apply). Missing or stale data makes a condition *unknown*, which blocks the entry.

## 6. Alerts and notifications

- **Alerts** (bottom dock) lists signals newest first: kind (Entry, Entry blocked, Setup forming…), instrument, R:R and data source. Expand a row for the **reason tree** (every condition true/false/unknown). *Show on chart* jumps to the exact venue, bar and zone. *Sort*: newest, R multiple, confluence (conditions satisfied), bar volume, distance to AOI (latest engine price vs the signal's zones; *Inside* = 0) or oldest first. *Acknowledge*, *Snooze* (15 min / 1 h / 4 h) and *Dismiss* hide a signal locally; *Show snoozed and dismissed* brings them back (*Wake*, *Restore*).
- **Notifications**: add Telegram (chat id), LINE (user/group id), e-mail or an HTTPS webhook; choose which signal kinds, a minimum severity, and quiet hours (critical alerts still arrive). If the server has no credentials for a channel the row shows *Not delivered*.

### Signal volume sort

*Sort → Bar volume* ranks signals by the signal bar's **quote turnover** (the *Bar vol.* column, compact, e.g. `1.2M`; hover for the exact value). It is the venue-published turnover of the evaluated bar, comparable across instruments, and never estimated: *—* means the venue publishes none, and those signals sort last.

## 7. Trading (paper, shadow and live)

- **Accounts**: the *Accounts* tab (bottom dock) lists every account the execution engine reports: the USD paper account (crypto, Nasdaq, NYSE), the TWD paper account (TWSE), crypto venues, Interactive Brokers (Nasdaq/NYSE), SinoPac Shioaji and Cathay (TWSE). It shows what each account trades, its connection (*Connected*, *Disconnected*, *Verifying permissions*, *Read only*), whether broker credentials are configured on the server, the permitted modes, equity, settled and unsettled cash, and the engine's notes. Broker credentials are set up by the operator on the execution server and are never entered in the browser. Cathay shows *Awaiting API SDK*: it can't take orders until Cathay releases a trading API.
- **Routing**: the ticket picks an account whose exchanges include the instrument's (IBKR for US stocks and ETFs, SinoPac or Cathay for TWSE, the venue's own account for crypto). Choosing another account shows why it can't be used.
- **Stock orders**: quantities are whole shares. Time in force and order types come from the account (DAY or GTC on IBKR, DAY on TWSE brokers; SinoPac offers no stop orders). On TWSE, *Board lot (張)* (1 張 = 1,000 shares) takes any order type; *Odd lot (零股)* (1–999 shares) is a limit DAY order, available only where the account allows odd lots; the hint shows the share count. Prices snap to the TWSE tick ladder, and a price outside today's ±10% limit around the previous close is refused before preview. While the exchange is closed, market orders are refused until the next open unless the account queues them (queued market orders must be DAY); IOC and FOK need an open session, and the ticket says when the next session opens. Cash accounts cannot sell more shares than they hold. *Allow outside regular trading hours* applies to IBKR limit and stop-limit orders. Stocks have no leverage, reduce-only or attached stop/target; prices, the account's settled cash and the preview's *Cash required* are in the instrument's currency (USD, TWD).
- **Live trading**: *Live* is available only when the execution engine enables it for the account (LIVE among its modes and the adapter available). Clicking it asks you to **arm** live trading for that account. Arming is done by the execution engine, not the browser: tick that you understand it is real money, choose how long (*Arm for* 15 minutes, 1 hour, 4 hours or 8 hours; default 1 hour) and enter the step-up code from your authenticator app. The engine checks the code the same way as automatic-strategy consent and arms the account for at most `JOSEPH_LIVE_ARM_MAX_MS` (8 hours unless the operator sets less; never more than 24 hours). A wrong code is refused, and paper accounts or accounts that are not verified cannot be armed. Each grant, refusal and disarm is in the audit log.
  - While armed, the ticket shows a **Live armed** badge with "*account* until HH:MM UTC" (the engine's time) and a *Disarm* button. *Disarm* tells the engine to disarm and puts the ticket back in paper. When the arming expires, the ticket returns to paper on its own.
  - If the engine refuses a LIVE order with `LIVE_NOT_ARMED` (the arming expired or was disarmed in another window), the ticket returns to paper and asks you to arm again. Amending a LIVE order also needs the account armed.
  - Closing or reducing a position (reduce-only, never more than the position) still works while disarmed, with a warning. So do cancelling orders, the kill switch (including *flatten*) and reconciliation.
  - Automatic strategies (AUTO_LIVE) don't use account arming; they need their deployment's own step-up consent.
  - Each LIVE order then opens a confirmation listing **LIVE money**, broker, account, action, quantity (shares and 張), price, currency and estimated value; tick *Send this order with real money* to send it. Nothing in the browser can enable LIVE; the engine re-checks every order.
  - In DEMO mode no account offers LIVE. To rehearse the flow, open the terminal with `?demo-live-rehearsal=1`: an *IBKR live rehearsal (demo)* account appears, the simulated engine accepts the step-up code `123456`, and armed orders are simulated in the browser (nothing reaches a broker).
- **Rates**: Treasury yields such as UST10Y are published rates, not tradable instruments, so they have no order ticket and no chart trading menu. Trade a bond ETF to take a position on rates.

1. *Order ticket*: choose account and mode (**Paper**; **Shadow** records what would be sent; **Live** only as described above, otherwise disabled with the reason shown).
2. Pick Buy/Sell, type (market, limit, stop), quantity (*Size from risk* computes it from a stop and % of equity), leverage, reduce-only, stop-loss/take-profit.
3. **Preview** shows normalized (tick/lot-rounded) values, notional, fee, margin, slippage, whether protection is venue-native or software-managed, and every risk check.
4. The confirm button names the exact action ("Place paper limit buy 0.010 BTC at 95,000.0"). Any edit invalidates the preview. Double-clicking sends one order.
5. Right-click the chart → *Buy limit at…* prefills the ticket; drag an order's SL/TP line to open an amend preview.

After submission the ticket shows the venue-reported state (*Submitting*, *Acknowledged*, *Partially filled*, *Filled*). **Unknown, reconciling** means the outcome is being verified — it is never assumed canceled, and nothing is resent until verified.

**Positions**: *Close* is always reduce-only (never flips); *Reverse* is two confirmed steps (close, then open once flat). **Risk controls**: the kill switch blocks new exposure; cancelling orders and closing positions are separate checkboxes. **Audit** lists every action with a hash-chain integrity check.

## 8. Shortcuts

| Keys | Action | Keys | Action |
|---|---|---|---|
| ⌘/Ctrl K | Command palette | Alt T / H / R / F / M | Trendline / horizontal / rectangle / Fib / measure |
| ⌘/Ctrl Z, ⇧⌘Z or ⌘Y | Undo / redo drawing | Alt 1 / 2 / 4 / 8 | Layout |
| Delete / Backspace | Delete selected drawing | Alt B / Alt S | Ticket buy / sell |
| Esc | Cancel current drawing (finishes a polyline) | Alt 0 | Fit all bars |
| Alt ⇧ R | Bar replay on/off | Alt ⇧ S | Screenshot |

## 9. Troubleshooting

| Message | What to do |
|---|---|
| *Candles didn't load: venue … is not enabled* | The market engine doesn't serve that venue; pick another or ask the operator to enable it |
| *No API key* badge in the market picker | The venue's data source (Alpaca for Nasdaq/NYSE) has no key on this deployment; ask the operator to configure it |
| *Delayed 15 min* badge | This deployment is entitled to delayed data only for that venue; prices and bars lag by that much |
| Chart header says *Closed · opens …* | The exchange is between sessions; bars resume at the next open and nothing is drawn for the night or weekend |
| *Stale* ribbon | Feed lagging; new orders are blocked until it recovers |
| *Price is too far from the mark* | Limit prices must be within the account's band (default 10%) |
| *Preview expired* | Click *Preview again* |
| *Live trading is not enabled for this account* | The execution engine has not enabled LIVE for that account (venue permissions, consent or broker connection missing); ask the operator |
| Account shows *Awaiting API SDK* | Cathay has no trading API yet; use SinoPac for TWSE orders |
