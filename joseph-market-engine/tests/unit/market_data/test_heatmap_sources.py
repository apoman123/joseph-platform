# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Keyless heatmap sources over recorded responses (no network)."""

from __future__ import annotations

import decimal
from collections.abc import Callable
from collections.abc import Iterator

import httpx
import pytest
import respx
from tests import conftest

from joseph.domain import clock as clock_lib
from joseph.domain import errors
from joseph.domain import instruments
from joseph.market_data.adapters import coingecko
from joseph.market_data.adapters import http
from joseph.market_data.adapters import taiwan_open_data as tw
from joseph.market_data.adapters import us_equities

D = decimal.Decimal


@pytest.fixture
def public(
    heatmap_routes: Callable[[respx.Router], None],
) -> Iterator[respx.Router]:
    """Serves the recorded public responses.

    Args:
        heatmap_routes: Installer of the heatmap routes.

    Yields:
        The respx router.
    """
    with respx.mock(assert_all_called=False) as router:
        heatmap_routes(router)
        yield router


def _client(base_url: str) -> http.VenueHttp:
    """Builds a fixture-backed client.

    Args:
        base_url: Public origin.

    Returns:
        Client.
    """
    return http.VenueHttp(
        instruments.Venue.NASDAQ, base_url, client=httpx.AsyncClient()
    )


def test_screener_parses_common_stocks_and_skips_other_classes() -> None:
    """Keeps common stocks, normalizes class tickers and cleans names."""
    nyse = us_equities.parse_screener(
        conftest.heatmap_fixture("nasdaq/screener_nyse"), "NYSE"
    )
    symbols = {row.symbol for row in nyse}
    assert {"BRK.B", "BF.B", "MMM", "JPM"} <= symbols
    assert "BFH^A" not in symbols
    agilent = next(row for row in nyse if row.symbol == "A")
    assert agilent.name == "Agilent Technologies Inc."
    assert agilent.last == D("178.43")
    assert agilent.change_pct == D("-2.774")
    assert agilent.market_cap == D("50304565419.00")
    nasdaq = us_equities.parse_screener(
        conftest.heatmap_fixture("nasdaq/screener_nasdaq"), "Nasdaq"
    )
    blank = next(row for row in nasdaq if row.symbol == "AACO")
    assert blank.sector == us_equities.UNCLASSIFIED
    with pytest.raises(errors.VenueUnavailableError):
        us_equities.parse_screener({"data": None}, "NYSE")


def test_clean_name_removes_share_class_descriptions() -> None:
    """Removes the security description but keeps company names."""
    assert us_equities.clean_name("Apple Inc. Common Stock") == "Apple Inc."
    assert (
        us_equities.clean_name("Alphabet Inc. Class A Common Stock")
        == "Alphabet Inc."
    )
    assert us_equities.clean_name("Common Stock") == "Common Stock"


def test_index_members_parse() -> None:
    """Reads Nasdaq-100 and S&P 500 (Wikipedia) members."""
    ndx = us_equities.parse_nasdaq100(
        conftest.heatmap_fixture("nasdaq/nasdaq100")
    )
    assert "AAPL" in ndx and len(ndx) == 8
    sp500 = us_equities.parse_sp500(conftest.heatmap_fixture("wikipedia/sp500"))
    assert {"MMM", "BRK.B", "BF.B", "ADBE"} <= set(sp500)
    with pytest.raises(errors.VenueUnavailableError):
        us_equities.parse_sp500({"parse": {"wikitext": "no table"}})


@pytest.mark.asyncio
async def test_us_source_sends_a_browser_agent(public: respx.Router) -> None:
    """Nasdaq.com answers only browser-like agents.

    Args:
        public: Recorded routes.
    """
    source = us_equities.UsEquitySource(
        _client(us_equities.NASDAQ_API), _client(us_equities.WIKIPEDIA_API)
    )
    rows = await source.screener("amex")
    assert rows and all(row.exchange == "NYSE American" for row in rows)
    assert len(await source.sp500()) == 13
    assert "MSFT" in await source.nasdaq100()
    request = public.calls.last.request
    assert "Mozilla" in request.headers["User-Agent"]
    with pytest.raises(ValueError, match="exchange"):
        await source.screener("otc")
    await source.close()


def test_taiwan_profiles_derive_shares_and_industries() -> None:
    """Reads issued shares (or capital over par) and industry names."""
    twse = tw.parse_twse_companies(
        conftest.heatmap_fixture("twse/company_info")
    )
    tsmc = next(row for row in twse if row.code == "2330")
    assert tsmc.shares == D("25932364970")
    assert tsmc.industry == "Semiconductor 半導體業"
    assert tsmc.english_name == "TSMC"
    tdr = next(row for row in twse if row.industry_code == "91")
    assert tdr.industry == "Taiwan Depositary Receipts 存託憑證"
    tpex = tw.parse_tpex_companies(
        conftest.heatmap_fixture("tpex/company_info")
    )
    assert {row.code for row in tpex} == {
        "5347",
        "6488",
        "3105",
        "8069",
        "1240",
    }
    assert all(row.shares for row in tpex)
    assert tw.TwCompany("1", "twse", "x", None, "99", None).industry is None


def test_par_value_fallback() -> None:
    """Derives shares from paid-in capital only with a TWD par value."""
    rows = tw.parse_twse_companies(
        [
            {
                "公司代號": "1",
                "公司簡稱": "A",
                "實收資本額": "1000",
                "普通股每股面額": "新台幣 10.0000元",
            },
            {"公司代號": "2", "實收資本額": "1000", "普通股每股面額": "無面額"},
        ]
    )
    assert rows[0].shares == D(100)
    assert rows[1].shares is None


def test_daily_quotes_and_codes() -> None:
    """Parses end-of-day quotes and tells ETFs from stocks."""
    twse = {
        row.code: row
        for row in tw.parse_twse_daily(
            conftest.heatmap_fixture("twse/stock_day_all")
        )
    }
    tsmc = twse["2330"]
    assert tsmc.close == D("2180.00")
    assert tsmc.previous_close == D("2145.0000")
    assert tsmc.as_of_ms == 1795757400000
    tpex = {
        row.code: row
        for row in tw.parse_tpex_daily(
            conftest.heatmap_fixture("tpex/daily_close_quotes")
        )
    }
    assert tpex["700019"].close is None
    assert tpex["5347"].shares == D("1878502192")
    assert tw.is_etf_code("0050") and tw.is_etf_code("00679B")
    assert not tw.is_etf_code("2330") and tw.is_stock_code("2330")
    assert not tw.is_stock_code("700019")
    assert tw.roc_close_ms("bad") is None
    assert tw.roc_close_ms("1151399") is None
    with pytest.raises(errors.VenueUnavailableError):
        tw.parse_twse_daily({"stat": "error"})


@pytest.mark.asyncio
async def test_taiwan_source_reads_both_markets(public: respx.Router) -> None:
    """Reads profiles and daily quotes of both markets.

    Args:
        public: Recorded routes.
    """
    del public
    source = tw.TaiwanOpenData(
        _client(tw.TWSE_OPENAPI), _client(tw.TPEX_OPENAPI)
    )
    assert len(await source.companies("twse")) == 8
    assert len(await source.companies("tpex")) == 5
    assert len(await source.daily("twse")) == 14
    assert len(await source.daily("tpex")) == 9
    await source.close()


def test_coin_markets_parse() -> None:
    """Parses prices, sizes and every change window."""
    coins = coingecko.parse_markets(
        conftest.heatmap_fixture("coingecko/markets_page1")
    )
    btc = coins[0]
    assert (btc.id, btc.symbol) == ("bitcoin", "BTC")
    assert set(btc.changes) == {"1h", "24h", "7d", "30d", "1y"}
    assert btc.market_cap and btc.fd_market_cap and btc.volume
    assert btc.as_of_ms is not None
    with pytest.raises(errors.VenueUnavailableError):
        coingecko.parse_markets({"status": "error"})
    [odd] = coingecko.parse_markets(
        [
            {
                "id": "x",
                "market_cap": 0,
                "current_price": None,
                "last_updated": "?",
            }
        ]
    )
    assert odd.market_cap is None and odd.price is None and odd.as_of_ms is None


@pytest.mark.asyncio
async def test_coin_pages_stop_on_a_short_page_and_back_off() -> None:
    """Reads full pages only, and pauses after HTTP 429."""
    clock = clock_lib.ManualClock(1_000_000)
    pages: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        """Serves one full page, then a short one, then 429.

        Args:
            request: Mocked request.

        Returns:
            Response.
        """
        page = request.url.params["page"]
        pages.append(page)
        if len(pages) > 2:
            return httpx.Response(429)
        count = coingecko.PER_PAGE if page == "1" else 3
        return httpx.Response(
            200,
            json=[{"id": f"c{page}-{n}", "symbol": "c"} for n in range(count)],
        )

    with respx.mock() as router:
        router.route(host="api.coingecko.com").mock(side_effect=answer)
        source = coingecko.CoinGeckoSource(_client(coingecko.API_URL), clock)
        coins = await source.markets(pages=5)
        assert len(coins) == coingecko.PER_PAGE + 3
        assert pages == ["1", "2"]
        with pytest.raises(errors.RateLimitedError):
            await source.markets()
        with pytest.raises(errors.RateLimitedError, match="backing off"):
            await source.markets()
        assert len(pages) == 3
        clock.advance(coingecko.BACKOFF_MS)
        with pytest.raises(errors.RateLimitedError):
            await source.markets(category=coingecko.STABLECOINS)
        assert router.calls.last.request.url.params["category"] == "stablecoins"
        await source.close()
