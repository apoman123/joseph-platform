// Order-flow CHART TYPES in Vela's candle-style menu (2026-11-12), the way TradingView offers them:
//   "Volume footprint"        buy x sell (or delta / total / profile) at each price inside every candle, from real trades
//   "Session volume profile"  candles + one volume profile per session (drawing shared with orderflow_profile.js)
// Pick one in the style menu to turn it on, pick Candles to turn it off. Settings: chart settings, its own tab.
(function () {
  if (!window.VelaLab || !VelaLab.registerChartType || !VelaLab.registerRendererLayer) return;
  const TF = { "1": "M1", "5": "M5", "15": "M15", "30": "M30", "60": "H1", "240": "H4", "1D": "D1", D: "D1" };
  const TF_SEC = { M1: 60, M5: 300, M15: 900, M30: 1800, H1: 3600, H4: 14400, D1: 86400 };
  const T = window.HubTheme || { up: "#26a69a", down: "#ef5350", accent: "#f0b90b", text: "#d1d4dc", dim: "#787b86" };
  const SRC = { "Auto": "auto", "Real (Binance trades)": "real", "Estimated (chart candles)": "estimated" };
  const rgba = (hex, a) => { const h = String(hex || "#888").replace("#", ""); const n = parseInt(h.length === 3 ? h.split("").map((c) => c + c).join("") : h.slice(0, 6), 16); return `rgba(${n >> 16 & 255},${n >> 8 & 255},${n & 255},${a})`; };
  const fmt = (v) => { const a = Math.abs(v); return a >= 1e6 ? (v / 1e6).toFixed(2) + "M" : a >= 1e4 ? (v / 1e3).toFixed(1) + "K" : a >= 100 ? Math.round(v) + "" : a >= 10 ? v.toFixed(1) : v.toFixed(2); };
  const ticker = (h) => String(h.symbol).replace(/^[a-z]+:/i, "");
  const feedOf = (t) => (window.srcOf ? srcOf(t) : "arm");

  // ================= Volume footprint =================
  const FP = "volume_footprint";
  const FP_TYPES = ["Buy and Sell", "Delta", "Total", "Profile"];
  const fpSettings = {
    title: "Volume footprint", layout: "grouped",
    rows: [
      { kind: "heading", label: "Footprint" },
      { kind: "select", key: "type", label: "Type", options: FP_TYPES, defval: FP_TYPES[0] },
      { kind: "select", key: "rowMode", label: "Row size", options: ["Auto", "Manual"], defval: "Auto" },
      { kind: "number", key: "rowSize", label: "Price per row", defval: 1, min: 0, step: 0.00001, when: { key: "rowMode", equals: "Manual" } },
      { kind: "toggle", key: "numbers", label: "Show numbers", defval: true },
      { kind: "toggle", key: "poc", label: "Point of control (POC)", defval: true, colors: [{ key: "pocColor", label: "POC", defval: T.accent || "#f0b90b" }] },
      { kind: "toggle", key: "summary", label: "Delta / Total box under candle", defval: true },
      { kind: "heading", label: "Imbalance" },
      { kind: "toggle", key: "imb", label: "Highlight imbalances", defval: true, number: { key: "imbRatio", label: "Ratio %", defval: 300, min: 110, max: 2000, step: 10 } },
      { kind: "number", key: "imbMin", label: "Ignore rows below volume", defval: 0, min: 0, step: 0.1 },
      { kind: "toggle", key: "stacked", label: "Stacked imbalance zones", defval: true, number: { key: "stackN", label: "Levels in a row", defval: 3, min: 2, max: 10 } },
      { kind: "heading", label: "Colours" },
      { kind: "row", label: "Buy / Sell", controls: [{ kind: "color", key: "buyColor", label: "Buy", defval: T.up || "#26a69a" }, { kind: "color", key: "sellColor", label: "Sell", defval: T.down || "#ef5350" }] },
      { kind: "heading", label: "Data" },
      { kind: "select", key: "source", label: "Data", options: Object.keys(SRC), defval: "Auto" },
      { kind: "toggle", key: "fill", label: "Estimate candles whose trades are still loading (dimmed)", defval: true },
    ],
  };
  const fpDefaults = {}; fpSettings.rows.forEach((r) => { if (r.key) fpDefaults[r.key] = r.defval; if (r.number) fpDefaults[r.number.key] = r.number.defval; (r.colors || []).forEach((c) => (fpDefaults[c.key] = c.defval)); (r.controls || []).forEach((c) => c.key && (fpDefaults[c.key] = c.defval)); });

  class FootprintEngine {
    start(host) { this.h = host; this.map = new Map(); this.s = this.s || { ...fpDefaults }; this.busy = false; this.stopped = false; this.live = 0; this.loaded = false; this.push(); this.loadVisible(); }
    url(a, b) { const t = ticker(this.h), s = this.s; return `/api/orderflow/footprint?symbol=${encodeURIComponent(t)}&tf=${TF[this.h.timeframe] || "M5"}&feed=${feedOf(t)}&from=${a}&to=${b}&source=${SRC[s.source] || "auto"}&row_size=${s.rowMode === "Manual" ? s.rowSize : (this.row || 0)}&fill=${s.fill === false ? 0 : 1}`; }
    async fetch(a, b) {
      if (this.busy || this.stopped) { this.again = [a, b]; return; }
      this.busy = true;
      try {
        const r = await fetch(this.url(a, b)); const j = await r.json(); if (!r.ok) throw new Error(j.error || r.status);
        if (!this.row && this.s.rowMode !== "Manual") this.row = j.row_size;  // keep one auto row size per chart (stable rows)
        this.info = j; this.err = null;
        // a full reload drops candles the server no longer estimates (fill switched off); real ones replace estimates
        if (a <= this.firstAsked) { for (const k of [...this.map.keys()]) if (k >= a * 1000 && k <= b * 1000) this.map.delete(k); this.pending = !!j.pending; }
        for (const c of j.candles) this.map.set(c.t * 1000, c);
        this.covered = j.covered_from;
      } catch (e) { this.err = String(e.message || e); }
      this.busy = false; this.push();
      if (this.again) { const [x, y] = this.again; this.again = null; this.fetch(x, y); }
    }
    loadVisible() {
      const bars = this.h.bars(); if (!bars.length) return;
      const sec = TF_SEC[TF[this.h.timeframe] || "M5"], n = bars.length;
      const r = this.range || { from: bars[Math.max(0, n - 120)].time, to: bars[n - 1].time };
      const a = Math.floor(Math.max(r.from, bars[Math.max(0, n - 400)].time) / 1000), b = Math.floor(Math.min(r.to, bars[n - 1].time) / 1000);
      this.loaded = true; this.firstAsked = a - (a % sec); this.fetch(a - (a % sec), b);
    }
    push() { if (!this.stopped) this.h.pushData({ pending: this.pending, candles: this.map, row: this.info ? this.info.row_size : this.row, info: this.info, err: this.err, settings: this.s, sec: TF_SEC[TF[this.h.timeframe] || "M5"] }); }
    onViewport(r) { this.range = r; clearTimeout(this.vt); this.vt = setTimeout(() => this.loadVisible(), 400); }
    onBars() {
      const bars = this.h.bars(); if (!bars.length) return;
      if (!this.loaded && !this.busy) { this.loadVisible(); return; }   // style picked before the candles arrived
      const now = Date.now();
      // live: re-read the last two candles every 3 s; while trades are still loading (server says pending), re-read
      // the view every 5 s so estimated candles turn real as soon as their trades arrive
      if (now - this.live > 3000) { this.live = now; const sec = TF_SEC[TF[this.h.timeframe] || "M5"]; const b = Math.floor(bars[bars.length - 1].time / 1000); this.fetch(b - sec, b); }
      if (this.pending && now - (this.fillAt || 0) > 5000) { this.fillAt = now; this.loadVisible(); }
    }
    onSettings(v) {
      const old = this.s || fpDefaults; this.s = { ...fpDefaults, ...v };
      if (!this.h) return;
      if (old.source !== this.s.source || old.fill !== this.s.fill || old.rowMode !== this.s.rowMode || old.rowSize !== this.s.rowSize) { this.map = new Map(); this.row = 0; this.loadVisible(); } else this.push();
    }
    suspend() { this.stopped = true; } resume() { this.stopped = false; this.loadVisible(); } stop() { this.stopped = true; this.map = new Map(); }
  }

  window.OFFootprint = { FP, FP_TYPES, fpSettings, fpDefaults, FootprintEngine, rgba, fmt, SRC, TF, TF_SEC, ticker, feedOf };
})();
