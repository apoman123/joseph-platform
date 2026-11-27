// "Time price opportunity" (TPO / Market Profile) CHART TYPE, organised like TradingView's (2026-11-12).
// Each time block (default 30 min) gets a letter A-Z, then a-z; the letter sits at every price row that block traded.
// Needs only prices (no volume), so it is exact on every pair. Data: the chart feed's bars at the block timeframe.
// Settings follow TradingView's TPO chart type.
(function () {
  const F = window.OFFootprint;
  if (!F || !window.VelaLab || !VelaLab.registerChartType) return;
  const ID = "tpo", T = window.HubTheme || {};
  const BLOCKS = { "15 min": "M15", "30 min": "M30", "1 hour": "H1" };
  const PERIODS = ["Asia / London / New York", "Day (UTC)", "Week (UTC)"];
  const LET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz";
  const settings = {
    title: "Time price opportunity", layout: "grouped",
    rows: [
      { kind: "heading", label: "Profile" },
      { kind: "select", key: "period", label: "Profile per", options: PERIODS, defval: PERIODS[0] },
      { kind: "select", key: "block", label: "Block size", options: Object.keys(BLOCKS), defval: "30 min" },
      { kind: "select", key: "rowMode", label: "Row size", options: ["Auto", "Manual"], defval: "Auto" },
      { kind: "number", key: "rowSize", label: "Price per row", defval: 1, min: 0, step: 0.00001, when: { key: "rowMode", equals: "Manual" } },
      { kind: "number", key: "va", label: "Value area %", defval: 70, min: 10, max: 100 },
      { kind: "heading", label: "Style" },
      { kind: "toggle", key: "letters", label: "Letters", defval: true },
      { kind: "toggle", key: "blocks", label: "Blocks", defval: true },
      { kind: "number", key: "dim", label: "Opacity outside value area %", defval: 35, min: 0, max: 100 },
      { kind: "toggle", key: "split", label: "Split by blocks (spread letters along time)", defval: false },
      { kind: "toggle", key: "poc", label: "POC", defval: true, colors: [{ key: "pocColor", label: "POC", defval: T.accent || "#f0b90b" }] },
      { kind: "toggle", key: "extendPoc", label: "Extend POC until price crosses it", defval: true },
      { kind: "toggle", key: "vavl", label: "VAH / VAL lines", defval: true },
      { kind: "toggle", key: "singles", label: "Single prints", defval: true },
      { kind: "toggle", key: "mid", label: "TPO midpoint", defval: false },
      { kind: "toggle", key: "ib", label: "Initial balance", defval: true, number: { key: "ibBlocks", label: "Blocks", defval: 2, min: 1, max: 8 } },
      { kind: "heading", label: "Colours" },
      { kind: "row", label: "Early / late blocks", controls: [{ kind: "color", key: "c1", label: "Early", defval: T.up || "#26a69a" }, { kind: "color", key: "c2", label: "Late", defval: "#5b8def" }] },
    ],
  };
  const defaults = {}; settings.rows.forEach((r) => { if (r.key) defaults[r.key] = r.defval; if (r.number) defaults[r.number.key] = r.number.defval; (r.colors || []).forEach((c) => (defaults[c.key] = c.defval)); (r.controls || []).forEach((c) => c.key && (defaults[c.key] = c.defval)); });

  function periodOf(t, mode) {           // UTC seconds -> profile start (same session cut as the volume profile)
    const day = t - (t % 86400), h = (t % 86400) / 3600;
    if (mode === PERIODS[1]) return day;
    if (mode === PERIODS[2]) return day - ((day / 86400 + 3) % 7) * 86400;
    return h >= 22 ? day + 22 * 3600 : h < 7 ? day - 2 * 3600 : h < 13 ? day + 7 * 3600 : day + 13 * 3600;
  }
  const nice = (x) => { if (x <= 0) return 1; const e = 10 ** Math.floor(Math.log10(x)); for (const m of [1, 2, 2.5, 5, 10]) if (x <= m * e) return m * e; return 10 * e; };

  // Build profiles: rows[r] = list of block indexes that traded at row r.
  function build(bars, s) {
    const step = s.rowMode === "Manual" && s.rowSize > 0 ? s.rowSize
      : nice((Math.max(...bars.slice(-300).map((b) => b.high)) - Math.min(...bars.slice(-300).map((b) => b.low))) / 80);  // TradingView auto
    const groups = new Map();
    for (const b of bars) { const k = periodOf(b.time, s.period); if (!groups.has(k)) groups.set(k, []); groups.get(k).push(b); }
    const out = [];
    for (const [start, bs] of groups) {
      const r0 = Math.floor(Math.min(...bs.map((b) => b.low)) / step + 1e-9), r1 = Math.floor(Math.max(...bs.map((b) => b.high)) / step + 1e-9);
      const rows = Array.from({ length: r1 - r0 + 1 }, () => []);
      bs.forEach((b, k) => { for (let r = Math.floor(b.low / step + 1e-9); r <= Math.floor(b.high / step + 1e-9); r++) rows[r - r0].push(k); });
      const cnt = rows.map((x) => x.length), total = cnt.reduce((a, b) => a + b, 0);
      // POC: most letters; ties go to the row nearest the middle (common Market Profile rule)
      let poc = 0; const midR = (rows.length - 1) / 2;
      cnt.forEach((c, i) => { if (c > cnt[poc] || (c === cnt[poc] && Math.abs(i - midR) < Math.abs(poc - midR))) poc = i; });
      let lo = poc, hi = poc, inside = cnt[poc];
      while (inside < (s.va / 100) * total && (lo > 0 || hi < rows.length - 1)) {
        const up = hi < rows.length - 1 ? cnt[hi + 1] : -1, dn = lo > 0 ? cnt[lo - 1] : -1;
        if (up >= dn) { hi++; inside += up; } else { lo--; inside += dn; }
      }
      out.push({ start, bars: bs, r0, rows, poc, vaLo: lo, vaHi: hi, step });
    }
    return out.sort((a, b) => a.start - b.start);
  }

  class TpoEngine {
    start(h) { this.h = h; this.s = this.s || { ...defaults }; this.stopped = false; this.load(true); }
    async load(full) {
      if (this.busy || this.stopped) { this.again = full ? "full" : (this.again || true); return; }
      const t = F.ticker(this.h), tf = BLOCKS[this.s.block] || "M30";
      const key = t + "|" + tf, inc = !full && this.key === key && this.bars && this.bars.length;
      this.busy = true;
      try {   // first load 1000 blocks; after that only the last 6 (keeps the remote MT5 link light)
        const r = await fetch(`/api/bars?symbol=${encodeURIComponent(t)}&tf=${tf}&limit=${inc ? 6 : 1000}&source=${F.feedOf(t)}`);
        const j = await r.json(); if (!Array.isArray(j)) throw new Error(j.error || "no bars");
        if (inc) { const m = new Map(this.bars.map((b) => [b.time, b])); j.forEach((b) => m.set(b.time, b)); this.bars = [...m.values()].sort((x, y) => x.time - y.time).slice(-1200); }
        else { this.bars = j; this.key = key; }
        this.err = null;
      } catch (e) { this.err = String(e.message || e); }
      this.busy = false; this.push(); this.lastLoad = Date.now();
      if (this.again) { const f = this.again === "full"; this.again = false; this.load(f); }
    }
    push() {
      if (this.stopped) return;
      let prof = []; try { if (this.bars && this.bars.length) prof = build(this.bars, this.s); } catch (e) { this.err = String(e); }
      this.h.pushData({ prof, s: this.s, err: this.err, blockSec: F.TF_SEC[BLOCKS[this.s.block] || "M30"] });
    }
    onBars() { if (!this.busy && Date.now() - (this.lastLoad || 0) > 20000) this.load(); }   // the running block updates every 20 s
    onViewport() {}
    onSettings(v) { const old = this.s || defaults; this.s = { ...defaults, ...v }; if (!this.h) return; if (old.block !== this.s.block) this.load(true); else this.push(); }
    suspend() { this.stopped = true; } resume() { this.stopped = false; this.load(true); } stop() { this.stopped = true; }
  }

  function mix(c1, c2, f) { const p = (c) => { const n = parseInt(String(c).replace("#", "").slice(0, 6), 16); return [n >> 16 & 255, n >> 8 & 255, n & 255]; };
    const a = p(c1), b = p(c2); return a.map((v, i) => Math.round(v + (b[i] - v) * f)); }

  function draw(canvas, g, a) {
    const d = a.data; if (!d) return;
    const s = d.s, C = a.coords, B = (v) => C.toBitmap(v), Y = (p) => B(C.priceToY(p, a.scale, a.bounds)), W = canvas.width;
    const txt = (a.theme && a.theme.textColor) || "#d1d4dc", bars = a.bars;
    const tag = (m) => { g.textAlign = "right"; g.font = `${B(11)}px system-ui, sans-serif`; g.fillStyle = F.rgba(txt, 0.7); g.fillText(m, W - B(8), Y(a.scale.min) - B(10)); };
    if (!d.prof || !d.prof.length) return tag(d.err ? "TPO: no data (" + d.err.slice(0, 40) + ")" : "TPO loading...");
    for (let n = 0; n < d.prof.length; n++) {
      const p = d.prof[n], next = d.prof[n + 1];
      const x0 = B(C.timeToX(p.start * 1000)), xEnd = next ? B(C.timeToX(next.start * 1000)) : W;
      if (xEnd < 0 || x0 > W) continue;
      const rowH = Math.abs(Y(p.step) - Y(0)), cw = Math.max(B(4), Math.min(B(11), rowH * 0.85));
      const fs = Math.min(B(11), rowH * 0.95), showLet = s.letters && fs >= B(6);
      g.font = `${fs}px ui-monospace, Consolas, monospace`; g.textAlign = "center"; g.textBaseline = "middle";
      const nb = p.bars.length;
      for (let r = 0; r < p.rows.length; r++) {
        const yT = Y((p.r0 + r + 1) * p.step), h = Math.max(1, Y((p.r0 + r) * p.step) - yT - B(0.5));
        const inVa = r >= p.vaLo && r <= p.vaHi, alpha = inVa ? 1 : s.dim / 100;
        p.rows[r].forEach((k, col) => {
          const x = s.split ? B(C.timeToX(p.bars[k].time * 1000)) - cw / 2 : x0 + col * cw;
          if (x > xEnd - cw) return;
          const [cr, cg, cb] = mix(s.c1, s.c2, nb > 1 ? k / (nb - 1) : 0);
          if (s.blocks) { g.fillStyle = `rgba(${cr},${cg},${cb},${0.55 * alpha})`; g.fillRect(x, yT, cw - B(1), h); }
          if (showLet) { g.fillStyle = s.blocks ? F.rgba(txt, alpha) : `rgba(${cr},${cg},${cb},${alpha})`; g.fillText(LET[k % LET.length], x + cw / 2, yT + h / 2); }
        });
        if (s.singles && p.rows[r].length === 1 && r > 0 && r < p.rows.length - 1) {   // single print: one letter, inside the range
          g.fillStyle = F.rgba(s.c2, 0.9); g.fillRect(x0 - B(4), yT, B(2.5), h);
        }
      }
      const yP = Y((p.r0 + p.poc + 0.5) * p.step);
      if (s.poc) {
        let xe = x0 + Math.max(...p.rows.map((x) => x.length)) * cw;
        if (s.extendPoc) {   // extend until a later bar trades through the POC (TradingView's "until price crosses")
          const price = (p.r0 + p.poc + 0.5) * p.step, after = bars.filter((b) => b.time / 1000 >= (next ? next.start : Infinity));
          const hit = after.find((b) => b.low <= price && b.high >= price);
          xe = next ? (hit ? B(C.timeToX(hit.time)) : W) : xe;
        }
        g.strokeStyle = s.pocColor; g.lineWidth = B(1.5); g.setLineDash([]); g.beginPath(); g.moveTo(x0, yP); g.lineTo(Math.max(xe, x0 + B(20)), yP); g.stroke();
      }
      if (s.vavl) {
        g.strokeStyle = F.rgba(txt, 0.55); g.lineWidth = B(1); g.setLineDash([B(4), B(3)]);
        for (const y of [Y((p.r0 + p.vaHi + 1) * p.step), Y((p.r0 + p.vaLo) * p.step)]) { g.beginPath(); g.moveTo(x0, y); g.lineTo(xEnd - B(4), y); g.stroke(); }
        g.setLineDash([]);
      }
      if (s.mid) { const ym = Y(((p.r0) + (p.r0 + p.rows.length)) / 2 * p.step); g.fillStyle = F.rgba(txt, 0.8); g.fillRect(x0 - B(7), ym - B(1), B(5), B(2)); }
      if (s.ib) {   // initial balance: high-low of the first blocks, drawn as a bar left of the profile
        const ibb = p.bars.slice(0, s.ibBlocks), hi = Math.max(...ibb.map((b) => b.high)), lo = Math.min(...ibb.map((b) => b.low));
        g.fillStyle = F.rgba(s.c1, 0.9); g.fillRect(x0 - B(10), Y(hi), B(2.5), Y(lo) - Y(hi));
      }
    }
    tag(`TPO ${s.block}`);
  }

  VelaLab.registerRendererLayer({
    id: ID, placement: "above-data",
    create() {
      let canvas = null;
      return {
        mount(c) { canvas = c; },
        render(a) {
          if (!canvas) return;
          const g = canvas.getContext("2d"); g.setTransform(1, 0, 0, 1, 0, 0); g.clearRect(0, 0, canvas.width, canvas.height);
          if (a.priceStyle !== ID) return;
          try { draw(canvas, g, a); } catch (e) { console.warn("tpo draw", e); }
        },
      };
    },
  });

  VelaLab.registerChartType({
    id: ID, label: "Time price opportunity", settings, basePainting: "none", tickerModifier: false,
    icon: '<svg viewBox="0 0 28 28" width="28" height="28" fill="currentColor"><text x="5" y="10" font-size="7" font-family="monospace">AB</text><text x="5" y="17" font-size="7" font-family="monospace">ABC</text><text x="5" y="24" font-size="7" font-family="monospace">B</text></svg>',
    dataEngine: () => new TpoEngine(),
  });
})();
