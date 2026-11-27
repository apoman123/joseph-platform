// TradeX theme: presets + the user's own overrides (Settings panel), shared by the wrapper and all engines.
// Saved in localStorage "chartHubTheme" (same origin, so every engine iframe reads the same settings).
window.HubPresets = {
  tradex: { label: "TradeX brand (logo)", mode: "dark",   // Saifullah's brand CORE 2026-11-25: chrome silver on black, bull green / bear red
    bg: "#0a0a0b", panel: "#131416", raised: "#1c1e21", line: "#2a2d31", grid: "#1c1e21",
    text: "#e8ecee", dim: "#b4bcbf", faint: "#797e86", accent: "#e8ecee", accentInk: "#0a0a0b",
    up: "#46a76f", down: "#d65b4b", ok: "#46a76f", bad: "#d65b4b" },
  graphite: { label: "Graphite & amber", mode: "dark",   // chosen 2026-11-02 (_design/concepts/A_graphite_amber_1.jpg)
    bg: "#151412", panel: "#1b1a17", raised: "#24221e", line: "#2f2c27", grid: "#1f1d1a",
    text: "#e6dfd2", dim: "#958c7e", faint: "#5e584f", accent: "#e3a33a", accentInk: "#1a1407",
    up: "#e3a33a", down: "#b4472f", ok: "#8fb36a", bad: "#c9553c" },
  black: { label: "Pure black", mode: "dark",
    bg: "#000000", panel: "#0b0b0b", raised: "#181818", line: "#262626", grid: "#121212",
    text: "#ececec", dim: "#8a8a8a", faint: "#4a4a4a", accent: "#e3a33a", accentInk: "#141005",
    up: "#e3a33a", down: "#b4472f", ok: "#8fb36a", bad: "#c9553c" },
  light: { label: "Light (white)", mode: "light",
    bg: "#ffffff", panel: "#f6f4f0", raised: "#ebe7e0", line: "#dcd6cc", grid: "#f0ede8",
    text: "#1d1b18", dim: "#6f685e", faint: "#b3ab9f", accent: "#b8761a", accentInk: "#ffffff",
    up: "#1d1b18", down: "#c0442b", ok: "#4e7d2c", bad: "#c0442b" },
};
// Font choices: the default system font plus smoother, rounder faces (loaded from Google Fonts when picked).
window.HubFonts = {
  system: { label: "Segoe UI (default)", css: '"Segoe UI Variable Text", "Segoe UI", system-ui, sans-serif' },
  nunito: { label: "Nunito (rounded)", css: '"Nunito", "Segoe UI", sans-serif', gf: "Nunito:wght@400;600;700" },
  manrope: { label: "Manrope (soft)", css: '"Manrope", "Segoe UI", sans-serif', gf: "Manrope:wght@400;600;700" },
  quicksand: { label: "Quicksand (very round)", css: '"Quicksand", "Segoe UI", sans-serif', gf: "Quicksand:wght@400;600;700" },
  inter: { label: "Inter (neutral)", css: '"Inter", "Segoe UI", sans-serif', gf: "Inter:wght@400;600;700" },
};
window.HubThemeKeys = ["bg", "panel", "raised", "line", "grid", "text", "dim", "faint", "accent", "accentInk", "up", "down", "ok", "bad"];

function hexA(hex, a) { // "#rrggbb" + alpha -> rgba()
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
}
window.HubSettingsLoad = () => {
  let s = {};
  try { s = JSON.parse(localStorage.getItem("chartHubTheme") || "{}"); } catch (_) {}
  // watermark = strength of the symbol name behind the candles, 0-40 (%); Vela's own default is 5.
  const wm = Number.isFinite(+s.watermark) && s.watermark !== null && s.watermark !== "" ? Math.max(0, Math.min(40, +s.watermark)) : 5;
  return { preset: HubPresets[s.preset] ? s.preset : (window.TX_DEFAULT_PRESET || "graphite"), font: HubFonts[s.font] ? s.font : "system", overrides: s.overrides || {}, indicatorBars: !!s.indicatorBars, watermark: wm, candleLook: ["flat", "lift", "matte", "matte_lift"].includes(s.candleLook) ? s.candleLook : "matte_lift" };
};
window.HubSettingsSave = (s) => { try { localStorage.setItem("chartHubTheme", JSON.stringify(s)); } catch (_) {} if (window.layoutsDirty) window.layoutsDirty(); };  // layouts.js autosave

// Build window.HubTheme from the saved settings and push it into CSS variables. Returns the theme.
window.HubThemeApply = () => {
  const s = HubSettingsLoad(), base = HubPresets[s.preset], f = HubFonts[s.font];
  const t = Object.assign({}, base, s.overrides);
  t.preset = s.preset; t.fontKey = s.font; t.font = f.css;
  t.mono = '"Cascadia Mono", Consolas, "SFMono-Regular", monospace';
  t.upVol = hexA(t.up, 0.42); t.downVol = hexA(t.down, 0.42);
  t.lines = t.mode === "light" ? ["#1d1b18", t.accent, "#8a7f6e", "#c0442b"] : [t.text, t.accent, "#9c8f7a", "#c97a4a"];
  window.HubTheme = t;
  const st = document.documentElement.style;
  for (const k of HubThemeKeys) st.setProperty("--" + k, t[k]);
  st.setProperty("--font", t.font); st.setProperty("--mono", t.mono);
  st.setProperty("--tx-wm", String(s.watermark / 100));   // vela.html: symbol watermark strength
  window.__txCandle = s.candleLook;   // web chart bundle: candle look (build_web.py patch_candles)
  document.documentElement.style.colorScheme = t.mode;
  if (f.gf && !document.getElementById("gf-" + s.font)) {
    const l = document.createElement("link"); l.id = "gf-" + s.font; l.rel = "stylesheet";
    l.href = `https://fonts.googleapis.com/css2?family=${f.gf}&display=swap`; document.head.appendChild(l);
  }
  return t;
};
HubThemeApply();
