// "Session volume profile" CHART TYPE (TradingView has SVP both as a chart type and as an indicator; so do we).
// Candles stay; one profile per session behind them. Drawing shared with the indicator (window.OFDrawProfile).
(function () {
  const F = window.OFFootprint, O = window.OFProfileOptions;
  if (!F || !O || !window.OFDrawProfile || !VelaLab.registerChartType) return;
  const ID = "session_volume_profile", T = window.HubTheme || {};
  const settings = {
    title: "Session volume profile", layout: "grouped",
    rows: [
      { kind: "heading", label: "Profile" },
      { kind: "select", key: "mode", label: "Profile per", options: O.MODES, defval: O.MODES[0] },
      { kind: "select", key: "layout", label: "Row layout", options: O.LAYOUTS, defval: O.LAYOUTS[0] },
      { kind: "number", key: "rows", label: "Rows", defval: 24, min: 4, max: 200, when: { key: "layout", equals: O.LAYOUTS[0] } },
      { kind: "number", key: "rowSize", label: "Row size (price)", defval: 1, min: 0, step: 0.00001, when: { key: "layout", equals: O.LAYOUTS[1] } },
      { kind: "select", key: "volume", label: "Volume", options: O.VOLS, defval: O.VOLS[0] },
      { kind: "number", key: "va", label: "Value area %", defval: 70, min: 10, max: 100 },
      { kind: "heading", label: "Lines" },
      { kind: "toggle", key: "poc", label: "POC", defval: true, colors: [{ key: "pocColor", label: "POC", defval: T.accent || "#f0b90b" }] },
      { kind: "toggle", key: "vavl", label: "VAH / VAL", defval: true },
      { kind: "toggle", key: "extend", label: "Extend lines right (last session)", defval: false },
      { kind: "toggle", key: "devPoc", label: "Developing POC", defval: false },
      { kind: "toggle", key: "devVa", label: "Developing VA", defval: false },
      { kind: "heading", label: "Look" },
      { kind: "number", key: "width", label: "Width (% of session)", defval: 60, min: 10, max: 100 },
      { kind: "select", key: "place", label: "Placement", options: O.PLACES, defval: O.PLACES[0] },
      { kind: "toggle", key: "box", label: "Session box", defval: true },
      { kind: "row", label: "Buy / Sell", controls: [{ kind: "color", key: "upColor", label: "Buy", defval: T.up || "#26a69a" }, { kind: "color", key: "downColor", label: "Sell", defval: T.down || "#ef5350" }] },
      { kind: "heading", label: "Data" },
      { kind: "select", key: "source", label: "Data", options: O.SOURCES, defval: O.SOURCES[0] },
    ],
  };
  const defaults = {}; settings.rows.forEach((r) => { if (r.key) defaults[r.key] = r.defval; (r.colors || []).forEach((c) => (defaults[c.key] = c.defval)); (r.controls || []).forEach((c) => c.key && (defaults[c.key] = c.defval)); });
  const REFETCH = ["mode", "layout", "rows", "rowSize", "va", "source", "devPoc", "devVa"];

  class SvpEngine {
    start(h) { this.h = h; this.s = this.s || { ...defaults }; this.sess = new Map(); this.busy = false; this.stopped = false; this.load(true); }
    url(a, b) {
      const s = this.s, t = F.ticker(this.h);
      return `/api/orderflow/profile?symbol=${encodeURIComponent(t)}&tf=${F.TF[this.h.timeframe] || "M5"}&feed=${F.feedOf(t)}&from=${a}&to=${b}` +
        `&source=${O.SRC_PARAM[s.source] || "auto"}&mode=${s.mode === O.MODES[1] ? "day" : "sessions"}&rows=${s.rows}` +
        `&row_size=${s.layout === O.LAYOUTS[1] ? s.rowSize : 0}&va=${s.va}&developing=${s.devPoc || s.devVa ? 1 : 0}`;
    }
    async load(full) {
      if (this.busy) { this.pending = this.pending || full; return; }
      const bars = this.h.bars(); if (!bars.length || this.stopped) return;
      if (full) this.lastFull = Date.now();
      const a = full || !this.sess.size ? Math.floor(bars[0].time / 1000) : Math.max(...this.sess.keys()), b = Math.floor(bars[bars.length - 1].time / 1000);
      this.busy = true;
      try {
        const r = await fetch(this.url(a, b)); const j = await r.json(); if (!r.ok) throw new Error(j.error || r.status);
        if (full) this.sess = new Map(); this.info = j; this.err = null; for (const s of j.sessions) this.sess.set(s.start, s);
      } catch (e) { this.err = String(e.message || e); }
      this.busy = false; this.push();
      if (this.pending) { this.pending = false; this.load(true); }
    }
    push() {
      if (this.stopped) return;
      const s = this.s;
      this.h.pushData({ sessions: [...this.sess.values()].sort((x, y) => x.start - y.start), inputs: s, info: this.info, err: this.err,
        colors: { up: s.upColor, down: s.downColor, poc: s.pocColor, dim: T.dim || "#787b86", text: T.text || "#d1d4dc" }, sec: F.TF_SEC[F.TF[this.h.timeframe] || "M5"] });
    }
    onBars() { if (Date.now() - (this.live || 0) > 15000) { this.live = Date.now(); this.load(!this.sess.size && Date.now() - (this.lastFull || 0) > 60000); } }
    onViewport() {}
    onSettings(v) { const old = this.s || defaults; this.s = { ...defaults, ...v }; if (!this.h) return; if (REFETCH.some((k) => old[k] !== this.s[k])) this.load(true); else this.push(); }
    suspend() { this.stopped = true; } resume() { this.stopped = false; this.load(true); } stop() { this.stopped = true; }
  }

  VelaLab.registerRendererLayer({
    id: ID, placement: "below-data",
    create() {
      let canvas = null;
      return {
        mount(c) { canvas = c; },
        render(a) {
          if (!canvas) return;
          const g = canvas.getContext("2d"); g.setTransform(1, 0, 0, 1, 0, 0); g.clearRect(0, 0, canvas.width, canvas.height);
          if (a.priceStyle !== ID || !a.data) return;
          try {
            const d = a.data; window.OFDrawProfile(canvas, g, a, d);
            const C = a.coords, B = (v) => C.toBitmap(v), Y = (p) => B(C.priceToY(p, a.scale, a.bounds)), s = d.inputs;
            const line = (k, col, w) => { for (const se of d.sessions) { const pts = se.dev || []; if (pts.length < 2) continue; g.strokeStyle = col; g.lineWidth = B(w); g.beginPath();
              let py = null;  // step line: hold the level until the next candle, then move
              for (const p of pts) { const x = B(C.timeToX(p[0] * 1000)), y = Y(p[k]); if (py === null) g.moveTo(x, y); else { g.lineTo(x, py); g.lineTo(x, y); } py = y; }
              g.stroke(); } };
            if (s.devPoc) line(1, d.colors.poc, 1.5);
            if (s.devVa) { line(2, F.rgba(d.colors.dim, 0.9), 1); line(3, F.rgba(d.colors.dim, 0.9), 1); }
            const i = d.info; g.textAlign = "left"; g.font = `${B(11)}px system-ui, sans-serif`; g.fillStyle = F.rgba(d.colors.text, 0.7);
            g.fillText(!i ? (d.err ? "Profile: no data" : "Profile loading...") : i.source === "real" ? `SVP · REAL · Binance ${i.real_symbol}` : "SVP · ESTIMATED", B(96), Y(a.scale.min) - B(10));
          } catch (e) { console.warn("svp draw", e); }
        },
      };
    },
  });

  VelaLab.registerChartType({
    id: ID, label: "Session volume profile", settings, basePainting: "candles", tickerModifier: false,
    icon: '<svg viewBox="0 0 28 28" width="28" height="28" fill="currentColor"><rect x="5" y="5" width="7" height="3" opacity=".6"/><rect x="5" y="9" width="12" height="3"/><rect x="5" y="13" width="9" height="3" opacity=".8"/><rect x="5" y="17" width="5" height="3" opacity=".5"/><rect x="20" y="6" width="1.4" height="16"/><rect x="18.8" y="10" width="3.8" height="7"/></svg>',
    dataEngine: () => new SvpEngine(),
  });
})();
