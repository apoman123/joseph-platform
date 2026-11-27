// TradeX: floating favourites toolbar for drawing tools (TradingView-style), 2026-11-15.
// Star a tool in the left drawing toolbar's flyout and it appears on a small floating bar over the chart.
// Click a button to arm that tool (click again to disarm). Drag the grip to move the bar; double-click the grip to
// switch between horizontal and vertical. Stars and bar position are kept in localStorage, so they survive a reload.
// Vela calls ws.drawToolbar.setFavorites / setActiveTool for every chart cell, so the bar hooks those two calls.
(function () {
  const FAV_KEY = "tradexDrawFavs", POS_KEY = "tradexFavBar";
  const load = (k, d) => { try { const v = JSON.parse(localStorage.getItem(k)); return v == null ? d : v; } catch (_) { return d; } };
  const keep = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch (_) {} };

  const css = document.createElement("style");
  css.textContent = `
  .tx-favbar{position:absolute;z-index:29;display:flex;align-items:center;gap:2px;padding:3px;border-radius:8px;
    background:var(--panel,#1b1a17);border:1px solid var(--line,#2f2c27);box-shadow:0 4px 14px rgba(0,0,0,.28);
    user-select:none;touch-action:none}
  .tx-favbar.tx-vert{flex-direction:column}
  .tx-favbar[hidden]{display:none!important}
  .tx-grip{width:10px;height:28px;cursor:grab;display:flex;align-items:center;justify-content:center;color:var(--faint,#5e584f)}
  .tx-vert .tx-grip{width:28px;height:10px}
  .tx-grip:active{cursor:grabbing}
  .tx-grip svg{display:block}
  .tx-fav{width:30px;height:30px;border:0;border-radius:6px;background:transparent;color:var(--text,#e6dfd2);
    display:flex;align-items:center;justify-content:center;cursor:pointer;padding:0}
  .tx-fav:hover{background:var(--raised,#24221e)}
  .tx-fav.on{background:var(--accent,#e3a33a);color:var(--accentInk,#1a1407)}
  .tx-fav svg{width:22px;height:22px;display:block}`;
  document.head.appendChild(css);

  let ws = null, bar = null, favs = [], active = null, restored = false, pos = load(POS_KEY, null);

  function toolInfo() {
    const m = {};
    for (const g of (ws && ws.toolbarDef && ws.toolbarDef.groups) || [])
      for (const t of g.tools) m[t.type] = { label: typeof t.label === "string" ? t.label : t.type, icon: t.icon || "" };
    return m;
  }
  const activeDrawings = () => (ws.active || ws).chart.drawings;

  function place() {
    if (!bar || bar.hidden) return;
    const host = bar.parentElement, W = host.clientWidth, H = host.clientHeight;
    const w = bar.offsetWidth, h = bar.offsetHeight;
    // Default spot: top middle of the chart, below Vela's top bar.
    let x = pos ? pos.x : Math.round((W - w) / 2), y = pos ? pos.y : 48;
    x = Math.max(4, Math.min(x, W - w - 4)); y = Math.max(4, Math.min(y, H - h - 4));
    bar.style.left = x + "px"; bar.style.top = y + "px";
  }

  function render() {
    if (!bar) return;
    const info = toolInfo();
    const list = favs.filter((t) => info[t]);
    bar.querySelectorAll(".tx-fav").forEach((b) => b.remove());
    for (const t of list) {
      const b = document.createElement("button");
      b.className = "tx-fav" + (t === active ? " on" : ""); b.dataset.tool = t;
      b.title = info[t].label; b.setAttribute("aria-label", info[t].label); b.setAttribute("aria-pressed", t === active ? "true" : "false");
      b.innerHTML = info[t].icon;
      b.onclick = () => {
        const d = activeDrawings();
        d.setTool(d.getTool() === t ? null : t);
        if (ws.refocusActive) try { ws.refocusActive(); } catch (_) {}
      };
      bar.appendChild(b);
    }
    bar.hidden = list.length === 0;
    place();
  }

  function onFavorites(list) {
    favs = [...(list || [])];
    if (restored) keep(FAV_KEY, favs);
    render();
  }
  function onActive(type) {
    active = type || null;
    if (bar) bar.querySelectorAll(".tx-fav").forEach((b) => { const on = b.dataset.tool === active; b.classList.toggle("on", on); b.setAttribute("aria-pressed", on ? "true" : "false"); });
  }

  function makeBar(host) {
    bar = document.createElement("div");
    bar.className = "tx-favbar" + (pos && pos.vert ? " tx-vert" : ""); bar.hidden = true;
    bar.setAttribute("role", "toolbar"); bar.setAttribute("aria-label", "Favourite drawing tools");
    const grip = document.createElement("div");
    grip.className = "tx-grip"; grip.title = "Drag to move. Double-click to turn.";
    grip.innerHTML = '<svg width="6" height="14" viewBox="0 0 6 14" fill="currentColor"><circle cx="1.5" cy="2" r="1.2"/><circle cx="4.5" cy="2" r="1.2"/><circle cx="1.5" cy="7" r="1.2"/><circle cx="4.5" cy="7" r="1.2"/><circle cx="1.5" cy="12" r="1.2"/><circle cx="4.5" cy="12" r="1.2"/></svg>';
    bar.appendChild(grip);
    // Keep pointer and wheel events off the chart (else dragging the bar pans the chart).
    for (const ev of ["pointerdown", "pointerup", "mousedown", "mouseup", "click", "dblclick", "wheel", "contextmenu", "touchstart"])
      bar.addEventListener(ev, (e) => e.stopPropagation());
    grip.addEventListener("pointerdown", (e) => {
      e.preventDefault(); grip.setPointerCapture(e.pointerId);
      const sx = e.clientX - bar.offsetLeft, sy = e.clientY - bar.offsetTop;
      const move = (m) => { pos = { x: m.clientX - sx, y: m.clientY - sy, vert: bar.classList.contains("tx-vert") }; place(); };
      const up = () => { grip.removeEventListener("pointermove", move); grip.removeEventListener("pointerup", up); if (pos) keep(POS_KEY, pos); };
      grip.addEventListener("pointermove", move); grip.addEventListener("pointerup", up);
    });
    grip.addEventListener("dblclick", () => {
      bar.classList.toggle("tx-vert");
      pos = { x: bar.offsetLeft, y: bar.offsetTop, vert: bar.classList.contains("tx-vert") }; keep(POS_KEY, pos); place();
    });
    host.appendChild(bar);
  }

  // Called after every workspace build (a layout rebuild destroys the old workspace).
  function mount(workspace, host) {
    ws = workspace; restored = false; active = null;
    if (bar) bar.remove();
    makeBar(host);
    const tb = ws.drawToolbar;
    if (tb && !tb.txHooked) {
      tb.txHooked = true;
      const sf = tb.setFavorites.bind(tb), sa = tb.setActiveTool.bind(tb);
      tb.setFavorites = (list) => { sf(list); if (ws && ws.drawToolbar === tb) onFavorites(list); };
      tb.setActiveTool = (t) => { sa(t); if (ws && ws.drawToolbar === tb) onActive(t); };
    }
    // Vela keeps no favourites across a reload here (persist: false), so put the saved stars back once drawings work.
    const saved = load(FAV_KEY, []);
    let tries = 0;
    const restore = () => {
      if (ws !== workspace) return;
      const d = ws.chart && ws.chart.drawings;
      if (d && d.supported) {
        restored = true;
        if (saved.length) d.setFavorites(saved); else onFavorites(d.favorites());
      } else if (tries++ < 100) setTimeout(restore, 100);
      else restored = true;
    };
    restore();
  }

  window.addEventListener("resize", place);
  window.TradeXFavBar = { mount, state: () => ({ favs: [...favs], active, hidden: !bar || bar.hidden, pos }) };
})();
