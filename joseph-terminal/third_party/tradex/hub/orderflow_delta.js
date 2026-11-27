// Order Flow Delta: a Vela native indicator for every chart symbol (step 1 of the order-flow build, 2026-11-12).
// Numbers come from the hub server /api/orderflow/delta (orderflow_api.py), so the chart, agents and scripts share one engine.
// Delta candle rules = TradingView Volume Delta / CVD: open 0 (CVD: previous close), high/low = running extremes inside the bar, close = final.
(function () {
  if (!window.VelaLab || !VelaLab.registerNativeIndicator) return;
  const TF = { "1": "M1", "5": "M5", "15": "M15", "30": "M30", "60": "H1", "240": "H4", "1D": "D1", D: "D1" };
  const VIEWS = ["Delta candles", "Cumulative delta (CVD) candles", "Delta columns"];
  const ANCHORS = ["Day (UTC)", "Week (UTC)", "None"];
  const SOURCES = ["Auto", "Real buyers/sellers (Binance)", "Estimated from chart candles"];
  const SRC_PARAM = { [SOURCES[0]]: "auto", [SOURCES[1]]: "real", [SOURCES[2]]: "estimated" };
  const theme = () => (window.HubTheme || { up: "#26a69a", down: "#ef5350", dim: "#787b86" });

  const schema = () => [
    { key: "view", title: "Show as", type: "string", defval: VIEWS[0], options: VIEWS,
      tooltip: "Delta = buyers minus sellers inside each candle. CVD keeps adding it up, so you see who has been in control." },
    { key: "anchor", title: "CVD restarts every", type: "string", defval: ANCHORS[0], options: ANCHORS, when: { key: "view", equals: VIEWS[1] } },
    { key: "source", title: "Data", type: "string", defval: SOURCES[0], options: SOURCES,
      tooltip: "Auto = real Binance buyer/seller data when that market trades there (crypto, gold, silver, copper...), otherwise an estimate from this chart's candles (forex)." },
    { key: "upColor", title: "Buyers stronger", type: "color", defval: "", group: "Colours" },
    { key: "downColor", title: "Sellers stronger", type: "color", defval: "", group: "Colours" },
  ];
  const defaults = () => Object.fromEntries(schema().map((s) => [s.key, s.defval]));

  function anchorKey(tMs, anchor) {
    if (anchor === ANCHORS[2]) return 0;
    const d = Math.floor(tMs / 86400000);
    return anchor === ANCHORS[1] ? Math.floor((d + 3) / 7) : d;  // weeks start Monday (1970-01-01 was a Thursday)
  }

  class OrderFlowDelta {
    start(ctx, inputs) {
      this.ctx = ctx; this.inputs = { ...defaults(), ...inputs }; this.rows = new Map(); this.info = null;
      this.ticker = String(ctx.symbol).replace(/^[a-z]+:/i, ""); this.tf = TF[ctx.timeframe] || (window.LabTF ? LabTF.fromAny(ctx.timeframe) : "M5");
      this.feed = window.srcOf ? srcOf(this.ticker) : "arm"; this.busy = false; this.lastLive = 0; this.stopped = false;
      this.load(true);
    }
    url(from, to) {
      return `/api/orderflow/delta?symbol=${encodeURIComponent(this.ticker)}&tf=${this.tf}&feed=${this.feed}&from=${from}&to=${to}&source=${SRC_PARAM[this.inputs.source] || "auto"}`;
    }
    async load(full) {
      const bars = this.ctx.bars();
      if (this.busy) { this.pendingFull = this.pendingFull || full; return; }  // a settings change mid-request runs right after it
      if (!bars.length || this.stopped) return;
      const first = Math.floor(bars[0].time / 1000), last = Math.floor(bars[bars.length - 1].time / 1000);
      const have = [...this.rows.keys()];
      if (!full && !this.rows.size) {                         // nothing loaded yet (or last load failed): full retry at most once a minute
        if (Date.now() - (this.lastFull || 0) < 60000) return;
        full = true;
      }
      let from = first;
      if (full) { this.askedFrom = first; this.lastFull = Date.now(); }
      if (!full && have.length) from = Math.max(first, Math.max(...have) / 1000 - 120);  // live: re-read only the newest bars
      this.busy = true; this.ctx.setStatus("loading");
      try {
        const r = await fetch(this.url(from, last));
        const j = await r.json();
        if (!r.ok) throw new Error(j.error || r.status);
        this.info = j; this.error = null;
        for (const x of j.rows) this.rows.set(x.t * 1000, x);
      } catch (e) { this.error = String(e.message || e); }
      this.busy = false;
      if (this.pendingFull) { this.pendingFull = false; return this.load(true); }
      if (!this.stopped) { this.ctx.setStatus(this.ctx.live ? "live" : "idle"); this.emit(); }
    }
    emit() {
      const bars = this.ctx.bars(), t = theme(), view = this.inputs.view;
      const up = this.inputs.upColor || t.up, down = this.inputs.downColor || t.down;
      const src = this.info ? (this.info.source === "real" ? `real, Binance ${this.info.real_symbol}` : "estimated") : (this.error ? "no data" : "loading");
      const title = view === VIEWS[1] ? "CVD" : "Delta";
      const base = { id: "of-delta-" + view.length, title, displayTitle: `${title} (${src})`, paneId: "" };
      // Corner tag: always say where the numbers come from (real exchange data or an estimate).
      const tip = this.info ? `${this.info.method}; unit: ${this.info.unit}; inside each bar: ${this.info.intrabar}` : (this.error || "");
      const tables = [{ id: "of-delta-src", paneId: "", position: "top_right", columns: 1, rows: 1, frameWidth: 0, borderWidth: 0, merges: [],
        cells: [[{ text: this.error && !this.info ? "no order-flow data: " + this.error.slice(0, 60) : (this.info && this.info.source === "real" ? `REAL · Binance ${this.info.real_symbol}` : this.info ? "ESTIMATED" : "loading..."),
          textColor: this.info && this.info.source === "real" ? up : (t.dim || "#787b86"), hAlign: "right", vAlign: "top", textSize: "small", fontFamily: "default", bold: false, italic: false, tooltip: tip }]] }];
      if (view === VIEWS[2]) {
        const points = bars.map((b) => { const r = this.rows.get(b.time); return { time: b.time, value: r ? r.delta : null, color: r && r.delta < 0 ? down : up }; });
        return this.ctx.emit({ series: [{ ...base, kind: "columns", points, style: { color: up, width: 1, lineStyle: "solid", base: 0 } }], tables });
      }
      let run = 0, key = null;
      const out = bars.map((b) => {
        const r = this.rows.get(b.time);
        if (!r) return { time: b.time, open: NaN, high: NaN, low: NaN, close: NaN };
        let o = 0;
        if (view === VIEWS[1]) { const k = anchorKey(b.time, this.inputs.anchor); if (k !== key) { key = k; run = 0; } o = run; run += r.delta; }
        return { time: b.time, open: o, high: o + r.hi, low: o + r.lo, close: o + r.delta, volume: r.buy + r.sell };
      });
      this.ctx.emit({
        series: [{ ...base, kind: "candle", bars: out, style: { up, down, wickUp: up, wickDown: down, borderUp: up, borderDown: down } }],
        priceLines: [{ id: "of-delta-zero", paneId: "", price: 0, color: t.dim || "#787b86", lineStyle: "dotted", width: 1 }],
        tables,
      });
    }
    onBars() {
      const bars = this.ctx.bars();
      if (!bars.length) return;
      // Older history landed (user scrolled back): one full reload, compared with what we last ASKED for, never with what
      // came back (a capped or failed answer would otherwise reload on every tick and flood the remote MT5 bridge; seen 2026-11-12).
      if (this.askedFrom != null && bars[0].time / 1000 < this.askedFrom && Date.now() - (this.lastFull || 0) > 30000) return this.load(true);
      this.emit();
      if (Date.now() - this.lastLive > 5000) { this.lastLive = Date.now(); this.load(false); }  // forming candle: at most every 5 s
    }
    onViewport() {}
    setInputs(inputs) {
      const srcChanged = inputs.source !== undefined && inputs.source !== this.inputs.source;
      this.inputs = { ...this.inputs, ...inputs };
      if (srcChanged) { this.rows.clear(); this.info = null; this.load(true); } else this.emit();
    }
    suspend() { this.stopped = true; }
    resume() { this.stopped = false; this.load(true); }
    stop() { this.stopped = true; this.rows.clear(); }
  }

  VelaLab.registerNativeIndicator({
    type: "orderflow_delta", title: "Order Flow Delta (delta candles, CVD)", shortTitle: "Delta",
    paneHint: "new", overlay: false, multiInstance: true,
    inputsSchema: schema, defaultInputs: defaults, create: () => new OrderFlowDelta(),
  });
})();
