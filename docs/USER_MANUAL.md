# Joseph Terminal — user manual

Joseph is a multi-venue perpetual-futures terminal: charts with your drawings, backend-computed SMC zones, a qualification screener, rule-based strategies with alerts, and paper/shadow trading. **Live order placement is not enabled in this release.**

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

- **Instrument**: click the symbol → pick a venue (or *All venues*) → search `BTC`. Results are exact venue instruments: `binance:perp:BTCUSDT` and `hyperliquid:perp:BTC` are different contracts.
- **Timeframe**: favorite buttons; *More* for every timeframe from `1s` to `1M`, ★ to pin favorites, or type a resolution (see *Timeframes* below). **Chart type**: candles, bars, line, area, Heikin Ashi (display only — never used for signals). **Scale**: linear, log, percent.
- **Pane header**: feed mode (Auto, WebSocket, REST polling) and link group (*Unlinked*, *Link A–D*: linked charts follow each other when sync is on).
- **Layouts**: *Layout* → 1, 2, 4 or 8 charts; toggle sync of crosshair, instrument, timeframe and viewport. Workspaces autosave; *⌘K → Export workspace* saves JSON, *Copy workspace share link* shares it.
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

### True delta

- The **Delta %** pane draws the engine's value per bar. **True** bars (solid) come from the venue's taker buy/sell volume; **estimate** bars (faint) sign the bar volume by candle direction when the venue publishes no taker volume. The pane title says which you see: *Delta % (true)*, *(estimate)* or *(true; faint = estimate)*.
- The data window adds *Delta source* (True or Estimate) and, for true bars, *Buy volume* and *Sell volume*.
- Layers → ⚙ Order flow → **Delta source**: *auto* (true when available) or *estimate* (always the estimate).

### Detachable panes

- Open a chart (window button in its header) or the Market, Watchlist, Screener or Alerts panel (window button beside the panel tabs) **in another window**. The slot shows *Open in another window* with **Focus window** and **Bring back**.
- The window follows the main one: link groups and sync (symbol, timeframe), crosshair, theme and workspace changes. Changes made there (symbol, timeframe, drawings, layers) come back to the main window.
- Closing the window, or *Bring back* in either window, docks the pane again; a window that stops responding is docked automatically.
- Detached panes are saved with the layout. After a reload, click **Re-open detached windows** (browsers only open windows from a click; Chrome allows one per click) or *Keep here*.
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

Both re-request their values when you move an anchor or a bar closes, follow bar replay, and show their values in the data window (*Anchored VWAP*, *VWAP ±1σ*, *Regression end*, *Channel upper/lower*, *Pearson's R*) and the Objects tab. The engine covers the latest 2000 bars: an older anchor shows *Anchor is more than 2000 bars back — move it closer* instead of a line; drag it closer. If the engine can't answer, the drawing reads *Engine values unavailable* and draws nothing.

Drawings are saved per instrument in this browser; *Export workspace* saves layout and chart settings, not drawings.

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

## 7. Trading (paper and shadow)

1. *Order ticket*: choose account and mode (**Paper**; **Shadow** records what would be sent; **Live** is disabled with the reason shown).
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
| *Stale* ribbon | Feed lagging; new orders are blocked until it recovers |
| *Price is too far from the mark* | Limit prices must be within the account's band (default 10%) |
| *Preview expired* | Click *Preview again* |
| *Live trading is not enabled for this account* | Expected: live trading requires approvals not present in this release |
