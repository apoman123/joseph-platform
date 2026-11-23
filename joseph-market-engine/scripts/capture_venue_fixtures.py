"""Captures small, sanitized public-API samples used by offline tests.

Run manually (network required); never run in CI::

    uv run python scripts/capture_venue_fixtures.py [venue ...]

Venues: binance hyperliquid lighter bitget mexc twse nasdaqtrader treasury
(default: all). ``twse`` records a short MIS snapshot series and must run
during a TWSE session (09:00-13:30 Asia/Taipei) to capture live data. Alpaca
fixtures are hand-written from Alpaca's documentation (no key is needed or
used here; see ``fixtures/venues/alpaca/README.md``).

Each venue directory under ``fixtures/venues`` receives truncated JSON
responses so that adapter parsing is contract-tested without network access.
No credentials are involved: every endpoint is public market data.
"""

from __future__ import annotations

import datetime
import json
import pathlib
import sys
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


def _save_text(venue: str, name: str, body: str) -> None:
    """Writes one text fixture file (CSV, pipe-separated).

    Args:
        venue: Venue directory name.
        name: File name with extension.
        body: Response text.
    """
    directory = _ROOT / venue
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(body, encoding="utf-8")


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


_TWSE_CODES = (
    "2330",
    "2317",
    "2603",
    "0050",
    "00878",
    "00865B",
    "00631L",
    "00632R",
    "00646",
    "00625K",
    "01001T",
    "2881A",
    "2314",
)
"""Stocks, domestic/foreign/leveraged/inverse/bond ETFs and exclusions."""
_MIS_CODES = ("2330", "0050", "2317", "00865B")
_TWSE_PACE_S = 2.5
"""TWSE blocks clients above roughly 3 requests per 5 seconds."""
_COMPANY_FIELDS = (
    "出表日期",
    "公司代號",
    "公司名稱",
    "公司簡稱",
    "英文簡稱",
    "產業別",
    "上市日期",
)
_FUND_FIELDS = (
    "出表日期",
    "基金代號",
    "基金簡稱",
    "基金英文名稱",
    "基金類型",
    "標的指數/追蹤指數名稱",
    "是否包含國外成分股",
    "上市日期",
)


def _twse(client: httpx.Client) -> None:
    """Captures TWSE OpenAPI, STOCK_DAY, holiday and MIS samples.

    Args:
        client: HTTP client.
    """
    openapi = "https://openapi.twse.com.tw/v1"
    keep = set(_TWSE_CODES)
    day_all = client.get(f"{openapi}/exchangeReport/STOCK_DAY_ALL").json()
    _save("twse", "stock_day_all", [r for r in day_all if r["Code"] in keep])
    companies = client.get(f"{openapi}/opendata/t187ap03_L").json()
    _save(
        "twse",
        "t187ap03_L",
        [
            {k: r.get(k) for k in _COMPANY_FIELDS}
            for r in companies
            if r["公司代號"] in keep
        ],
    )
    funds = client.get(f"{openapi}/opendata/t187ap47_L").json()
    _save(
        "twse",
        "t187ap47_L",
        [
            {k: r.get(k) for k in _FUND_FIELDS}
            for r in funds
            if r["基金代號"] in keep
        ],
    )
    for name in ("TWT88U", "TWTAWU"):
        _save(
            "twse",
            name.lower(),
            client.get(f"{openapi}/exchangeReport/{name}").json(),
        )
    _save(
        "twse",
        "holiday_openapi",
        client.get(f"{openapi}/holidaySchedule/holidaySchedule").json(),
    )
    www = "https://www.twse.com.tw/rwd/zh"
    year = datetime.datetime.now(datetime.UTC).year
    for offset in (0, 1):
        _save(
            "twse",
            f"holiday_rwd_{year + offset}",
            client.get(
                f"{www}/holidaySchedule/holidaySchedule",
                params={"response": "json", "date": f"{year + offset}0101"},
            ).json(),
        )
        time.sleep(_TWSE_PACE_S)
    month = datetime.datetime.now(datetime.UTC).strftime("%Y%m01")
    for code in ("2330", "0050"):
        _save(
            "twse",
            f"stock_day_{code}",
            client.get(
                f"{www}/afterTrading/STOCK_DAY",
                params={"date": month, "stockNo": code, "response": "json"},
            ).json(),
        )
        time.sleep(_TWSE_PACE_S)
    series = []
    query = "|".join(f"tse_{code}.tw" for code in (*_MIS_CODES, "00679B"))
    for _ in range(24):
        started = int(time.time() * 1000)
        body = client.get(
            "https://mis.twse.com.tw/stock/api/getStockInfo.jsp",
            params={"ex_ch": query, "json": "1", "delay": "0", "_": started},
        ).json()
        series.append({"received_ms": started, "response": body})
        time.sleep(5.0)
    _save("twse", "mis_series", {"query": query, "responses": series})


_NASDAQ_KEEP = frozenset(
    {
        "AAPL", "QQQ", "TLT", "IEF", "SHY", "BND", "MBB", "EMB", "VCIT",
        "VCSH", "BNDX", "AACIU", "ZXYZ.A", "GOOGL", "BRKR",
    }
)  # fmt: skip
_OTHER_KEEP = frozenset(
    {
        "BRK.A", "BRK.B", "BF.B", "AGG", "LQD", "HYG", "TIP", "GOVT", "BIL",
        "SGOV", "JNK", "MUB", "SPY", "ABR$D", "AAC.U", "AAC.W", "ZEXIT",
        "MTEST", "ZZZTX", "AIIA.R", "IBM", "AAA",
    }
)  # fmt: skip


def _nasdaqtrader(client: httpx.Client) -> None:
    """Captures a slice of the Nasdaq Trader symbol directory.

    Args:
        client: HTTP client.
    """
    base = "https://www.nasdaqtrader.com/dynamic/SymDir"
    for name, keep in (
        ("nasdaqlisted.txt", _NASDAQ_KEEP),
        ("otherlisted.txt", _OTHER_KEEP),
    ):
        lines = client.get(f"{base}/{name}").text.splitlines()
        kept = [lines[0]]
        kept += [line for line in lines[1:-1] if line.split("|")[0] in keep]
        kept.append(lines[-1])
        _save_text("nasdaqtrader", name, "\n".join(kept) + "\n")


def _treasury(client: httpx.Client) -> None:
    """Captures Treasury par and real yield-curve CSV years.

    Args:
        client: HTTP client.
    """
    base = (
        "https://home.treasury.gov/resource-center/data-chart-center/"
        "interest-rates/daily-treasury-rates.csv"
    )
    year = datetime.datetime.now(datetime.UTC).year
    for curve, kind, years in (
        ("nominal", "daily_treasury_yield_curve", (year, 2002, 1990)),
        ("real", "daily_treasury_real_yield_curve", (year,)),
    ):
        for when in years:
            body = client.get(
                f"{base}/{when}/all",
                params={
                    "type": kind,
                    "field_tdr_date_value": str(when),
                    "page": "",
                    "_format": "csv",
                },
            ).text
            _save_text("treasury", f"{curve}_{when}.csv", body)
            time.sleep(1.0)


def main() -> None:
    """Captures the fixtures of the venues named on the command line."""
    captures = {
        "binance": _binance,
        "hyperliquid": _hyperliquid,
        "lighter": _lighter,
        "bitget": _bitget,
        "mexc": _mexc,
        "twse": _twse,
        "nasdaqtrader": _nasdaqtrader,
        "treasury": _treasury,
    }
    names = sys.argv[1:] or list(captures)
    with httpx.Client(
        timeout=30.0, headers={"User-Agent": "joseph-fixture-capture"}
    ) as c:
        for name in names:
            captures[name](c)
            print(f"captured {name}")  # noqa: T201


if __name__ == "__main__":
    main()
