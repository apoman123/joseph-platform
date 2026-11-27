"""Coinbase Exchange public candles (no account, no key). USD pairs; also works for users in the US."""
import time
from .base import get_json, bar, resample, TF_SEC

MANIFEST = {"id": "coinbase", "name": "Coinbase", "short": "CB", "needs": "none", "official": True, "default_on": True,
            "markets": "crypto spot (USD pairs)", "terms": "https://docs.cdp.coinbase.com/exchange/", "notice": ""}
REST = "https://api.exchange.coinbase.com/products/"
NATIVE = {"M1": 60, "M5": 300, "M15": 900, "H1": 3600, "D1": 86400}
BUILT = {"M30": "M15", "H4": "H1"}  # Coinbase has no 30m / 4h candles: built from 15m / 1h
COINS = [("BTC", "Bitcoin"), ("ETH", "Ethereum"), ("SOL", "Solana"), ("XRP", "XRP"), ("DOGE", "Dogecoin"),
         ("ADA", "Cardano"), ("LINK", "Chainlink"), ("LTC", "Litecoin"), ("AVAX", "Avalanche"),
         ("PAXG", "Gold token PAXG (not gold spot)")]

def symbols():
    return [{"ticker": f"CB_{c}-USD", "name": f"{n} / US Dollar (Coinbase)", "group": "crypto", "key": c + "USD"} for c, n in COINS]

def _iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))

def _native(prod, tf, want, to_s):
    g, out = NATIVE[tf], []
    end = (to_s if to_s else int(time.time())) + g
    for _ in range(40):  # 300 candles per call
        if len(out) >= want:
            break
        start = end - 300 * g
        rows = get_json(f"{REST}{prod}/candles?granularity={g}&start={_iso(start)}&end={_iso(end)}")
        if isinstance(rows, dict):
            raise RuntimeError(f"Coinbase: {rows.get('message')}")
        if not rows:
            break
        out += rows  # newest first: [time, low, high, open, close, volume]
        end = int(rows[-1][0]) - g
    out = sorted({int(r[0]): r for r in out}.values(), key=lambda r: r[0])
    if to_s:
        out = [r for r in out if r[0] <= to_s]
    return [bar(r[0], r[3], r[2], r[1], r[4], r[5]) for r in out[-want:]]

def history(symbol, tf, limit=1000, to_ms=None):
    prod, want = symbol[3:], min(int(limit), 5000)
    to_s = int(to_ms) // 1000 if to_ms else None
    if tf in BUILT:
        k = TF_SEC[tf] // TF_SEC[BUILT[tf]]
        return resample(_native(prod, BUILT[tf], want * k + k, to_s), BUILT[tf], tf)[-want:]
    return _native(prod, tf, want, to_s)

def selftest():
    b = history("CB_BTC-USD", "M1", 3)
    return bool(b), f"{len(b)} candles, last close {b[-1]['close'] if b else '-'}"
