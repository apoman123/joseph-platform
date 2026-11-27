// TradeX: auto-fit the chart when an order-flow chart type is picked (2026-11-16).
// Footprint numbers, profiles and TPO letters are unreadable at a normal candle zoom, so each type gets its own view:
//   Volume footprint        ~110 px per candle (8-16 candles across), price axis fitted: the numbers in the cells read clearly
//   Session volume profile  2-3 whole sessions across (a day or two on 15m), so each profile has room to spread out
//   TPO                     about one to two days of 30-minute letter blocks
// Going back to candles (or any other normal style) puts the earlier zoom back. Changing timeframe or symbol while an
// order-flow type is on fits again once the new candles have loaded. Works for every cell of a multi-chart layout.
(function () {
  const SEC = { "1": 60, "5": 300, "15": 900, "30": 1800, "60": 3600, "240": 14400, "1D": 86400, D: 86400 };
  const H = 3600, DAY = 86400;
  // Time span shown, by chart timeframe (seconds). Footprint is set by candle width instead (below).
  const SPAN = {
    session_volume_profile: { "1": 9 * H, "5": DAY, "15": 30 * H, "30": 3 * DAY, "60": 5 * DAY, "240": 21 * DAY, "1D": 120 * DAY },
    tpo: { "1": 6 * H, "5": DAY, "15": 2 * DAY, "30": 4 * DAY, "60": 7 * DAY, "240": 30 * DAY, "1D": 180 * DAY },
  };
  const FP = "volume_footprint", FP_PX = 110;
  const isOF = (s) => s === FP || s in SPAN;

  let ws = null;
  const saved = new Map();        // cell id -> visible range before an order-flow type took over
  const fittedKey = new Map();    // cell id -> "symbol|tf" last fitted

  const cellOf = (id) => (ws.cells ? ws.cells() : [ws.active]).find((c) => c && c.id === id) || ws.active;
  const chartOf = (c) => c && (c.chart || c.inner);
  const styleOf = (ch) => { try { return ch.renderer.get("priceStyle"); } catch (_) { return null; } };
  const tfOf = (c, ch) => String((c && c.timeframe) || (ch && ch.timeframe) || "15").replace(/^D$/, "1D");
  const widthOf = (c) => { const h = c && c.host; const w = h && h.clientWidth; return w > 100 ? w : (document.getElementById("chart") || document.body).clientWidth; };

  function fit(cellId, style) {
    const c = cellOf(cellId), ch = chartOf(c);
    if (!ch || !ch.getVisibleRange) return;
    const vr = ch.getVisibleRange(); if (!vr) return;
    const tf = tfOf(c, ch), sec = SEC[tf] || 900;
    let span;
    if (style === FP) {
      const n = Math.max(6, Math.min(16, Math.round((widthOf(c) - 70) / FP_PX)));   // 70: price axis
      span = n * sec;
    } else span = (SPAN[style] && SPAN[style][tf]) || 2 * DAY;
    const to = vr.to + sec * 1000;                         // one empty slot on the right: the newest candle sits clear of the price axis
    try { ch.renderer.set("autoScale", true); } catch (_) {}
    ch.setVisibleRange({ from: to - span * 1000, to });
    fittedKey.set(c.id, (c.symbol || "") + "|" + tf);
  }

  function onStyle(id, from, to) {
    const c = cellOf(id), ch = chartOf(c);
    if (!ch) return;
    if (isOF(to)) {
      if (!isOF(from) && ch.getVisibleRange) saved.set(c.id, ch.getVisibleRange());
      setTimeout(() => fit(c.id, to), 60);               // after the new style has been applied
    } else if (isOF(from) && saved.has(c.id)) {
      const r = saved.get(c.id); saved.delete(c.id);
      setTimeout(() => { try { ch.renderer.set("autoScale", true); ch.setVisibleRange(r); } catch (_) {} }, 60);
    }
  }

  function onLoaded(id) {   // new timeframe/symbol loaded (or a reload that restored an order-flow style): fit again
    const c = cellOf(id), ch = chartOf(c), s = styleOf(ch);
    if (!isOF(s)) return;
    const key = (c.symbol || "") + "|" + tfOf(c, ch);
    if (fittedKey.get(c.id) !== key) fit(c.id, s);
  }

  function mount(workspace) {
    ws = workspace; saved.clear(); fittedKey.clear();
    if (!ws || !ws.on) return;
    ws.on("cell:priceStyle", (e) => onStyle(e.id, e.from, e.to));
    const hook = (c) => { const ch = chartOf(c); if (ch && ch.on && !ch.__txFit) { ch.__txFit = true; ch.on("history:complete", () => setTimeout(() => onLoaded(c.id), 80)); } };
    (ws.cells ? ws.cells() : [ws.active]).forEach(hook);
    ws.on("cell:created", (e) => setTimeout(() => hook(cellOf(e && (e.id || e))), 0));
  }

  window.TradeXAutoFit = { mount, fit: (style) => ws && fit(ws.active.id, style || styleOf(chartOf(ws.active))) };
})();
