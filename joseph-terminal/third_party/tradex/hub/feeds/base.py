"""Shared helpers for chart-source plugins (see feeds/__init__.py for the plugin contract)."""
import asyncio, json, ssl, time, urllib.request

TF_SEC = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800, "H1": 3600, "H4": 14400, "D1": 86400}
UA = {"User-Agent": "TradeX/1.0 (+https://github.com/saifullahshafin/open-source-tradingview)"}

def _ssl_context():
    """Use the operating system's certificate store when truststore is installed (fixes TLS errors on some PCs)."""
    try:
        import truststore
        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except Exception:
        return ssl.create_default_context()

_CTX = _ssl_context()

def get_json(url, timeout=15, data=None, headers=None):
    h = dict(UA, **(headers or {}))
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=h)
    with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as r:
        return json.loads(r.read())

def bar(t, o, h, l, c, v=0.0):
    return {"time": int(t), "open": float(o), "high": float(h), "low": float(l), "close": float(c), "volume": float(v)}

def resample(bars, src_tf, dst_tf):
    """Join bars of src_tf into dst_tf (for exchanges without 30m or 4h candles). Bars must be oldest first."""
    span, out = TF_SEC[dst_tf], []
    for b in bars:
        start = b["time"] - b["time"] % span
        if out and out[-1]["time"] == start:
            x = out[-1]
            x["high"] = max(x["high"], b["high"]); x["low"] = min(x["low"], b["low"])
            x["close"] = b["close"]; x["volume"] += b["volume"]
        else:
            out.append(dict(b, time=start))
    return out

async def poll_stream(ws, history_fn, symbol, tf, source, history=1000, every=2.0):
    """Send history, then poll the newest bars every `every` seconds and send each change as a live bar.
    Works for any REST-only source; plugins with a websocket can provide their own stream()."""
    try:
        bars = await asyncio.to_thread(history_fn, symbol, tf, history, None)
    except Exception as e:
        await ws.send(json.dumps({"type": "error", "msg": f"{source} history failed: {e}"}))
        return
    if not bars:
        await ws.send(json.dumps({"type": "error", "msg": f"{source}: no candles for {symbol} {tf}"}))
        return
    await ws.send(json.dumps({"type": "history", "bars": bars, "source": source, "symbol": symbol, "tf": tf}))
    last = dict(bars[-1])
    fails = 0
    while True:
        await asyncio.sleep(every)
        try:
            new = await asyncio.to_thread(history_fn, symbol, tf, 3, None)
            fails = 0
        except Exception as e:
            fails += 1
            if fails in (3, 30):
                await ws.send(json.dumps({"type": "status", "msg": f"{source} not answering ({e.__class__.__name__}); retrying"}))
            await asyncio.sleep(min(30, every * fails))
            continue
        for b in new:
            if b["time"] < last["time"] or b == last:
                continue
            last = dict(b)
            now = int(time.time() * 1000)
            await ws.send(json.dumps({"type": "bar", "bar": b, "sent": now, "trade_ms": now, "lag_ms": None}))
