"""Kraken public OHLC (no account, no key). USD pairs; also works for users in the US.
Limit: Kraken only returns the newest 720 candles per timeframe, so scroll-back stops there."""
from .base import get_json, bar

MANIFEST = {"id": "kraken", "name": "Kraken", "short": "KRK", "needs": "none", "official": True, "default_on": True,
            "markets": "crypto spot (USD pairs), newest 720 candles per timeframe",
            "terms": "https://docs.kraken.com/api/", "notice": ""}
REST = "https://api.kraken.com/0/public/OHLC"
TF = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440}
PAIRS = [("XBTUSD", "BTC", "Bitcoin"), ("ETHUSD", "ETH", "Ethereum"), ("SOLUSD", "SOL", "Solana"),
         ("XRPUSD", "XRP", "XRP"), ("XDGUSD", "DOGE", "Dogecoin"), ("ADAUSD", "ADA", "Cardano"),
         ("LINKUSD", "LINK", "Chainlink"), ("LTCUSD", "LTC", "Litecoin"), ("AVAXUSD", "AVAX", "Avalanche"),
         ("PAXGUSD", "PAXG", "Gold token PAXG (not gold spot)")]

def symbols():
    return [{"ticker": f"KRK_{p}", "name": f"{n} / US Dollar (Kraken)", "group": "crypto", "key": c + "USD"} for p, c, n in PAIRS]

def history(symbol, tf, limit=1000, to_ms=None):
    d = get_json(f"{REST}?pair={symbol[4:]}&interval={TF[tf]}")
    if d.get("error"):
        raise RuntimeError("Kraken: " + "; ".join(d["error"]))
    rows = next(v for k, v in d["result"].items() if k != "last")  # oldest first: [t, o, h, l, c, vwap, vol, n]
    if to_ms:
        rows = [r for r in rows if r[0] * 1000 <= int(to_ms)]
    return [bar(r[0], r[1], r[2], r[3], r[4], r[6]) for r in rows[-min(int(limit), 720):]]

def selftest():
    b = history("KRK_XBTUSD", "M1", 3)
    return bool(b), f"{len(b)} candles, last close {b[-1]['close'] if b else '-'}"
