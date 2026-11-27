// TradeX web beta: the visitor's own browser fetches free exchange data (no TradeX server, no key, no cost).
// One adapter per feed, same contract as the desktop server's /api/bars and live stream:
//   history(ticker, tf, limit, toMs) -> candles oldest first, time in epoch SECONDS, at most `limit`, none newer than toMs
//   live(ticker, tf, onBar, onStatus) -> stop()   (onBar gets the forming candle, time in seconds)
// Desktop equivalents: hub/server.py (binance, bybit, deriv) and hub/feeds/*.py (coinbase, kraken, okx).
(function () {
  "use strict";
  const NativeWS = window.WebSocket;
  const SEC = { M1: 60, M5: 300, M15: 900, M30: 1800, H1: 3600, H4: 14400, D1: 86400 };
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const health = {};   // feed id -> {ok, msg, at}: the status lights
  const mark = (id, ok, msg) => { health[id] = { ok, msg, at: Date.now() }; };
  async function getJSON(id, url) {
    let r;
    try { r = await fetch(url, { cache: "no-store" }); }
    catch (e) { mark(id, false, "not reachable from this browser (blocked in your country or network?)"); throw new Error(`${id}: not reachable from your browser`); }
    if (!r.ok) { mark(id, false, `HTTP ${r.status}`); throw new Error(`${id}: HTTP ${r.status}${r.status === 451 || r.status === 403 ? " (not available in your country)" : ""}`); }
    return r.json();
  }
  const bar = (t, o, h, l, c, v) => ({ time: Math.floor(+t), open: +o, high: +h, low: +l, close: +c, volume: +(v || 0) });
  const cut = (bars, limit, toMs) => { if (toMs) bars = bars.filter((b) => b.time * 1000 <= toMs); return bars.slice(-limit); };
  function resample(bars, tf) {
    const span = SEC[tf], out = [];
    for (const b of bars) {
      const t = b.time - (b.time % span), last = out[out.length - 1];
      if (last && last.time === t) { last.high = Math.max(last.high, b.high); last.low = Math.min(last.low, b.low); last.close = b.close; last.volume += b.volume; }
      else out.push({ ...b, time: t });
    }
    return out;
  }
  // Polling live feed (same as the desktop plugins' poll_stream): newest candles every few seconds.
  function pollLive(feed, ticker, tf, onBar, every = 3000) {
    let on = true;
    (async () => { while (on) { try { const b = await feed.history(ticker, tf, 2); if (on && b.length) onBar(b[b.length - 1]); } catch (_) {} await sleep(every); } })();
    return () => { on = false; };
  }
  // A WebSocket that reconnects by itself until stopped.
  function socket(id, url, onOpen, onMsg, onStatus, pingMs, pingMsg) {
    let ws, on = true, timer, tries = 0;
    const open = () => {
      if (!on) return;
      ws = new NativeWS(url);
      ws.onopen = () => { tries = 0; mark(id, true, "live"); onOpen(ws); if (pingMs) timer = setInterval(() => { try { ws.send(pingMsg); } catch (_) {} }, pingMs); };
      ws.onmessage = (e) => { try { onMsg(JSON.parse(e.data), ws); } catch (_) {} };
      ws.onclose = () => { clearInterval(timer); if (!on) return; tries++; onStatus && onStatus(`${id}: reconnecting`); setTimeout(open, Math.min(30000, 1000 * 2 ** tries)); };
      ws.onerror = () => { mark(id, false, "live stream blocked or down"); };
    };
    open();
    return () => { on = false; clearInterval(timer); try { ws.close(); } catch (_) {} };
  }

  // ---------------- Binance (spot via the public market-data hosts, USD-M futures incl. gold/silver/stocks) ----------------
  const BIN_TF = { M1: "1m", M5: "5m", M15: "15m", M30: "30m", H1: "1h", H4: "4h", D1: "1d" };
  const BIN_ALIAS = { XAUUSD: "BINF_XAUUSDT", XAGUSD: "BINF_XAGUSDT" };
  function bsym(t) { let s = String(t).toUpperCase().split(":").pop(); s = BIN_ALIAS[s] || s; return s.startsWith("BINF_") ? [s.slice(5), true] : [s.endsWith("USD") ? s + "T" : s, false]; }
  const binance = {
    async history(t, tf, limit = 1000, toMs) {
      const [s, fut] = bsym(t);
      const base = fut ? "https://fapi.binance.com/fapi/v1/klines" : "https://data-api.binance.vision/api/v3/klines";
      const rows = await getJSON("binance", `${base}?symbol=${s}&interval=${BIN_TF[tf]}&limit=${Math.min(limit, fut ? 1500 : 1000)}` + (toMs ? `&endTime=${Math.floor(toMs)}` : ""));
      mark("binance", true, "ok");
      return rows.map((k) => bar(k[0] / 1000, k[1], k[2], k[3], k[4], k[5]));
    },
    live(t, tf, onBar, onStatus) {
      const [s, fut] = bsym(t);
      const url = (fut ? "wss://fstream.binance.com/market/ws/" : "wss://data-stream.binance.vision/ws/") + `${s.toLowerCase()}@kline_${BIN_TF[tf]}`;
      return socket("binance", url, () => {}, (m) => { const k = m.k; if (k) onBar(bar(k.t / 1000, k.o, k.h, k.l, k.c, k.v), m.E); }, onStatus);
    },
  };

  // ---------------- Bybit (public v5): BYBIT_<SYM> linear, BYBIT_S_<SYM> spot, BYBIT_I_<SYM> inverse ----------------
  const BY_TF = { M1: "1", M5: "5", M15: "15", M30: "30", H1: "60", H4: "240", D1: "D" };
  function bparse(t) { let s = String(t).slice(6), cat = "linear"; if (s.startsWith("S_")) { s = s.slice(2); cat = "spot"; } else if (s.startsWith("I_")) { s = s.slice(2); cat = "inverse"; } return [cat, s]; }
  const bybit = {
    async history(t, tf, limit = 1000, toMs) {
      const [cat, s] = bparse(t);
      const d = await getJSON("bybit", `https://api.bybit.com/v5/market/kline?category=${cat}&symbol=${s}&interval=${BY_TF[tf]}&limit=${Math.min(limit, 1000)}` + (toMs ? `&end=${Math.floor(toMs)}` : ""));
      if (d.retCode) { mark("bybit", false, d.retMsg); throw new Error("Bybit: " + d.retMsg); }
      mark("bybit", true, "ok");
      return d.result.list.map((k) => bar(k[0] / 1000, k[1], k[2], k[3], k[4], k[5])).reverse();
    },
    live(t, tf, onBar, onStatus) {
      const [cat, s] = bparse(t), topic = `kline.${BY_TF[tf]}.${s}`;
      return socket("bybit", `wss://stream.bybit.com/v5/public/${cat}`, (ws) => ws.send(JSON.stringify({ op: "subscribe", args: [topic] })),
        (m) => { if (m.topic === topic) for (const k of m.data || []) onBar(bar(k.start / 1000, k.open, k.high, k.low, k.close, k.volume), m.ts); },
        onStatus, 20000, JSON.stringify({ op: "ping" }));
    },
  };

  // ---------------- Deriv (forex, gold, indices; public WebSocket, no account) ----------------
  const DERIV_URL = "wss://api.derivws.com/trading/v1/options/ws/public";
  let dSock = null, dReady = null, dId = 0;
  const dWait = new Map();
  function derivConn() {
    if (dSock && dSock.readyState === 1) return Promise.resolve(dSock);
    if (dReady) return dReady;
    dReady = new Promise((res, rej) => {
      const s = new NativeWS(DERIV_URL);
      const to = setTimeout(() => { rej(new Error("Deriv: no answer")); try { s.close(); } catch (_) {} }, 12000);
      s.onopen = () => { clearTimeout(to); dSock = s; dReady = null; mark("deriv", true, "ok"); res(s); };
      s.onmessage = (e) => { const m = JSON.parse(e.data), w = dWait.get(m.req_id); if (w) { dWait.delete(m.req_id); w(m); } };
      s.onerror = () => { mark("deriv", false, "not reachable from this browser"); };
      s.onclose = () => { clearTimeout(to); dSock = null; dReady = null; for (const w of dWait.values()) w({ error: { message: "connection closed" } }); dWait.clear(); rej(new Error("Deriv: connection closed")); };
    });
    return dReady;
  }
  async function derivAsk(req) {
    const s = await derivConn(), id = ++dId;
    return new Promise((res) => { dWait.set(id, res); s.send(JSON.stringify({ ...req, req_id: id })); setTimeout(() => { if (dWait.delete(id)) res({ error: { message: "timeout" } }); }, 20000); });
  }
  const dBars = (r) => { if (r.error) throw new Error("Deriv: " + r.error.message); return (r.candles || []).map((c) => bar(c.epoch, c.open, c.high, c.low, c.close, 0)); };
  const deriv = {
    async history(t, tf, limit = 1000, toMs) {
      const g = SEC[tf];
      let end = toMs ? Math.floor(toMs / 1000) : Math.floor(Date.now() / 1000), got = new Map(), empty = 0;
      if (limit <= 1000) return dBars(await derivAsk({ ticks_history: t, style: "candles", granularity: g, count: limit, end: toMs ? String(end) : "latest", adjust_start_time: 1 }));
      end -= end % g;
      // Back-to-back windows of 1,000 candles, up to 5 at once on the one socket (as the desktop server does);
      // closed-market windows (weekends) come back empty: stop after 4 empty rounds.
      while (got.size < limit && empty < 4) {
        const before = got.size, n = Math.max(1, Math.min(5, Math.ceil((limit - got.size) / 1000)));
        const wins = Array.from({ length: n }, (_, k) => [end - (k + 1) * 1000 * g + g, end - k * 1000 * g]);
        const parts = await Promise.all(wins.map(([a, b]) => derivAsk({ ticks_history: t, style: "candles", granularity: g, start: a, end: b, count: 1000 }).then(dBars)));
        for (const part of parts) for (const b of part) got.set(b.time, b);
        empty = got.size === before ? empty + 1 : 0;
        end -= n * 1000 * g;
      }
      return [...got.values()].sort((a, b) => a.time - b.time).slice(-limit);
    },
    live(t, tf, onBar, onStatus) {
      const g = SEC[tf];
      return socket("deriv", DERIV_URL, (ws) => ws.send(JSON.stringify({ ticks_history: t, style: "candles", granularity: g, count: 1, end: "latest", subscribe: 1 })),
        (m) => { if (m.error) { onStatus && onStatus("Deriv: " + m.error.message); return; } const o = m.ohlc; if (o) onBar(bar(o.open_time, o.open, o.high, o.low, o.close, 0), o.epoch * 1000); },
        onStatus, 30000, JSON.stringify({ ping: 1 }));
    },
  };

  // ---------------- Coinbase (CB_<BASE>-USD): 300 candles per call; no 30m / 4h, built from 15m / 1h ----------------
  const CB_G = { M1: 60, M5: 300, M15: 900, H1: 3600, D1: 86400 }, CB_BUILT = { M30: "M15", H4: "H1" };
  const iso = (s) => new Date(s * 1000).toISOString().replace(/\.\d+Z$/, "Z");
  async function cbNative(prod, tf, want, toS) {
    const g = CB_G[tf], seen = new Map();
    let end = (toS || Math.floor(Date.now() / 1000)) + g;
    for (let i = 0; i < 40 && seen.size < want; i++) {
      const rows = await getJSON("coinbase", `https://api.exchange.coinbase.com/products/${prod}/candles?granularity=${g}&start=${iso(end - 300 * g)}&end=${iso(end)}`);
      if (!Array.isArray(rows)) throw new Error("Coinbase: " + (rows.message || "error"));
      if (!rows.length) break;
      for (const r of rows) seen.set(r[0], bar(r[0], r[3], r[2], r[1], r[4], r[5]));   // [time, low, high, open, close, volume]
      end = rows[rows.length - 1][0] - g;
    }
    mark("coinbase", true, "ok");
    return cut([...seen.values()].sort((a, b) => a.time - b.time), want, toS && toS * 1000);
  }
  const coinbase = {
    async history(t, tf, limit = 1000, toMs) {
      const prod = String(t).slice(3), want = Math.min(limit, 5000), toS = toMs ? Math.floor(toMs / 1000) : null;
      if (CB_BUILT[tf]) { const k = SEC[tf] / SEC[CB_BUILT[tf]]; return resample(await cbNative(prod, CB_BUILT[tf], want * k + k, toS), tf).slice(-want); }
      return cbNative(prod, tf, want, toS);
    },
    live(t, tf, onBar) { return pollLive(coinbase, t, tf, onBar); },
  };

  // ---------------- Kraken (KRK_<PAIR>): newest 720 candles per timeframe only ----------------
  const KR_TF = { M1: 1, M5: 5, M15: 15, M30: 30, H1: 60, H4: 240, D1: 1440 };
  const kraken = {
    async history(t, tf, limit = 1000, toMs) {
      const d = await getJSON("kraken", `https://api.kraken.com/0/public/OHLC?pair=${String(t).slice(4)}&interval=${KR_TF[tf]}`);
      if (d.error && d.error.length) { mark("kraken", false, d.error.join("; ")); throw new Error("Kraken: " + d.error.join("; ")); }
      mark("kraken", true, "ok");
      const rows = Object.entries(d.result).find(([k]) => k !== "last")[1];   // oldest first: [t, o, h, l, c, vwap, vol, n]
      return cut(rows.map((r) => bar(r[0], r[1], r[2], r[3], r[4], r[6])), Math.min(limit, 720), toMs);
    },
    // Live: ask only for candles since the last one (a full 720-candle reply every few seconds trips Kraken's rate limit).
    live(t, tf, onBar) {
      let on = true, since = 0;
      (async () => { while (on) {
        try {
          const d = await getJSON("kraken", `https://api.kraken.com/0/public/OHLC?pair=${String(t).slice(4)}&interval=${KR_TF[tf]}` + (since ? `&since=${since}` : ""));
          const rows = d.result ? Object.entries(d.result).find(([k]) => k !== "last")[1] : [];
          if (on && rows.length) { const r = rows[rows.length - 1]; since = r[0] - 1; mark("kraken", true, "ok"); onBar(bar(r[0], r[1], r[2], r[3], r[4], r[6])); }
        } catch (_) {}
        await sleep(6000);
      } })();
      return () => { on = false; };
    },
  };

  // ---------------- OKX (OKX_<BASE>-USDT): newest 300, then 100 per page backwards ----------------
  const OKX_TF = { M1: "1m", M5: "5m", M15: "15m", M30: "30m", H1: "1H", H4: "4H", D1: "1Dutc" };
  async function okxRows(path, inst, tf, limit, after) {
    const d = await getJSON("okx", `https://www.okx.com/api/v5/market/${path}?instId=${inst}&bar=${OKX_TF[tf]}&limit=${limit}` + (after ? `&after=${after}` : ""));
    if (d.code !== "0") { mark("okx", false, d.msg || d.code); throw new Error("OKX: " + (d.msg || d.code)); }
    return d.data;   // newest first
  }
  const okx = {
    async history(t, tf, limit = 1000, toMs) {
      const inst = String(t).slice(4), want = Math.min(limit, 5000);
      let out = [], after = toMs ? Math.floor(toMs) + 1 : null;
      if (!after) { out = await okxRows("candles", inst, tf, Math.min(300, want)); after = out.length ? out[out.length - 1][0] : null; }
      for (let i = 0; i < 50 && out.length < want && after; i++) {
        const rows = await okxRows("history-candles", inst, tf, 100, after);
        if (!rows.length) break;
        out = out.concat(rows); after = rows[rows.length - 1][0];
      }
      mark("okx", true, "ok");
      return out.slice(0, want).reverse().map((r) => bar(r[0] / 1000, r[1], r[2], r[3], r[4], r[5]));
    },
    live(t, tf, onBar) { return pollLive(okx, t, tf, onBar); },
  };

  const feeds = { binance, bybit, deriv, coinbase, kraken, okx };
  // Status lights: one cheap check per feed at start, then whatever the charts saw last.
  const PROBE = {
    binance: () => binance.history("BTCUSD", "M1", 1), bybit: () => bybit.history("BYBIT_BTCUSDT", "M1", 1),
    deriv: () => deriv.history("frxEURUSD", "M1", 1), coinbase: () => coinbase.history("CB_BTC-USD", "D1", 1),
    kraken: () => kraken.history("KRK_XBTUSD", "D1", 1), okx: () => okxRows("candles", "BTC-USDT", "D1", 1).then(() => mark("okx", true, "ok")),
  };
  function probeAll() { for (const [id, f] of Object.entries(PROBE)) if (!health[id]) { health[id] = { ok: false, msg: "checking ...", at: 0 }; f().catch(() => {}); } }
  window.TXWebFeeds = { feeds, health, probeAll, SEC };
})();
