// TradeX web beta: order flow computed in the browser (port of hub/orderflow_api.py; keep the two in step).
// Real buyer/seller data = Binance USD-M futures klines (field 9 = taker buy volume), fetched by the visitor's browser.
// Other symbols: estimated from the chart feed's own lower candles (TradingView's default rule).
// Footprint: the web has no trade-by-trade store, so every candle uses Binance 1-minute buy/sell totals spread over
// that minute's range (the desktop's "est" fill) and is flagged est.
(function () {
  "use strict";
  const F = window.TXWebFeeds, SEC = { ...F.SEC, S1: 1 };   // S1 = Binance spot 1-second candles (footprint only)
  const INTRABAR = { M1: "M1", M5: "M1", M15: "M1", M30: "M5", H1: "M5", H4: "M15", D1: "H1" };
  const BTF = { S1: "1s", M1: "1m", M5: "5m", M15: "15m", M30: "30m", H1: "1h", H4: "4h", D1: "1d" };
  const MAX_ROWS = 20000, FAPI = "https://fapi.binance.com/fapi/v1", SPOT = "https://data-api.binance.vision/api/v3";
  const RENAME = { XCU: "COPPER", XNG: "NATGAS", XBT: "BTC", XDG: "DOGE" };
  // Binance spot has no gold pair; PAXG (a gold-backed coin) stands in for gold's 1-second price path. Price gaps are
  // removed by the same shift used for every Binance series (median gap to the chart's own candles).
  const SPOT_PROXY = { XAUUSDT: "PAXGUSDT" };
  let futP = null;
  const futures = () => futP || (futP = fetch(FAPI + "/exchangeInfo").then((r) => r.json())
    .then((d) => new Set(d.symbols.filter((s) => s.status === "TRADING").map((s) => s.symbol))).catch(() => { futP = null; return new Set(); }));

  async function realSymbol(ticker) {
    let t = String(ticker).toUpperCase().split(":").pop();
    for (const p of ["BINF_", "BYBIT_S_", "BYBIT_I_", "BYBIT_", "FRX", "CRY", "CB_", "KRK_", "OKX_"]) if (t.startsWith(p)) { t = t.slice(p.length); break; }
    if (/\.(MARK|INDEX)$/.test(t)) return null;
    t = t.replace(/-/g, "");
    let base = t.endsWith("USDT") ? t.slice(0, -4) : t.endsWith("USD") ? t.slice(0, -3) : null;
    if (!base) return null;
    const s = (RENAME[base] || base) + "USDT", fut = await futures();
    if (fut.has(s)) return s;
    // Binance spot klines carry the same taker-buy volume and come from the public data host (reachable where
    // Binance futures is blocked, e.g. the US). Spot pairs = the Binance spot tickers in the symbol list.
    const spot = (window.LabSymbols || []).some((x) => x.source === "binance" && !x.ticker.startsWith("BINF_") && x.ticker.toUpperCase() === base + "USD");
    return spot ? "SPOT:" + s : null;
  }
  const futuresBlocked = async () => (await futures()).size === 0;

  async function binanceRows(sym, itf, a, b) {
    const out = new Map(), step = SEC[itf] * 1000, spot = sym.startsWith("SPOT:"), api = spot ? SPOT : FAPI, lim = spot ? 1000 : 1500;
    if (spot) sym = sym.slice(5);
    let start = a * 1000;
    for (let i = 0; i < 20 && start <= b * 1000; i++) {
      const r = await fetch(`${api}/klines?symbol=${sym}&interval=${BTF[itf]}&startTime=${start}&endTime=${b * 1000}&limit=${lim}`);
      if (!r.ok) throw new Error(`Binance ${spot ? "spot" : "futures"}: HTTP ${r.status}${r.status === 451 ? " (not available in your country)" : ""}`);
      const rows = await r.json();
      if (!rows.length) break;
      for (const k of rows) { const vol = +k[5], tb = +k[9]; out.set(k[0] / 1000, [tb, vol - tb, 0, +k[2], +k[3], +k[4]]); }
      start = rows[rows.length - 1][0] + step;
      if (rows.length < lim) break;
    }
    return out;
  }

  async function estimatedRows(feed, ticker, itf, a, b) {
    const hist = F.feeds[feed];
    if (!hist) throw new Error(`${feed}: no history in the web version`);
    const need = Math.floor((b - a) / SEC[itf]) + 3;
    const bars = await hist.history(ticker, itf, Math.max(3, Math.min(5000, need)), (b + SEC[itf]) * 1000);
    const out = new Map(); let prev = null, sign = 0;
    for (const x of bars.sort((p, q) => p.time - q.time)) {
      const o = x.open, c = x.close, v = +x.volume || 0;
      if (c > o) sign = 1; else if (c < o) sign = -1; else if (prev !== null && c !== prev) sign = c > prev ? 1 : -1;
      prev = c;
      if (x.time >= a && x.time <= b) out.set(x.time, [sign > 0 ? v : 0, sign < 0 ? v : 0, sign, x.high, x.low, c]);
    }
    return out;
  }

  // Cache closed lower bars per (source, ticker, timeframe); the newest one is always re-read (it may still be forming).
  const cache = new Map();
  async function rows(src, feed, ticker, itf, a, b) {
    const key = src + "|" + ticker + "|" + itf, have = cache.get(key) || new Map(); cache.set(key, have);
    const ts = [...have.keys()].sort((x, y) => x - y), sec = SEC[itf], need = [];
    if (!ts.length || a < ts[0]) need.push([a, ts.length && ts[0] - sec < b ? ts[0] - sec : b]);
    if (ts.length && b > ts[ts.length - 1] - sec) need.push([Math.max(a, ts[ts.length - 1]), b]);
    for (const [lo, hi] of need) {
      if (hi < lo) continue;
      // 1-second candles exist on Binance spot only: same coin, spot market.
      const sym = src === "real" ? await realSymbol(ticker) : null;
      const spotSym = () => { const b = String(sym).replace(/^SPOT:/, ""); return "SPOT:" + (SPOT_PROXY[b] || b); };
      const got = src === "real" ? await binanceRows(itf === "S1" ? spotSym() : sym, itf, lo, hi) : await estimatedRows(feed, ticker, itf, lo, hi);
      for (const [t, v] of got) have.set(t, v);
    }
    return new Map([...have].filter(([t]) => t >= a && t <= b).sort((x, y) => x[0] - y[0]));
  }
  const needVolume = async (m) => {
    if (m.size && ![...m.values()].some((v) => v[0] || v[1]))
      throw new Error(await futuresBlocked() ? "real buyer/seller data comes from Binance futures, which is not reachable from your country or network; this feed sends no volume" : "this feed sends no volume and has no Binance match, so there is no order flow to show");
    return m;
  };

  function shift(lower, chart, sec, isec) {   // chart price - Binance price, median of the last 30 chart candles (past only)
    const pts = [];
    for (const [t, v] of chart) { const k = t + sec - isec; if (lower.has(k)) pts.push([k, v[5] - lower.get(k)[5]]); }
    const out = new Map();
    if (!pts.length) { for (const t of lower.keys()) out.set(t, 0); return out; }
    const keys = [], meds = [], win = [];
    for (const [k, g] of pts) { win.push(g); if (win.length > 30) win.shift(); const s = [...win].sort((x, y) => x - y); keys.push(k); meds.push(s[Math.floor(s.length / 2)]); }
    for (const t of lower.keys()) { let lo = 0, hi = keys.length; while (lo < hi) { const m = (lo + hi) >> 1; if (keys[m] <= t) lo = m + 1; else hi = m; } out.set(t, meds[Math.max(lo - 1, 0)]); }
    return out;
  }
  async function pick(ticker, source) {
    const real = await realSymbol(ticker);
    if (source === "real") return real ? "real" : null;
    if (source === "estimated") return "estimated";
    return real ? "real" : "estimated";
  }
  const unitOf = (src) => src === "real" ? "base volume (coin / oz)" : "feed volume";
  const methodOf = (src) => src === "real" ? "Binance taker buy volume (futures, or spot where futures is blocked)" : "lower-bar direction (TradingView default)";

  async function delta(q) {
    const ticker = q.get("symbol"), tf = q.get("tf") || "M5", feed = q.get("feed") || "binance", now = Math.floor(Date.now() / 1000);
    const src = await pick(ticker, q.get("source") || "auto");
    if (!src) throw new Error(`no real buyer/seller data for ${ticker}`);
    const sec = SEC[tf], itf = INTRABAR[tf], isec = SEC[itf], end = +(q.get("to") || now);
    let start = Math.max(+(q.get("from") || now - 86400), end - MAX_ROWS * isec);
    if (start % sec && tf !== "D1") start += sec - start % sec;
    const lower = await rows(src, feed, ticker, itf, start, end + sec - isec);
    if (src === "estimated") await needVolume(lower);
    const out = []; let cur = null;
    for (const [t, v] of lower) {
      const bt = tf !== "D1" ? t - t % sec : t - t % 86400;
      if (!cur || cur.t !== bt) { cur = { t: bt, buy: 0, sell: 0, delta: 0, hi: 0, lo: 0, n: 0 }; out.push(cur); }
      cur.buy += v[0]; cur.sell += v[1]; cur.delta += v[0] - v[1];
      cur.hi = Math.max(cur.hi, cur.delta); cur.lo = Math.min(cur.lo, cur.delta); cur.n++;
    }
    return { rows: out, source: src, real_symbol: await realSymbol(ticker), intrabar: itf, unit: unitOf(src), method: methodOf(src) };
  }

  function sessionOf(t, mode) {
    const day = t - t % 86400;
    if (mode === "day") return [day, "Day"];
    if (mode === "week") return [day - ((Math.floor(day / 86400) + 3) % 7) * 86400, "Week"];
    const h = Math.floor((t % 86400) / 3600);
    return h >= 22 ? [day + 22 * 3600, "Asia"] : h < 7 ? [day - 2 * 3600, "Asia"] : h < 13 ? [day + 7 * 3600, "London"] : [day + 13 * 3600, "New York"];
  }
  function valueArea(tot, poc, pct) {
    const total = tot.reduce((x, y) => x + y, 0); let lo = poc, hi = poc, inside = tot[poc];
    while (total && inside < pct * total && (lo > 0 || hi < tot.length - 1)) {
      const up = hi < tot.length - 1 ? tot[hi + 1] : -1, dn = lo > 0 ? tot[lo - 1] : -1;
      if (up >= dn) { hi++; inside += up; } else { lo--; inside += dn; }
    }
    return [lo, hi];
  }
  function histogram(bars, nRows, rowSize) {   // bars: [buy, sell, high, low]
    let lo = Math.min(...bars.map((b) => b[3])); const hi = Math.max(...bars.map((b) => b[2]));
    const step = rowSize > 0 ? rowSize : Math.max((hi - lo) / nRows, 1e-12);
    if (rowSize > 0) lo -= lo % step;
    const n = rowSize > 0 ? Math.max(1, Math.floor((hi - lo) / step) + 1) : nRows, buy = new Array(n).fill(0), sell = new Array(n).fill(0);
    for (const [b_, s_, h, l] of bars) {
      const i0 = Math.min(n - 1, Math.max(0, Math.floor((l - lo) / step))), i1 = Math.min(n - 1, Math.max(0, Math.floor((h - lo) / step)));
      if (i1 === i0 || h <= l) { buy[i0] += b_; sell[i0] += s_; continue; }
      for (let i = i0; i <= i1; i++) { const w = (Math.min(h, lo + (i + 1) * step) - Math.max(l, lo + i * step)) / (h - l); if (w > 0) { buy[i] += b_ * w; sell[i] += s_ * w; } }
    }
    return [lo, step, buy, sell];
  }
  function levels(lo, step, buy, sell, va) {
    const tot = buy.map((x, i) => x + sell[i]); let poc = 0; tot.forEach((x, i) => { if (x > tot[poc]) poc = i; });
    const [a, b] = valueArea(tot, poc, va);
    return [poc, lo + (poc + 0.5) * step, lo + (b + 1) * step, lo + a * step];
  }
  const r4 = (x) => Math.round(x * 1e4) / 1e4, r6 = (x) => Math.round(x * 1e6) / 1e6;

  async function realOnChart(src, feed, ticker, tf, itf, start, endI, end) {
    const sec = SEC[tf], isec = SEC[itf];
    if (src === "estimated") return [await needVolume(await rows("estimated", feed, ticker, itf, start, endI)), null];
    const lower = await rows("real", feed, ticker, itf, start, endI);
    const g = shift(lower, await rows("estimated", feed, ticker, tf, start - 30 * sec, end), sec, isec), out = new Map();
    for (const [t, v] of lower) if (g.has(t)) { const d = g.get(t); out.set(t, [v[0], v[1], v[2], v[3] + d, v[4] + d, v[5] + d]); }
    return [out, "Binance prices shifted onto the chart price (median gap of the last 30 chart candles)"];
  }

  async function profile(q) {
    const ticker = q.get("symbol"), tf = q.get("tf") || "M5", feed = q.get("feed") || "binance", now = Math.floor(Date.now() / 1000);
    const src = await pick(ticker, q.get("source") || "auto");
    if (!src) throw new Error(`no real buyer/seller data for ${ticker}`);
    let mode = q.get("mode") || "sessions";
    if (tf === "H4" && mode === "sessions") mode = "day"; else if (tf === "D1" && (mode === "sessions" || mode === "day")) mode = "week";
    const nRows = +(q.get("rows") || 24), rowSize = +(q.get("row_size") || 0), va = +(q.get("va") || 70) / 100, developing = (q.get("developing") || "1") === "1";
    const sec = SEC[tf], itf = INTRABAR[tf], isec = SEC[itf], end = +(q.get("to") || now);
    let start = Math.max(+(q.get("from") || now - 86400), end - MAX_ROWS * isec);
    const s0 = sessionOf(start, mode)[0];
    if (s0 < start) { let t = start - start % 3600; while (sessionOf(t, mode)[0] === s0) t += 3600; start = t; }
    const [lower, shiftNote] = await realOnChart(src, feed, ticker, tf, itf, start, end + sec - isec, end);
    const groups = new Map();
    for (const t of lower.keys()) { const [s, name] = sessionOf(t, mode), k = s + "|" + name; if (!groups.has(k)) groups.set(k, { s, name, ts: [] }); groups.get(k).ts.push(t); }
    const out = [];
    for (const { s, name, ts } of [...groups.values()].sort((x, y) => x.s - y.s)) {
      const bars = ts.map((t) => { const v = lower.get(t); return [v[0], v[1], v[3], v[4]]; });
      const [lo, step, buy, sell] = histogram(bars, nRows, rowSize), [pocI, poc, vah, val] = levels(lo, step, buy, sell, va), dev = [];
      if (developing) ts.forEach((t, k) => {
        if (k === ts.length - 1 || (ts[k + 1] - ts[k + 1] % sec) !== (t - t % sec)) {
          const [l2, st2, b2, s2] = histogram(bars.slice(0, k + 1), nRows, rowSize), [, p2, h2, v2] = levels(l2, st2, b2, s2, va);
          dev.push([tf !== "D1" ? t - t % sec : t - t % 86400, r6(p2), r6(h2), r6(v2)]);
        }
      });
      const sb = buy.reduce((x, y) => x + y, 0), ss = sell.reduce((x, y) => x + y, 0);
      out.push({ start: s, name, first: ts[0], last: ts[ts.length - 1] + isec, lo, step, buy: buy.map(r4), sell: sell.map(r4), poc_i: pocI, poc, vah, val, total: r4(sb + ss), delta: r4(sb - ss), dev });
    }
    return { sessions: out, source: src, real_symbol: await realSymbol(ticker), intrabar: itf, unit: unitOf(src), mode, va_pct: va,
      method: methodOf(src) + "; each lower bar's volume spread evenly over its high-low range", shift: shiftNote };
  }

  function niceStep(x) { if (x <= 0) return 1; const e = 10 ** Math.floor(Math.log10(x)); for (const m of [1, 2, 2.5, 5, 10]) if (x <= m * e) return m * e; return 10 * e; }

  async function footprint(q) {
    const ticker = q.get("symbol"), tf = q.get("tf") || "M5", feed = q.get("feed") || "binance", now = Math.floor(Date.now() / 1000);
    const src = await pick(ticker, q.get("source") || "auto");
    if (!src) throw new Error(`no real buyer/seller data for ${ticker}`);
    const sec = SEC[tf], start = +(q.get("from") || now - 3 * 3600), end = +(q.get("to") || now);
    const bucket = (x) => tf !== "D1" ? x - x % sec : x - x % 86400;
    let rowSize = +(q.get("row_size") || 0);
    if (rowSize <= 0) {
      const n = Math.min(1000, Math.max(5, Math.floor((end - start) / sec) + 5));
      const cb = (await F.feeds[feed].history(ticker, tf, n, end * 1000)).filter((x) => x.time >= start && x.time <= end);
      const rng = cb.map((x) => x.high - x.low).filter((x) => x > 0).sort((a, b) => a - b);
      rowSize = rng.length ? niceStep(rng[Math.floor(rng.length / 2)] / 7) : 1;
    }
    // Bug fixed 2026-11-25: each minute's buy/sell totals used to be spread EVENLY over the minute's range, so every row
    // that minute touched got the same numbers (on a 1m chart: every row of a candle identical). Now the totals stay the
    // futures minute totals (so footprint delta/total match the delta panel), and the 1-second price path of Binance
    // spot (gold: PAXG) decides WHERE in the range they go: each second weighs its row by its volume plus a little for
    // time spent there, buyers and sellers by that second's own split. 25% stays an even spread across the minute's range
    // (the spot path is thinner than futures). Windows over 5 hours or no 1-second data = the old even spread.
    const [m1] = await realOnChart(src, feed, ticker, tf, "M1", start, end + sec - 60, end), candles = new Map();
    let s1 = null, shaped = 0;
    if (src === "real" && end - start <= 5 * 3600) { try { [s1] = await realOnChart(src, feed, ticker, tf, "S1", start, end + sec - 1, end); } catch (_) { s1 = null; } }
    const EVEN = 0.25;
    // Base shape inside one minute (when the 1-second path says nothing about a row): a soft tent peaking near where
    // the minute spent its end (high, low and twice the close), never below 15% of the peak. A flat even spread made
    // runs of identical numbers.
    const tent = (r0, r1, v) => {
      const w = new Map(), n = r1 - r0 + 1; if (n === 1) { w.set(r0, 1); return w; }
      const c = (v[3] + v[4] + 2 * v[5]) / 4, half = Math.max(rowSize, (v[3] - v[4]) / 2); let t = 0;
      for (let r = r0; r <= r1; r++) { const x = Math.max(0.15, 1 - Math.abs((r + 0.5) * rowSize - c) / (2 * half)); w.set(r, x); t += x; }
      for (const [r, x] of w) w.set(r, x / t); return w;
    };
    for (const [m, v] of m1) {
      const bt = bucket(m); if (bt < start || bt > end) continue;
      const r0 = Math.floor(v[4] / rowSize + 1e-9), r1 = Math.floor(v[3] / rowSize + 1e-9), n = r1 - r0 + 1;
      const wb = new Map(), ws = new Map();
      let secs = [];
      if (s1) for (let k = 0; k < 60; k++) { const x = s1.get(m + k); if (x) secs.push(x); }
      if (secs.length >= 30) {
        shaped++;
        const vt = secs.reduce((a, x) => a + x[0] + x[1], 0), eps = vt / secs.length * 0.1 + 1e-12, mShare = v[0] + v[1] > 0 ? v[0] / (v[0] + v[1]) : 0.5;
        const pb = new Map(), ps = new Map(); let sb = 0, ss = 0;
        for (const x of secs) {
          const r = Math.min(r1, Math.max(r0, Math.floor(x[5] / rowSize + 1e-9))), vol = x[0] + x[1], share = vol > 0 ? x[0] / vol : mShare, w = vol + eps;
          pb.set(r, (pb.get(r) || 0) + w * share); ps.set(r, (ps.get(r) || 0) + w * (1 - share)); sb += w * share; ss += w * (1 - share);
        }
        const base = tent(r0, r1, v);
        for (let r = r0; r <= r1; r++) {
          wb.set(r, EVEN * base.get(r) + (1 - EVEN) * (sb > 0 ? (pb.get(r) || 0) / sb : base.get(r)));
          ws.set(r, EVEN * base.get(r) + (1 - EVEN) * (ss > 0 ? (ps.get(r) || 0) / ss : base.get(r)));
        }
      } else { const base = tent(r0, r1, v); for (let r = r0; r <= r1; r++) { wb.set(r, base.get(r)); ws.set(r, base.get(r)); } }
      if (!candles.has(bt)) candles.set(bt, new Map());
      const c = candles.get(bt);
      for (let r = r0; r <= r1; r++) { const x = c.get(r) || [0, 0]; x[0] += v[0] * wb.get(r); x[1] += v[1] * ws.get(r); c.set(r, x); }
    }
    const fine = shaped > 0;
    const out = [];
    for (const bt of [...candles.keys()].sort((a, b) => a - b)) {
      const c = candles.get(bt), ks = [...c.keys()], lo = Math.min(...ks), hi = Math.max(...ks), buy = [], sell = [];
      for (let r = lo; r <= hi; r++) { const x = c.get(r) || [0, 0]; buy.push(r4(x[0])); sell.push(r4(x[1])); }
      out.push({ t: bt, r0: lo, buy, sell, est: src === "real" });
    }
    return { candles: out, source: src, real_symbol: await realSymbol(ticker), row_size: rowSize, unit: unitOf(src), covered_from: start, pending: false,
      est_candles: src === "real" ? out.length : 0,
      method: fine ? "Binance futures 1-minute buy/sell totals, placed along Binance spot's 1-second price path (web version: no trade-by-trade data)"
        : src === "real" ? "Binance 1-minute buy/sell totals spread over each minute's range (web version: no trade-by-trade data)" : "chart 1-minute candles spread over their range (coarse estimate)" };
  }

  window.TXWebOrderflow = { delta, profile, footprint, realSymbol };
})();
