// TradeX Symbol Search (2026-11-24): the TradingView-style window for picking an instrument.
// Opens from the symbol button in the top bar, or by just typing a letter while on the chart.
// Tabs = categories (All, Stocks, Funds, Forex, Crypto, Indices, Commodities), a source (broker) filter,
// ranked results from the server (GET /api/instruments/search, the same call agents use), recent symbols,
// paging as you scroll. A row click opens it at the current broker when offered; a broker tag opens it there.
// Picking still goes through the hub's st + push(), so broker memory, variants, layouts and the cover all work.
(function () {
  const TYPE = { stock: "stock", fund: "fund", forex: "forex", crypto: "crypto", index: "index", commodity: "commodity" };
  const CUR = { USD: "$", EUR: "€", GBP: "£", JPY: "¥", CHF: "Fr", AUD: "A$", CAD: "C$", NZD: "N$", CNH: "¥", HKD: "HK", SGD: "S$", ZAR: "R", MXN: "M$", TRY: "₺", SEK: "kr", NOK: "kr", DKK: "kr", PLN: "zł", INR: "₹" };
  const METAL = { XAU: ["Au", "#b8902c"], XAG: ["Ag", "#8c9196"], XPT: ["Pt", "#7d8a92"], XPD: ["Pd", "#8a8378"], XCU: ["Cu", "#b06a3b"], XAL: ["Al", "#86909a"], XNI: ["Ni", "#7f8a7a"], XPB: ["Pb", "#6f7480"], XZN: ["Zn", "#80868c"] };
  const PAGE = 60, RECENT_KEY = "tradexRecentSymbols", PREF_KEY = "tradexSymSearch";
  let S = { cat: "", broker: "", q: "", rows: [], total: 0, seq: 0, hi: 0, busy: false, timer: null };
  try { Object.assign(S, JSON.parse(localStorage.getItem(PREF_KEY) || "{}"), { q: "" }); } catch (_) {}
  const savePref = () => { try { localStorage.setItem(PREF_KEY, JSON.stringify({ cat: S.cat, broker: S.broker })); } catch (_) {} };
  const recent = () => { try { return JSON.parse(localStorage.getItem(RECENT_KEY) || "[]"); } catch (_) { return []; } };
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
  const D = () => $("symDlg");

  function build() {
    const d = document.createElement("div"); d.id = "symDlg"; d.hidden = true;
    d.innerHTML = `<div class="dlg sy" role="dialog" aria-label="Symbol Search">
      <div class="dlg-h"><span>Symbol Search</span><button class="dlg-x" title="Close (Esc)">&times;</button></div>
      <div class="dlg-s sy-s"><input placeholder="Symbol, name, or keyword" spellcheck="false" autocomplete="off" aria-label="Search symbols"><button class="sy-clear" title="Clear" hidden>&times;</button></div>
      <div class="sy-tabs" role="tablist"></div>
      <div class="sy-bar"><span class="sy-count"></span><select class="sy-src" title="Price source (broker)"></select></div>
      <div class="sy-list" tabindex="-1"></div>
      <div class="sy-foot"><span>Simply start typing while on the chart to pull up this search box</span><span class="sy-keys"><kbd>&uarr;</kbd><kbd>&darr;</kbd> move <kbd>Enter</kbd> open <kbd>Esc</kbd> close</span></div></div>`;
    document.body.appendChild(d);
    d.onmousedown = (e) => { if (e.target === d) close(); };
    d.querySelector(".dlg-x").onclick = close;
    const inp = d.querySelector(".sy-s input"), clr = d.querySelector(".sy-clear");
    inp.oninput = () => { S.q = inp.value.trim(); clr.hidden = !inp.value; clearTimeout(S.timer); S.timer = setTimeout(() => load(true), 70); };
    clr.onclick = () => { inp.value = ""; S.q = ""; clr.hidden = true; inp.focus(); load(true); };
    inp.onkeydown = onKey;
    const tabs = d.querySelector(".sy-tabs");
    [["", "All"], ...((DATA.cats || []).map((c) => [c.id, c.name]))].forEach(([id, n]) => {
      const b = document.createElement("button"); b.textContent = n; b.dataset.id = id; b.setAttribute("role", "tab");
      b.onclick = () => { S.cat = id; savePref(); paintTabs(); load(true); inp.focus(); };
      tabs.appendChild(b);
    });
    const src = d.querySelector(".sy-src");
    src.add(new Option("All sources", ""));
    DATA.brokers.forEach((b) => src.add(new Option(b.name, b.id)));
    src.onchange = () => { S.broker = src.value; savePref(); load(true); inp.focus(); };
    const list = d.querySelector(".sy-list");
    list.onscroll = () => { if (list.scrollTop + list.clientHeight > list.scrollHeight - 200) more(); };
    return d;
  }

  // onPick (optional, 2026-11-24): "pick mode" for the + Compare button. The chosen symbol is handed to onPick
  // ({key, ticker, broker}) instead of switching the chart.
  function open(text, onPick) {
    if (!DATA.brokers.length) return;  // instruments not loaded yet
    const d = D() || build();
    const inp = d.querySelector(".sy-s input");
    S.onPick = typeof onPick === "function" ? onPick : null;
    inp.placeholder = S.onPick ? "Compare with: symbol, name, or keyword" : "Symbol, name, or keyword";
    inp.value = text || ""; S.q = inp.value.trim(); d.querySelector(".sy-clear").hidden = !inp.value;
    if (S.broker && !DATA.brokers.some((b) => b.id === S.broker)) S.broker = "";
    d.querySelector(".sy-src").value = S.broker;
    d.hidden = false; paintTabs(); load(true);
    // Opened by a key typed on the chart: focus is still inside the chart iframe, so take it back first.
    const grab = () => { if (document.activeElement === $("frame")) $("frame").blur(); window.focus(); inp.focus(); const n = inp.value.length; inp.setSelectionRange(n, n); };
    // A chart that just finished loading can pull focus back a moment later, so take it a few more times.
    grab(); [0, 80, 250, 600].forEach((ms) => setTimeout(() => { if (isOpen() && document.activeElement !== inp) grab(); }, ms));
  }
  function close() { const d = D(); if (d) d.hidden = true; S.onPick = null; }
  const isOpen = () => !!D() && !D().hidden;
  function paintTabs() { D().querySelectorAll(".sy-tabs button").forEach((b) => { const on = b.dataset.id === S.cat; b.classList.toggle("on", on); b.setAttribute("aria-selected", on); }); }

  // One request per change; an older answer that arrives late is dropped (sequence number).
  async function load(reset) {
    if (reset) { S.rows = []; S.total = 0; S.hi = 0; }
    const seq = ++S.seq; S.busy = true;
    const u = `/api/instruments/search?q=${encodeURIComponent(S.q)}&cat=${S.cat}&broker=${S.broker}&offset=${S.rows.length}&limit=${PAGE}`;
    let r;
    try { r = await (await fetch(u)).json(); }
    catch (e) { if (seq === S.seq) { S.busy = false; paintList("Chart server not answering: start START_CHART_LAB.bat"); } return; }
    if (seq !== S.seq) return;
    S.busy = false; S.total = r.total; S.rows = S.rows.concat(r.results);
    paintList();
  }
  function more() { if (!S.busy && S.rows.length < S.total) load(false); }

  function paintList(err) {
    const d = D(), L = d.querySelector(".sy-list"), keep = L.scrollTop, firstPage = S.rows.length <= PAGE;
    const catName = S.cat ? (DATA.cats.find((c) => c.id === S.cat) || {}).name.toLowerCase() : "symbols";
    d.querySelector(".sy-count").textContent = err ? "" : `${S.total.toLocaleString()} ${catName}${S.q ? ` matching "${S.q}"` : ""}`;
    L.innerHTML = "";
    if (err) { L.innerHTML = `<div class="dlg-empty">${esc(err)}</div>`; return; }
    // Recent symbols on top of an empty search (All tab, any source that has them).
    if (!S.q && !S.cat) {
      const rec = recent().map((k) => inst(k)).filter((i) => i && (!S.broker || i.brokers[S.broker]));
      if (rec.length) {
        L.insertAdjacentHTML("beforeend", `<div class="sy-gh">Recent</div>`);
        rec.forEach((i) => L.appendChild(row(i, true)));
        L.insertAdjacentHTML("beforeend", `<div class="sy-gh">All symbols</div>`);
      }
    }
    S.rows.forEach((i) => L.appendChild(row(i)));
    if (!S.rows.length) L.insertAdjacentHTML("beforeend", `<div class="dlg-empty">No symbol matches${S.q ? ` "${esc(S.q)}"` : ""}${S.cat ? " in this tab" : ""}${S.broker ? " at this source" : ""}. Try All, or All sources.</div>`);
    else if (S.rows.length < S.total) L.insertAdjacentHTML("beforeend", `<div class="sy-more">Scroll for more (${(S.total - S.rows.length).toLocaleString()} left)</div>`);
    markHi();
    if (!firstPage) L.scrollTop = keep;
  }

  function row(i, isRecent) {
    const r = document.createElement("div"); r.className = "sy-row" + (i.key === st.key ? " cur" : ""); r.dataset.k = i.key;
    const tags = DATA.brokers.filter((b) => i.brokers[b.id]).map((b) =>
      `<button class="sy-tag${b.id === st.broker && i.key === st.key ? " on" : ""}" data-b="${b.id}" title="Open at ${esc(b.name)}">${shortOf(b.id)}</button>`).join("");
    r.innerHTML = `${logo(i)}<span class="sy-sym">${hl(i.key)}</span><span class="sy-desc">${hl(i.name)}</span>`
      + `<span class="sy-type">${TYPE[i.cat] || ""}</span><span class="sy-tags">${tags}</span>`;
    r.onclick = (e) => { const t = e.target.closest(".sy-tag"); choose(i.key, t ? t.dataset.b : null); };
    r.onmousemove = () => { const rows = [...D().querySelectorAll(".sy-row")], n = rows.indexOf(r); if (n !== S.hi) { S.hi = n; markHi(false); } };
    if (isRecent) r.classList.add("rec");
    return r;
  }
  // Highlight every query word inside the symbol and the description.
  function hl(text) {
    const words = S.q.toUpperCase().split(/\s+/).filter(Boolean); if (!words.length) return esc(text);
    const up = text.toUpperCase(), mark = new Array(text.length).fill(false);
    words.forEach((w) => { let p = up.indexOf(w); while (p >= 0) { for (let n = p; n < p + w.length; n++) mark[n] = true; p = up.indexOf(w, p + w.length); } });
    let out = "", on = false;
    for (let n = 0; n < text.length; n++) { if (mark[n] !== on) { out += mark[n] ? "<mark>" : "</mark>"; on = mark[n]; } out += esc(text[n]); }
    return out + (on ? "</mark>" : "");
  }
  // Logos are drawn locally (no outside images): two currency coins for forex, element badges for metals.
  function hue(s) { let h = 0; for (const c of s) h = (h * 31 + c.charCodeAt(0)) % 360; return h; }
  function coin(text, bg, cls) { return `<span class="sy-coin${cls ? " " + cls : ""}" style="background:${bg}">${esc(text)}</span>`; }
  function logo(i) {
    const k = i.key;
    if (i.cat === "forex" && /^[A-Z]{6}$/.test(k)) {
      const a = k.slice(0, 3), b = k.slice(3);
      return `<span class="sy-logo two">${coin(CUR[a] || a[0], `hsl(${hue(a)} 38% 42%)`, "a")}${coin(CUR[b] || b[0], `hsl(${hue(b)} 38% 42%)`, "b")}</span>`;
    }
    if (i.cat === "commodity") {
      const m = METAL[k.slice(0, 3)];
      if (m) return `<span class="sy-logo">${coin(m[0], m[1])}</span>`;
      return `<span class="sy-logo">${coin(/GAS|XNG/.test(k + i.name.toUpperCase()) ? "Gas" : "Oil", "#5b5146", "sm")}</span>`;
    }
    if (i.cat === "crypto") {
      const base = k.replace(/USD$/, ""), sym = { BTC: "₿", ETH: "Ξ" }[base];
      return `<span class="sy-logo">${coin(sym || base.replace(/^\d+/, "")[0] || base[0], base === "BTC" ? "#c77a1e" : base === "ETH" ? "#5a6bb0" : `hsl(${hue(base)} 40% 42%)`)}</span>`;
    }
    if (i.cat === "index") return `<span class="sy-logo">${coin(k.replace(/^OTC_/, "").slice(0, 2), "#4b5563", "sm")}</span>`;
    return `<span class="sy-logo">${coin(k[0], `hsl(${hue(k)} 36% ${i.cat === "fund" ? 36 : 42}%)`, i.cat === "fund" ? "sq" : "")}</span>`;
  }

  function markHi(scroll = true) {
    const rows = D().querySelectorAll(".sy-row");
    rows.forEach((r, n) => r.classList.toggle("hi", n === S.hi));
    if (scroll && rows[S.hi]) rows[S.hi].scrollIntoView({ block: "nearest" });
  }
  function onKey(e) {
    const rows = D().querySelectorAll(".sy-row");
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault(); if (!rows.length) return;
      S.hi = Math.max(0, Math.min(rows.length - 1, S.hi + (e.key === "ArrowDown" ? 1 : -1)));
      if (S.hi >= rows.length - 3) more();
      markHi();
    } else if (e.key === "Enter") { e.preventDefault(); if (rows[S.hi]) choose(rows[S.hi].dataset.k, null); }
    else if (e.key === "Escape") { e.preventDefault(); close(); }
  }

  function choose(k, broker) {
    const i = inst(k); if (!i) return;
    if (S.onPick) {  // compare: hand the symbol over, keep the chart as it is
      const fn = S.onPick, b = (broker && i.brokers[broker]) ? broker : (i.brokers[st.broker] ? st.broker : Object.keys(i.brokers)[0]);
      close(); fn({ key: k, broker: b, ticker: i.brokers[b][0].ticker }); $("frame").focus(); return;
    }
    const rec = [k, ...recent().filter((x) => x !== k)].slice(0, 8);
    try { localStorage.setItem(RECENT_KEY, JSON.stringify(rec)); } catch (_) {}
    close();
    if (broker && i.brokers[broker]) { Object.assign(st, { key: k, broker, ticker: i.brokers[broker][0].ticker }); push(); }
    else chooseInstrument(k);
    $("frame").focus();
  }

  // Typing a letter anywhere on the hub (not in a field, no other window open) opens the search with that letter.
  const otherModal = () => ["indDlg", "alDlg"].some((id) => $(id) && !$(id).hidden) || ($("lyMenu") && !$("lyMenu").hidden);
  function typed(key) {
    if (otherModal()) return false;
    if (isOpen()) {  // a letter that still reached the chart while the window opens: add it to the query
      const inp = D().querySelector(".sy-s input"); if (document.activeElement === inp) return false;
      inp.value += key; inp.dispatchEvent(new Event("input")); inp.focus(); return true;
    }
    open(key); return true;
  }
  document.addEventListener("keydown", (e) => {
    if (isOpen() && e.key === "Escape") { close(); return; }
    if (e.ctrlKey || e.altKey || e.metaKey || e.isComposing || !/^[a-zA-Z]$/.test(e.key)) return;
    const a = document.activeElement;
    if (a && (/INPUT|TEXTAREA|SELECT/.test(a.tagName) || a.isContentEditable) && a !== $("frame")) return;
    if (typed(e.key)) e.preventDefault();
  });
  window.addEventListener("message", (e) => {
    if (e.origin === location.origin && e.data && e.data.evt === "symsearch") typed(e.data.key || "");
  });
  $("symBtn").onclick = () => open("");
  if ($("cmpBtn")) $("cmpBtn").onclick = () => open("", window.addCompare);  // + Compare (ind_dialog.js)
  window.openSymDlg = open;
  window.closeSymDlg = close;
})();
