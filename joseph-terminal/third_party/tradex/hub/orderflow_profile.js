// Session Volume Profile (step 2 of the order-flow build, 2026-11-12): a Vela native indicator + renderer layer.
// One histogram per session (Asia 22-07, London 07-13, New York 13-22 UTC, or one per UTC day), buy/sell split by price,
// POC, value area, VAH/VAL, developing POC/VA. Numbers: /api/orderflow/profile (orderflow_api.py), shared with agents.
// Settings follow TradingView's Session Volume Profile.
(function () {
  if (!window.VelaLab || !VelaLab.registerNativeIndicator || !VelaLab.registerRendererLayer) return;
  const TYPE = "orderflow_profile";
  const TF = { "1": "M1", "5": "M5", "15": "M15", "30": "M30", "60": "H1", "240": "H4", "1D": "D1", D: "D1" };
  const TF_SEC = { M1: 60, M5: 300, M15: 900, M30: 1800, H1: 3600, H4: 14400, D1: 86400 };
  const MODES = ["Asia / London / New York", "Whole day (UTC)"];
  const LAYOUTS = ["Number of rows", "Row size (price)"];
  const VOLS = ["Buy / Sell", "Total", "Delta"];
  const PLACES = ["Left", "Right"];
  const SOURCES = ["Auto", "Real buyers/sellers (Binance)", "Estimated from chart candles"];
  const SRC_PARAM = { [SOURCES[0]]: "auto", [SOURCES[1]]: "real", [SOURCES[2]]: "estimated" };
  const theme = () => (window.HubTheme || { up: "#26a69a", down: "#ef5350", dim: "#787b86", text: "#d1d4dc", accent: "#f0a030" });
  const rgba = (hex, a) => { const h = (hex || "#888888").replace("#", ""); const n = parseInt(h.length === 3 ? h.split("").map((c) => c + c).join("") : h.slice(0, 6), 16); return `rgba(${n >> 16 & 255},${n >> 8 & 255},${n & 255},${a})`; };

  const schema = () => [
    { key: "mode", title: "Profile per", type: "string", defval: MODES[0], options: MODES,
      tooltip: "Sessions are cut in UTC so they never overlap: Asia 22:00-07:00, London 07:00-13:00, New York 13:00-22:00. A 4-hour chart uses one profile per day, a daily chart one per week." },
    { key: "layout", title: "Row layout", type: "string", defval: LAYOUTS[0], options: LAYOUTS },
    { key: "rows", title: "Rows", type: "int", defval: 24, min: 4, max: 200, when: { key: "layout", equals: LAYOUTS[0] } },
    { key: "rowSize", title: "Row size (price)", type: "float", defval: 1, min: 0, step: 0.0001, when: { key: "layout", equals: LAYOUTS[1] } },
    { key: "volume", title: "Volume", type: "string", defval: VOLS[0], options: VOLS,
      tooltip: "Buy / Sell = who traded at each price. Total = all volume. Delta = buyers minus sellers at each price." },
    { key: "va", title: "Value area %", type: "int", defval: 70, min: 10, max: 100,
      tooltip: "The price zone that holds this share of the session's volume, grown outward from the POC (the busiest price)." },
    { key: "poc", title: "POC line", type: "bool", defval: true, group: "Lines" },
    { key: "vavl", title: "VAH / VAL lines", type: "bool", defval: true, group: "Lines" },
    { key: "extend", title: "Extend lines right (last session)", type: "bool", defval: false, group: "Lines" },
    { key: "devPoc", title: "Developing POC", type: "bool", defval: false, group: "Lines",
      tooltip: "Where the POC was at each candle while the session was still running (no look-ahead)." },
    { key: "devVa", title: "Developing VA", type: "bool", defval: false, group: "Lines" },
    { key: "width", title: "Width (% of session)", type: "int", defval: 60, min: 10, max: 100, group: "Look" },
    { key: "place", title: "Placement", type: "string", defval: PLACES[0], options: PLACES, group: "Look" },
    { key: "box", title: "Session box", type: "bool", defval: true, group: "Look" },
    { key: "source", title: "Data", type: "string", defval: SOURCES[0], options: SOURCES,
      tooltip: "Auto = real Binance buyer/seller volume when that market trades there, shifted onto this chart's price; else an estimate from this chart's candles." },
    { key: "upColor", title: "Buy volume", type: "color", defval: "", group: "Colours" },
    { key: "downColor", title: "Sell volume", type: "color", defval: "", group: "Colours" },
    { key: "pocColor", title: "POC", type: "color", defval: "", group: "Colours" },
  ];
  const defaults = () => Object.fromEntries(schema().map((s) => [s.key, s.defval]));

  class OrderFlowProfile {
    start(ctx, inputs) {
      this.ctx = ctx; this.inputs = { ...defaults(), ...inputs }; this.sess = new Map(); this.info = null; this.error = null;
      this.ticker = String(ctx.symbol).replace(/^[a-z]+:/i, ""); this.tf = TF[ctx.timeframe] || "M5";
      this.feed = window.srcOf ? srcOf(this.ticker) : "arm"; this.busy = false; this.lastLive = 0; this.stopped = false;
      this.load(true);
    }
    url(from, to) {
      const i = this.inputs;
      return `/api/orderflow/profile?symbol=${encodeURIComponent(this.ticker)}&tf=${this.tf}&feed=${this.feed}&from=${from}&to=${to}` +
        `&source=${SRC_PARAM[i.source] || "auto"}&mode=${i.mode === MODES[1] ? "day" : "sessions"}&rows=${i.rows}` +
        `&row_size=${i.layout === LAYOUTS[1] ? i.rowSize : 0}&va=${i.va}&developing=${i.devPoc || i.devVa ? 1 : 0}`;
    }
    async load(full) {
      const bars = this.ctx.bars();
      if (this.busy) { this.pendingFull = this.pendingFull || full; return; }  // a settings change mid-request runs right after it
      if (!bars.length || this.stopped) return;
      const first = Math.floor(bars[0].time / 1000), last = Math.floor(bars[bars.length - 1].time / 1000);
      if (!full && !this.sess.size) {                         // nothing loaded yet (or last load failed): full retry at most once a minute
        if (Date.now() - (this.lastFull || 0) < 60000) return;
        full = true;
      }
      let from = first;
      if (full) { this.askedFrom = first; this.lastFull = Date.now(); }
      if (!full && this.sess.size) from = Math.max(...this.sess.keys());  // live: only the running session
      this.busy = true; this.ctx.setStatus("loading");
      try {
        const r = await fetch(this.url(from, last)); const j = await r.json();
        if (!r.ok) throw new Error(j.error || r.status);
        if (full) this.sess.clear();
        this.info = j; this.error = null;
        for (const s of j.sessions) this.sess.set(s.start, s);
      } catch (e) { this.error = String(e.message || e); }
      this.busy = false;
      if (this.pendingFull) { this.pendingFull = false; return this.load(true); }
      if (!this.stopped) { this.ctx.setStatus(this.ctx.live ? "live" : "idle"); this.emit(); }
    }
    emit() {
      const t = theme(), i = this.inputs, bars = this.ctx.bars();
      const colors = { up: i.upColor || t.up, down: i.downColor || t.down, poc: i.pocColor || t.accent || "#f0a030", dim: t.dim || "#787b86", text: t.text || "#d1d4dc" };
      const sessions = [...this.sess.values()].sort((a, b) => a.start - b.start);
      this.ctx.pushData({ sessions, inputs: i, colors, sec: TF_SEC[this.tf] });
      const series = [];
      const devMap = new Map(); for (const s of sessions) for (const d of s.dev || []) devMap.set(d[0] * 1000, d);
      // Break the developing lines between sessions (no vertical jump from one session's level to the next).
      for (const s of sessions) if (s.dev && s.dev.length) devMap.set(s.dev[0][0] * 1000, null);
      const line = (id, title, k, color) => ({ id, title, paneId: "", kind: "step", style: { color, width: 2, lineStyle: "solid" },
        points: bars.map((b) => { const d = devMap.get(b.time); return { time: b.time, value: d ? d[k] : null }; }) });
      if (i.devPoc) series.push(line("of-prof-dpoc", "Developing POC", 1, colors.poc));
      if (i.devVa) { series.push(line("of-prof-dvah", "Developing VAH", 2, rgba(colors.dim, 0.9))); series.push(line("of-prof-dval", "Developing VAL", 3, rgba(colors.dim, 0.9))); }
      const real = this.info && this.info.source === "real";
      const text = this.error && !this.info ? "no profile data: " + this.error.slice(0, 60) : real ? `SVP · REAL · Binance ${this.info.real_symbol}` : this.info ? "SVP · ESTIMATED" : "SVP loading...";
      const tip = this.info ? `${real ? "Real buyer/seller volume from Binance, shifted onto this chart's price. " : "Estimated from this chart's candles. "}${this.info.method}. Buy/sell per price row is approximate (1-minute bars); exact needs trade data (footprint step).` : "";
      this.ctx.emit({ series, tables: [{ id: "of-prof-src", paneId: "", position: "bottom_right", columns: 1, rows: 1, frameWidth: 0, borderWidth: 0, merges: [],
        cells: [[{ text, textColor: real ? colors.up : colors.dim, hAlign: "right", vAlign: "bottom", textSize: "small", fontFamily: "default", bold: false, italic: false, tooltip: tip }]] }] });
    }
    onBars() {
      const bars = this.ctx.bars(); if (!bars.length) return;
      // Older history landed (user scrolled back): one full reload, compared with what we last ASKED for, never with what
      // came back (a capped or failed answer would otherwise reload on every tick and flood the remote MT5 bridge; seen 2026-11-12).
      if (this.askedFrom != null && bars[0].time / 1000 < this.askedFrom && Date.now() - (this.lastFull || 0) > 30000) return this.load(true);
      if (Date.now() - this.lastLive > 15000) { this.lastLive = Date.now(); this.load(false); }  // running session: every 15 s
    }
    onViewport() {}
    setInputs(inputs) {
      const old = this.inputs; this.inputs = { ...this.inputs, ...inputs };
      const refetch = ["mode", "layout", "rows", "rowSize", "va", "source"].some((k) => inputs[k] !== undefined && inputs[k] !== old[k]) ||
        ((inputs.devPoc || inputs.devVa) && !(old.devPoc || old.devVa));
      if (refetch) this.load(true); else this.emit();
    }
    suspend() { this.stopped = true; }
    resume() { this.stopped = false; this.load(true); }
    stop() { this.stopped = true; this.sess.clear(); try { this.ctx.pushData(null); } catch (_) {} }
  }

  // ---- drawing (canvas layer behind the candles) ----
  // Shared drawing (also used by the 'Session volume profile' chart type in orderflow_charttypes.js).
  function drawProfile(canvas, g, a, d) {
          if (!d || !d.sessions || !d.sessions.length) return;
          const C = a.coords, B = (v) => C.toBitmap(v), i = d.inputs, col = d.colors, sec = d.sec;
          const Y = (p) => B(C.priceToY(p, a.scale, a.bounds));
          const half = (C.bodySpacing ? C.bodySpacing() : 6) / 2;
          const lastStart = d.sessions[d.sessions.length - 1].start;
          for (const s of d.sessions) {
            const t0 = (s.first - s.first % sec) * 1000, t1 = ((s.last - 1) - (s.last - 1) % sec) * 1000;
            const x0 = B(C.timeToX(t0) - half), x1 = B(C.timeToX(t1) + half);
            if (x1 < 0 || x0 > canvas.width || x1 - x0 < 2) continue;
            const n = s.buy.length, top = s.lo + n * s.step;
            const tot = s.buy.map((b, k) => b + s.sell[k]);
            const vaLo = Math.floor((s.val - s.lo) / s.step + 1e-9), vaHi = Math.ceil((s.vah - s.lo) / s.step - 1e-9) - 1;
            const maxV = i.volume === VOLS[2] ? Math.max(...s.buy.map((b, k) => Math.abs(b - s.sell[k])), 1e-12) : Math.max(...tot, 1e-12);
            const wMax = (x1 - x0) * i.width / 100, right = i.place === PLACES[1];
            if (i.box) { g.fillStyle = rgba(col.dim, 0.06); g.fillRect(x0, Y(top), x1 - x0, Y(s.lo) - Y(top)); }
            for (let k = 0; k < n; k++) {
              const yTop = Y(s.lo + (k + 1) * s.step), yBot = Y(s.lo + k * s.step);
              const h = Math.max(1, yBot - yTop - Math.max(1, B(0.5)));
              const inVa = k >= vaLo && k <= vaHi, alpha = inVa ? 0.55 : 0.22;
              const parts = i.volume === VOLS[0] ? [[s.buy[k], col.up], [s.sell[k], col.down]]
                : i.volume === VOLS[1] ? [[tot[k], k === s.poc_i ? col.poc : col.dim]]
                : [[Math.abs(s.buy[k] - s.sell[k]), s.buy[k] >= s.sell[k] ? col.up : col.down]];
              let off = 0;
              for (const [v, c] of parts) {
                const w = wMax * v / maxV; if (w <= 0) continue;
                g.fillStyle = rgba(c, i.volume === VOLS[1] ? alpha + 0.15 : alpha);
                g.fillRect(right ? x1 - off - w : x0 + off, yTop, w, h); off += w;
              }
            }
            const xe = i.extend && s.start === lastStart ? canvas.width : x1;
            const hline = (p, c, w, dash) => { g.strokeStyle = c; g.lineWidth = B(w); g.setLineDash(dash.map(B)); g.beginPath(); g.moveTo(x0, Y(p)); g.lineTo(xe, Y(p)); g.stroke(); };
            if (i.poc) hline(s.poc, col.poc, 1.5, []);
            if (i.vavl) { hline(s.vah, rgba(col.dim, 0.9), 1, [4, 3]); hline(s.val, rgba(col.dim, 0.9), 1, [4, 3]); }
            g.setLineDash([]);
          }
  }
  window.OFDrawProfile = drawProfile; window.OFProfileOptions = { MODES, LAYOUTS, VOLS, PLACES, SOURCES, SRC_PARAM };

  VelaLab.registerRendererLayer({
    id: TYPE, placement: "below-data",
    create() {
      let canvas = null;
      return {
        mount(c) { canvas = c; },
        render(a) {
          if (!canvas) return;
          const g = canvas.getContext("2d"); g.setTransform(1, 0, 0, 1, 0, 0); g.clearRect(0, 0, canvas.width, canvas.height);
          window.OFDrawProfile(canvas, g, a, a.data);
        },
      };
    },
  });

  VelaLab.registerNativeIndicator({
    type: TYPE, title: "Session Volume Profile (order flow)", shortTitle: "SVP",
    paneHint: "price", overlay: true,
    inputsSchema: schema, defaultInputs: defaults, create: () => new OrderFlowProfile(),
  });
})();
