// Drawing + registration of the "Volume footprint" chart type (engine in orderflow_charttypes.js).
// Look follows TradingView's footprint: thin candle in the middle, sellers' cells left, buyers' cells right,
// shade by volume (4 steps inside each candle), POC box, imbalance marks, stacked-imbalance zones, Delta/Total box.
(function () {
  const F = window.OFFootprint; if (!F) return;
  const { FP, rgba, fmt } = F;

  function imbalances(buy, sell, ratio, minV) {
    // TradingView rule (diagonal): buyers at row r vs sellers one row below; sellers at r vs buyers one row above.
    const n = buy.length, bi = new Array(n).fill(false), si = new Array(n).fill(false), k = ratio / 100;
    for (let r = 0; r < n; r++) {
      if (r > 0 && buy[r] >= minV && buy[r] >= k * Math.max(sell[r - 1], 1e-9)) bi[r] = true;
      if (r < n - 1 && sell[r] >= minV && sell[r] >= k * Math.max(buy[r + 1], 1e-9)) si[r] = true;
    }
    return { bi, si };
  }
  const runs = (flags, need) => { const out = []; let s = -1; for (let r = 0; r <= flags.length; r++) { if (r < flags.length && flags[r]) { if (s < 0) s = r; } else { if (s >= 0 && r - s >= need) out.push([s, r - 1]); s = -1; } } return out; };

  function draw(canvas, g, a) {
    const d = a.data; if (!d || !d.candles) return;
    const s = d.settings || F.fpDefaults, C = a.coords, B = (v) => C.toBitmap(v), Y = (p) => B(C.priceToY(p, a.scale, a.bounds));
    const pitch = Math.abs(C.logicalToX(1) - C.logicalToX(0)), row = d.row || 1;
    const half = B(pitch * 0.46), gap = B(Math.max(1.5, pitch * 0.05));
    const buyC = s.buyColor, sellC = s.sellColor, pocC = s.pocColor, txt = (a.theme && a.theme.textColor) || "#d1d4dc";
    const bars = a.bars, W = canvas.width, zones = []; let estN = 0;
    g.textAlign = "center"; g.textBaseline = "middle";
    for (let i = 0; i < bars.length; i++) {
      const bar = bars[i], c = d.candles.get(bar.time); if (!c) continue;
      if (c.est) estN++;
      g.globalAlpha = c.est ? 0.4 : 1;   // estimated (trades still loading): dimmed until the real trades replace it
      const x = B(C.timeToX(bar.time)); if (x < -half || x > W + half) continue;
      const n = c.buy.length, tot = c.buy.map((b, k) => b + c.sell[k]);
      const maxT = Math.max(...tot, 1e-9), maxS = Math.max(...c.buy, ...c.sell, 1e-9);
      const pocR = tot.indexOf(maxT), { bi, si } = s.imb ? imbalances(c.buy, c.sell, s.imbRatio, s.imbMin) : { bi: [], si: [] };
      const shade = (v, m) => 0.18 + 0.62 * Math.min(1, Math.ceil(4 * v / m) / 4);   // 4 steps like TradingView's gradient
      const hRow = Math.abs(Y(row) - Y(0)), fs = Math.min(B(11), hRow - B(2)), showTxt = s.numbers && fs >= B(7);
      g.font = `${Math.max(1, fs)}px system-ui, sans-serif`;
      for (let r = 0; r < n; r++) {
        const p0 = (c.r0 + r) * row, yT = Y(p0 + row), h = Math.max(1, Y(p0) - yT - B(0.6));
        if (s.type === "Buy and Sell") {
          const wl = half - gap;
          g.fillStyle = rgba(sellC, shade(c.sell[r], maxS)); g.fillRect(x - half, yT, wl, h);
          g.fillStyle = rgba(buyC, shade(c.buy[r], maxS)); g.fillRect(x + gap, yT, wl, h);
          if (si[r]) { g.fillStyle = sellC; g.fillRect(x - half - B(2), yT, B(2), h); }
          if (bi[r]) { g.fillStyle = buyC; g.fillRect(x + half, yT, B(2), h); }
          if (showTxt && wl >= B(18)) {
            g.fillStyle = si[r] ? sellC : txt; g.fillText(fmt(c.sell[r]), x - half + wl / 2, yT + h / 2);
            g.fillStyle = bi[r] ? buyC : txt; g.fillText(fmt(c.buy[r]), x + gap + wl / 2, yT + h / 2);
          }
        } else if (s.type === "Profile") {      // his drawing: a small buy/sell histogram beside the candle
          const w = (2 * half - gap) * tot[r] / maxT, wb = tot[r] ? w * c.buy[r] / tot[r] : 0;
          g.fillStyle = rgba(buyC, 0.75); g.fillRect(x + gap, yT, wb, h);
          g.fillStyle = rgba(sellC, 0.75); g.fillRect(x + gap + wb, yT, w - wb, h);
        } else {
          const v = s.type === "Delta" ? c.buy[r] - c.sell[r] : tot[r], m = s.type === "Delta" ? Math.max(...tot.map((t, k) => Math.abs(c.buy[k] - c.sell[k])), 1e-9) : maxT;
          g.fillStyle = s.type === "Delta" ? rgba(v >= 0 ? buyC : sellC, shade(Math.abs(v), m)) : rgba(txt, 0.08 + 0.5 * tot[r] / maxT);
          g.fillRect(x + gap, yT, half - gap, h);
          if (showTxt && half - gap >= B(18)) { g.fillStyle = txt; g.fillText(fmt(v), x + gap + (half - gap) / 2, yT + h / 2); }
        }
        if (s.poc && r === pocR) { g.strokeStyle = pocC; g.lineWidth = B(1.2); g.strokeRect(x - (s.type === "Buy and Sell" ? half : -gap), yT, s.type === "Buy and Sell" ? 2 * half : (s.type === "Profile" ? 2 * half - gap : half - gap), h); }
      }
      if (s.imb && s.stacked) {
        for (const [r0, r1] of runs(bi, s.stackN)) zones.push({ i, lo: (c.r0 + r0) * row, hi: (c.r0 + r1 + 1) * row, col: buyC });
        for (const [r0, r1] of runs(si, s.stackN)) zones.push({ i, lo: (c.r0 + r0) * row, hi: (c.r0 + r1 + 1) * row, col: sellC });
      }
      if (s.summary && half >= B(16)) {
        const delta = c.buy.reduce((u, v) => u + v, 0) - c.sell.reduce((u, v) => u + v, 0), all = tot.reduce((u, v) => u + v, 0);
        const y0 = Y(bar.low) + B(6), fz = Math.min(B(10), half / 3.2);
        g.font = `${fz}px system-ui, sans-serif`;
        g.fillStyle = rgba(txt, 0.08); g.fillRect(x - half, y0, 2 * half, fz * 2.6);
        g.fillStyle = delta >= 0 ? buyC : sellC; g.fillText("Δ " + fmt(delta), x, y0 + fz * 0.75);
        g.fillStyle = txt; g.fillText("Σ " + fmt(all), x, y0 + fz * 1.9);
      }
    }
    g.globalAlpha = 1;
    // Stacked imbalance zones: extend right until a later candle trades through the zone's middle (TradingView's projection).
    for (const z of zones) {
      const mid = (z.lo + z.hi) / 2; let j = z.i + 1;
      while (j < bars.length && !(bars[j].low <= mid && bars[j].high >= mid)) j++;
      const x0 = B(C.timeToX(bars[z.i].time)) + half, x1 = j < bars.length ? B(C.timeToX(bars[j].time)) : W;
      g.fillStyle = rgba(z.col, 0.09); g.fillRect(x0, Y(z.hi), Math.max(0, x1 - x0), Y(z.lo) - Y(z.hi));
    }
    // Source tag, bottom left (after the Vela logo): always say real or estimated, and how far back today's trades are loaded.
    // left-aligned: the canvas runs under the price axis, which hid the right end
    const i = d.info; g.textAlign = "left"; g.font = `${B(11)}px system-ui, sans-serif`; g.fillStyle = rgba(txt, 0.7);
    const tag = d.err && !i ? "Footprint: no data (" + d.err.slice(0, 50) + ")" : !i ? "Footprint loading..." :
      i.source === "real" ? `Footprint · REAL trades · Binance ${i.real_symbol} · row ${i.row_size}` +
        (estN ? ` · ${estN} dimmed candle${estN > 1 ? "s" : ""} estimated from 1-min buy/sell` + (d.pending ? ", real trades loading..." : " (older than the trade files kept)") : "") : `Footprint · ESTIMATED (coarse) · row ${i.row_size}`;
    g.fillText(tag, B(96), B(C.priceToY(a.scale.min, a.scale, a.bounds)) - B(10));
  }

  VelaLab.registerRendererLayer({
    id: FP, placement: "above-data",
    create() {
      let canvas = null;
      return {
        mount(c) { canvas = c; },
        render(a) {
          if (!canvas) return;
          const g = canvas.getContext("2d"); g.setTransform(1, 0, 0, 1, 0, 0); g.clearRect(0, 0, canvas.width, canvas.height);
          if (a.priceStyle !== FP) return;
          try { draw(canvas, g, a); } catch (e) { console.warn("footprint draw", e); }
        },
        modulateBase(a) { return a.priceStyle === FP ? { candleBodyScale: 0.12 } : null; },   // thin candle between the two columns
      };
    },
  });

  VelaLab.registerChartType({
    id: FP, label: "Volume footprint", settings: F.fpSettings, basePainting: "candles", tickerModifier: false,
    icon: '<svg viewBox="0 0 28 28" width="28" height="28" fill="currentColor"><rect x="7" y="5" width="6" height="4" opacity=".5"/><rect x="15" y="5" width="6" height="4"/><rect x="7" y="10" width="6" height="4"/><rect x="15" y="10" width="6" height="4" opacity=".5"/><rect x="7" y="15" width="6" height="4" opacity=".7"/><rect x="15" y="15" width="6" height="4" opacity=".3"/><rect x="13.4" y="3" width="1.2" height="20"/></svg>',
    dataEngine: () => new F.FootprintEngine(),
  });
})();
