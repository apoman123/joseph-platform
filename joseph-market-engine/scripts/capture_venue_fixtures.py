"""Captures small, sanitized public-API samples used by offline tests.

Run manually (network required); never run in CI::

    uv run python scripts/capture_venue_fixtures.py

Each venue directory under ``fixtures/venues`` receives truncated JSON
responses so that adapter parsing is contract-tested without network access.
No credentials are involved: every endpoint is public market data.
"""

from __future__ import annotations

import json
import pathlib
import time
from typing import Any

import httpx

_ROOT = pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "venues"


def _save(venue: str, name: str, payload: Any) -> None:
    """Writes one fixture file.

    Args:
        venue: Venue directory name.
        name: Fixture base name.
        payload: JSON-compatible response body.
    """
    directory = _ROOT / venue
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.json").write_text(
        json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )


def _binance(client: httpx.Client) -> None:
    """Captures Binance USD-M futures samples.

    Args:
        client: HTTP client.
    """
    base = "https://fapi.binance.com/fapi/v1"
    info = client.get(f"{base}/exchangeInfo").json()
    keep = {"BTCUSDT", "ETHUSDT", "TSLAUSDT", "XAUUSDT"}
    symbols = [s for s in info["symbols"] if s["symbol"] in keep]
    settling = [s for s in info["symbols"] if s["status"] == "SETTLING"][:1]
    _save(
        "binance",
        "exchange_info",
        {
            "timezone": info["timezone"],
            "serverTime": info["serverTime"],
            "symbols": symbols + settling,
        },
    )
    params = {"symbol": "BTCUSDT"}
    _save(
        "binance",
        "klines_5m",
        client.get(
            f"{base}/klines", params={**params, "interval": "5m", "limit": 3}
        ).json(),
    )
    _save(
        "binance",
        "premium_index",
        client.get(f"{base}/premiumIndex", params=params).json(),
    )
    _save(
        "binance",
        "ticker_24hr",
        client.get(f"{base}/ticker/24hr", params=params).json(),
    )
    _save(
        "binance",
        "book_ticker",
        client.get(f"{base}/ticker/bookTicker", params=params).json(),
    )
    _save(
        "binance",
        "open_interest",
        client.get(f"{base}/openInterest", params=params).json(),
    )
    _save(
        "binance",
        "depth",
        client.get(f"{base}/depth", params={**params, "limit": 20}).json(),
    )
    _save(
        "binance", "funding_info", client.get(f"{base}/fundingInfo").json()[:5]
    )
    tickers = client.get(f"{base}/ticker/24hr").json()
    tickers = sorted(tickers, key=lambda t: -float(t["quoteVolume"]))
    _save("binance", "ticker_24hr_all", tickers[:8])


def _hyperliquid(client: httpx.Client) -> None:
    """Captures Hyperliquid samples.

    Args:
        client: HTTP client.
    """
    url = "https://api.hyperliquid.xyz/info"
    meta, ctxs = client.post(url, json={"type": "metaAndAssetCtxs"}).json()
    indices = list(range(4))
    delisted = [
        i for i, u in enumerate(meta["universe"]) if u.get("isDelisted")
    ][:1]
    indices += delisted
    _save(
        "hyperliquid",
        "meta_and_asset_ctxs",
        [
            {
                "universe": [meta["universe"][i] for i in indices],
                "collateralToken": meta.get("collateralToken"),
            },
            [ctxs[i] for i in indices],
        ],
    )
    now = int(time.time() * 1000)
    _save(
        "hyperliquid",
        "candles_5m",
        client.post(
            url,
            json={
                "type": "candleSnapshot",
                "req": {
                    "coin": "BTC",
                    "interval": "5m",
                    "startTime": now - 900_000,
                    "endTime": now,
                },
            },
        ).json(),
    )
    book = client.post(url, json={"type": "l2Book", "coin": "BTC"}).json()
    book["levels"] = [side[:20] for side in book["levels"]]
    _save("hyperliquid", "l2_book", book)


def _lighter(client: httpx.Client) -> None:
    """Captures Lighter samples (owner account indices zeroed).

    Args:
        client: HTTP client.
    """
    base = "https://mainnet.zklighter.elliot.ai/api/v1"
    books = client.get(f"{base}/orderBooks").json()
    perps = [b for b in books["order_books"] if b["market_type"] == "perp"]
    keep = [b for b in perps if b["symbol"] in {"BTC", "ETH"}]
    keep += [b for b in perps if b["status"] != "active"][:1]
    _save("lighter", "order_books", {"code": 200, "order_books": keep})
    details = client.get(
        f"{base}/orderBookDetails", params={"market_id": 1}
    ).json()
    _save("lighter", "order_book_details", details)
    now = int(time.time() * 1000)
    _save(
        "lighter",
        "candles_5m",
        client.get(
            f"{base}/candles",
            params={
                "market_id": 1,
                "resolution": "5m",
                "start_timestamp": now - 900_000,
                "end_timestamp": now,
                "count_back": 3,
            },
        ).json(),
    )
    _save(
        "lighter",
        "fundings",
        client.get(
            f"{base}/fundings",
            params={
                "market_id": 1,
                "resolution": "1h",
                "start_timestamp": now - 10_800_000,
                "end_timestamp": now,
                "count_back": 3,
            },
        ).json(),
    )
    orders = client.get(
        f"{base}/orderBookOrders", params={"market_id": 1, "limit": 20}
    ).json()
    for side in ("asks", "bids"):
        for order in orders.get(side, []):
            order["owner_account_index"] = 0
    _save("lighter", "order_book_orders", orders)


def _bitget(client: httpx.Client) -> None:
    """Captures Bitget v2 USDT-FUTURES samples.

    Args:
        client: HTTP client.
    """
    base = "https://api.bitget.com/api/v2/mix/market"
    product = {"productType": "USDT-FUTURES"}
    contracts = client.get(f"{base}/contracts", params=product).json()
    rows = contracts["data"]
    keep = [c for c in rows if c["symbol"] in {"BTCUSDT", "ETHUSDT"}]
    keep += [c for c in rows if c.get("isRwa") == "YES"][:1]
    _save("bitget", "contracts", {**contracts, "data": keep})
    one = {**product, "symbol": "BTCUSDT"}
    _save(
        "bitget",
        "candles_5m",
        client.get(
            f"{base}/candles", params={**one, "granularity": "5m", "limit": 3}
        ).json(),
    )
    _save("bitget", "ticker", client.get(f"{base}/ticker", params=one).json())
    tickers = client.get(f"{base}/tickers", params=product).json()
    tickers["data"] = sorted(
        tickers["data"], key=lambda t: -float(t["usdtVolume"])
    )[:6]
    _save("bitget", "tickers", tickers)
    _save(
        "bitget",
        "open_interest",
        client.get(f"{base}/open-interest", params=one).json(),
    )
    _save(
        "bitget",
        "current_fund_rate",
        client.get(f"{base}/current-fund-rate", params=one).json(),
    )
    _save(
        "bitget",
        "orderbook",
        client.get(f"{base}/orderbook", params={**one, "limit": 15}).json(),
    )


def _mexc(client: httpx.Client) -> None:
    """Captures MEXC contract samples.

    Args:
        client: HTTP client.
    """
    base = "https://contract.mexc.com/api/v1/contract"
    detail = client.get(f"{base}/detail").json()
    keep = [
        d for d in detail["data"] if d["symbol"] in {"BTC_USDT", "ETH_USDT"}
    ]
    for row in keep:
        row.pop("baseCoinIconUrl", None)
    _save("mexc", "detail", {**detail, "data": keep})
    now = int(time.time())
    _save(
        "mexc",
        "kline_min5",
        client.get(
            f"{base}/kline/BTC_USDT",
            params={"interval": "Min5", "start": now - 900, "end": now},
        ).json(),
    )
    _save(
        "mexc",
        "ticker",
        client.get(f"{base}/ticker", params={"symbol": "BTC_USDT"}).json(),
    )
    tickers = client.get(f"{base}/ticker").json()
    tickers["data"] = sorted(
        tickers["data"], key=lambda t: -float(t["amount24"])
    )[:6]
    _save("mexc", "ticker_all", tickers)
    _save(
        "mexc",
        "funding_rate",
        client.get(f"{base}/funding_rate/BTC_USDT").json(),
    )
    _save(
        "mexc",
        "depth",
        client.get(f"{base}/depth/BTC_USDT", params={"limit": 20}).json(),
    )


def main() -> None:
    """Captures every venue's fixtures."""
    with httpx.Client(
        timeout=20.0, headers={"User-Agent": "joseph-fixture-capture"}
    ) as c:
        for capture in (_binance, _hyperliquid, _lighter, _bitget, _mexc):
            capture(c)
            print(f"captured {capture.__name__.strip('_')}")  # noqa: T201


if __name__ == "__main__":
    main()
