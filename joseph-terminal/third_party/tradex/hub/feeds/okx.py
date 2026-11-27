"""OKX public market data (no account, no key). Spot pairs quoted in USDT."""
from .base import get_json, bar

MANIFEST = {"id": "okx", "name": "OKX", "short": "OKX", "needs": "none", "official": True, "default_on": True,
            "markets": "crypto spot (USDT pairs)", "terms": "https://www.okx.com/docs-v5/en/", "notice": ""}
REST = "https://www.okx.com/api/v5/market/"
TF = {"M1": "1m", "M5": "5m", "M15": "15m", "M30": "30m", "H1": "1H", "H4": "4H", "D1": "1Dutc"}
COINS = [("BTC", "Bitcoin"), ("ETH", "Ethereum"), ("SOL", "Solana"), ("XRP", "XRP"), ("DOGE", "Dogecoin"),
         ("ADA", "Cardano"), ("LINK", "Chainlink"), ("LTC", "Litecoin"), ("AVAX", "Avalanche"), ("BNB", "BNB"),
         ("TON", "Toncoin"), ("PAXG", "Gold token PAXG (not gold spot)")]

def symbols():
    return [{"ticker": f"OKX_{c}-USDT", "name": f"{n} / Tether (OKX)", "group": "crypto", "key": c + "USD"} for c, n in COINS]

def _rows(path, inst, tf, limit, after=None):
    url = f"{REST}{path}?instId={inst}&bar={TF[tf]}&limit={limit}" + (f"&after={after}" if after else "")
    d = get_json(url)
    if d.get("code") != "0":
        raise RuntimeError(f"OKX: {d.get('msg') or d.get('code')}")
    return d["data"]  # newest first: [ts, o, h, l, c, vol, ...]

def history(symbol, tf, limit=1000, to_ms=None):
    inst, want = symbol[4:], min(int(limit), 5000)
    out, after = [], (int(to_ms) + 1 if to_ms else None)
    if not after:  # newest candles (includes the forming one)
        out = _rows("candles", inst, tf, min(300, want))
        after = int(out[-1][0]) if out else None
    while len(out) < want and after:
        rows = _rows("history-candles", inst, tf, 100, after)
        if not rows:
            break
        out += rows
        after = int(rows[-1][0])
    out = out[:want]
    return [bar(int(r[0]) // 1000, r[1], r[2], r[3], r[4], r[5]) for r in reversed(out)]

def selftest():
    b = history("OKX_BTC-USDT", "M1", 3)
    return bool(b), f"{len(b)} candles, last close {b[-1]['close'] if b else '-'}"
