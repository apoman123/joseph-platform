# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Heatmap service over recorded public sources and Alpaca fixtures."""

from __future__ import annotations

import asyncio
import dataclasses
import datetime
import decimal
from collections.abc import AsyncIterator
from collections.abc import Callable

import httpx
import pytest
import respx

from joseph import bootstrap
from joseph.domain import clock as clock_lib
from joseph.domain import errors
from joseph.domain import instruments
from joseph.domain import sessions
from joseph.market_data.app import heatmap
from joseph.market_data.app import heatmap_cache
from joseph.market_data.app import heatmap_models as models
from joseph.market_data.app import heatmap_tw
from joseph.market_data.app import heatmap_us
from joseph.market_data.app import universe
from joseph.platform import config

D = decimal.Decimal
_SESSION = sessions.XNYS.session(datetime.date(2026, 11, 20))
assert _SESSION is not None
_NOW = _SESSION.open_ms + 3_600_000


def _settings(**changes: object) -> config.Settings:
    """Builds offline settings.

    Args:
        **changes: Overrides.

    Returns:
        Settings.
    """
    base = dataclasses.replace(
        config.Settings(),
        database_url="sqlite://",
        strategy_activations_enabled=False,
        script_plugin_packages="none",
    )
    return dataclasses.replace(base, **changes)  # type: ignore[arg-type]  # overrides are per-field values


@dataclasses.dataclass
class _Env:
    """A heatmap service and its fixture router.

    Attributes:
        service: Service under test.
        router: respx router serving the fixtures.
        clock: Manual clock.
    """

    service: heatmap.HeatmapService
    router: respx.Router
    clock: clock_lib.ManualClock


async def _env(
    router: respx.Router, settings: config.Settings, budget_s: float = 5
) -> tuple[_Env, bootstrap.Container]:
    """Builds the service over fixture-backed sources.

    Args:
        router: Active respx router.
        settings: Engine settings.
        budget_s: Request budget.

    Returns:
        Environment and its container (closed by the caller).
    """
    clock = clock_lib.ManualClock(_NOW)
    client = httpx.AsyncClient()
    container = bootstrap.build_container(
        settings, clock=clock, http_client=client
    )
    sources = bootstrap.heatmap_sources(clock, client, 5)
    service = heatmap.HeatmapService(
        universe.UniverseService(container.market_data),
        sources,
        clock=clock,
        budget_s=budget_s,
    )
    return _Env(service, router, clock), container


@pytest.fixture
async def demo(
    heatmap_routes: Callable[[respx.Router], None],
) -> AsyncIterator[_Env]:
    """Serves the public sources with a DEMO (mock-only) engine.

    Args:
        heatmap_routes: Installer of the heatmap routes.

    Yields:
        Environment without Alpaca or Taiwan feeds.
    """
    with respx.mock(assert_all_called=False) as router:
        heatmap_routes(router)
        env, container = await _env(router, _settings(demo_only=True))
        yield env
        await env.service.close()
        await container.aclose()


def _tile(snapshot: models.HeatmapSnapshot, key: str) -> models.HeatmapTile:
    """Finds a tile.

    Args:
        snapshot: Snapshot.
        key: Tile key.

    Returns:
        The tile.
    """
    return next(tile for tile in snapshot.tiles if tile.key == key)


def _reasons(snapshot: models.HeatmapSnapshot) -> dict[str, str | None]:
    """Lists unavailable metrics.

    Args:
        snapshot: Snapshot.

    Returns:
        Reason by unavailable metric id.
    """
    return {
        row.id: row.reason
        for row in (*snapshot.size_metrics, *snapshot.color_metrics)
        if not row.available
    }


async def test_sp500_uses_screener_taxonomy_without_alpaca(demo: _Env) -> None:
    """Shows members by market cap with delayed values and honest gaps.

    Args:
        demo: Environment.
    """
    snap = await demo.service.snapshot("stocks")
    assert snap.dataset == "us_sp500" and snap.currency == "USD"
    assert snap.universe_count == 13 == len(snap.tiles)
    caps = [D(tile.metrics.get("market_cap", "-1")) for tile in snap.tiles]
    assert caps == sorted(caps, reverse=True)
    # The screener publishes no market cap for BF.B: it is last, not guessed.
    assert snap.tiles[-1].key == "BF.B"
    assert "market_cap" not in snap.tiles[-1].metrics
    apple = _tile(snap, "AAPL")
    assert apple.group == "Technology"
    assert apple.industry == "Computer Manufacturing"
    assert apple.exchange == "Nasdaq"
    assert apple.instrument_id is None
    assert apple.chart_unavailable_reason == heatmap_us.NO_CHART
    assert set(apple.metrics) == {
        "market_cap",
        "change_1d",
        "volume_1d",
        "turnover_1d",
    }
    assert D(apple.metrics["turnover_1d"]) == (
        D(apple.price or "0") * D(apple.metrics["volume_1d"])
    ).quantize(D(1))
    reasons = _reasons(snap)
    assert reasons["perf_1y"] == heatmap_us.NO_KEYS
    assert reasons["change_1h"] == heatmap_us.INTRADAY
    assert "gap" in reasons and "premarket_change" in reasons
    assert "mono" not in reasons and "market_cap" not in reasons
    assert snap.default_grouping == "sector"
    assert {row.id for row in snap.groupings} == {"sector", "none"}
    fields = {row.field: row for row in snap.provenance}
    assert fields["Members"].source.startswith("Wikipedia")
    assert "unofficial" in fields["Sector, industry & market cap"].source
    assert not snap.pending and not snap.source_errors


async def test_exchange_and_index_data_sets(demo: _Env) -> None:
    """Selects Nasdaq-100 members and exchange listings.

    Args:
        demo: Environment.
    """
    ndx = await demo.service.snapshot("stocks", dataset="us_nasdaq100")
    assert {tile.key for tile in ndx.tiles} == {
        "AAPL",
        "MSFT",
        "NVDA",
        "AMZN",
        "ADBE",
        "AMD",
        "AMAT",
        "INTC",
    }
    nyse = await demo.service.snapshot("stocks", dataset="us_nyse")
    assert {tile.exchange for tile in nyse.tiles} == {"NYSE"}
    assert "BRK.B" in {tile.key for tile in nyse.tiles}
    every = await demo.service.snapshot("stocks", dataset="us_all", limit=5)
    assert len(every.tiles) == 5 and every.limit == 5
    assert every.universe_count > 5
    misc = await demo.service.snapshot("stocks", dataset="us_nasdaq_composite")
    assert _tile(misc, "AACO").group == "Miscellaneous"


async def test_taiwan_stocks_derive_market_cap(demo: _Env) -> None:
    """Groups by industry and multiplies issued shares by the close.

    Args:
        demo: Environment.
    """
    snap = await demo.service.snapshot("stocks", dataset="tw_twse")
    assert snap.currency == "TWD"
    tsmc = _tile(snap, "2330")
    assert snap.tiles[0].key == "2330"
    assert tsmc.group == "Semiconductor 半導體業"
    assert tsmc.metrics["market_cap"] == str(D("25932364970") * D("2180"))
    assert tsmc.metrics["change_1d"] == "1.6317"
    assert {"gap", "volatility_1d"} <= set(tsmc.metrics)
    assert tsmc.chart_unavailable_reason == heatmap_tw.NO_CHART
    assert all(not key.startswith("00") for key in (t.key for t in snap.tiles))
    reasons = _reasons(snap)
    assert reasons["perf_1w"] == heatmap_tw.NO_HISTORY
    assert "market_cap" not in reasons
    notes = [row.note for row in snap.provenance if row.note]
    assert any("derived" in note for note in notes)
    tpex = await demo.service.snapshot("stocks", dataset="tw_tpex")
    assert {tile.key for tile in tpex.tiles} == {
        "5347",
        "6488",
        "3105",
        "8069",
        "1240",
    }


async def test_taiwan_etfs_by_turnover_and_asset_class(demo: _Env) -> None:
    """Lists ETFs of both markets with classified asset classes.

    Args:
        demo: Environment.
    """
    catalog = demo.service.catalog()
    assert catalog.defaults["etfs"] == "tw_etfs"
    unavailable = {row.id: row for row in catalog.datasets if not row.available}
    assert set(unavailable) == {"us_etfs"}
    assert "Alpaca" in (unavailable["us_etfs"].reason or "")
    snap = await demo.service.snapshot("etfs")
    assert snap.dataset == "tw_etfs" and snap.default_size == "turnover_1d"
    groups = {tile.key: tile.group for tile in snap.tiles}
    assert groups["00679B"] == "Fixed income"
    assert groups["00635U"] == "Commodity"
    assert groups["0050"] == "Equity"
    turnover = [D(tile.metrics.get("turnover_1d", "-1")) for tile in snap.tiles]
    assert turnover == sorted(turnover, reverse=True)
    reasons = _reasons(snap)
    assert "aum" in reasons and "market_cap" not in [
        row.id for row in snap.size_metrics
    ]
    us = await demo.service.snapshot("etfs", dataset="us_etfs")
    assert not us.tiles and "Alpaca" in us.source_errors["dataset"]


async def test_crypto_data_sets(demo: _Env) -> None:
    """Excludes Bitcoin or stablecoins and maps CoinGecko windows.

    Args:
        demo: Environment.
    """
    snap = await demo.service.snapshot("crypto")
    assert snap.tiles[0].key == "bitcoin"
    btc = snap.tiles[0]
    assert {"change_1d", "change_1h", "perf_1w", "perf_1m", "perf_1y"} <= set(
        btc.metrics
    )
    assert "volatility_1d" in btc.metrics
    assert btc.chart_unavailable_reason == "No chart on configured venues"
    reasons = _reasons(snap)
    assert set(reasons) == {"change_4h", "perf_3m", "perf_6m", "perf_ytd"}
    assert {row.id for row in snap.groupings} == {"none"}
    ex_btc = await demo.service.snapshot("crypto", dataset="crypto_ex_btc")
    assert "bitcoin" not in {tile.key for tile in ex_btc.tiles}
    ex_stable = await demo.service.snapshot(
        "crypto", dataset="crypto_ex_stable"
    )
    keys = {tile.key for tile in ex_stable.tiles}
    assert "tether" not in keys and "usd-coin" not in keys and "bitcoin" in keys
    defi = await demo.service.snapshot("crypto", dataset="crypto_defi")
    assert defi.tiles[0].symbol == "STETH"


async def test_rejects_data_sets_of_another_family(demo: _Env) -> None:
    """Validates the data set against the family.

    Args:
        demo: Environment.
    """
    with pytest.raises(errors.ValidationFailedError):
        await demo.service.snapshot("crypto", dataset="us_sp500")
    with pytest.raises(errors.ValidationFailedError):
        await demo.service.snapshot("stocks", dataset="nope")


async def test_disabled_sources_report_unavailable() -> None:
    """Without public sources every data set states why."""
    container = bootstrap.build_container(_settings(demo_only=True))
    service = heatmap.HeatmapService(
        universe.UniverseService(container.market_data)
    )
    catalog = service.catalog()
    assert not any(row.available for row in catalog.datasets)
    snap = await service.snapshot("crypto")
    assert not snap.tiles and "disabled" in snap.source_errors["dataset"]
    await service.close()
    await container.aclose()


async def test_slow_sources_answer_pending_then_fill(
    heatmap_routes: Callable[[respx.Router], None],
) -> None:
    """Answers within the budget and serves the finished load next time.

    Args:
        heatmap_routes: Installer of the heatmap routes.
    """
    gate = asyncio.Event()

    async def slow(request: httpx.Request) -> httpx.Response:
        """Answers once the gate opens.

        Args:
            request: Mocked request.

        Returns:
            One recorded page of coins.
        """
        del request
        await gate.wait()
        return httpx.Response(
            200, json=[{"id": "bitcoin", "symbol": "btc", "market_cap": 5}]
        )

    with respx.mock(assert_all_called=False) as router:
        heatmap_routes(router)
        router.route(host="api.coingecko.com").mock(side_effect=slow)
        env, container = await _env(
            router, _settings(demo_only=True), budget_s=0.05
        )
        first = await env.service.snapshot("crypto")
        assert first.pending and not first.tiles
        gate.set()
        for _ in range(50):
            await asyncio.sleep(0)
        second = await env.service.snapshot("crypto")
        assert not second.pending and [t.key for t in second.tiles] == [
            "bitcoin"
        ]
        await env.service.close()
        await container.aclose()


async def _failing_load() -> int:  # noqa: DOC201 - always raises; never returns
    """Fails like an unreachable source.

    Raises:
        errors.VenueUnavailableError: Always.
    """
    raise errors.VenueUnavailableError("down")


async def test_cache_serves_stale_values_and_keeps_errors() -> None:
    """Refreshes in the background and never drops the last good value."""
    clock = clock_lib.ManualClock(0)
    cache = heatmap_cache.SourceCache[int]("test", clock, 1000)
    calls = []

    async def good() -> int:
        """Loads a value.

        Returns:
            The call count.
        """
        calls.append(1)
        return len(calls)

    assert await cache.get(good, budget_s=1) == 1
    assert await cache.get(good, budget_s=1) == 1
    clock.advance(1000)
    assert await cache.get(_failing_load, budget_s=1) == 1
    await asyncio.sleep(0)
    assert cache.error == "down" and cache.value == 1
    assert await cache.get(good, budget_s=1) == 1
    await asyncio.sleep(0)
    assert cache.value == 2 and cache.error is None
    await cache.close()


async def test_alpaca_quotes_and_charts(
    heatmap_routes: Callable[[respx.Router], None],
    exchange_routes: Callable[[respx.Router], None],
) -> None:
    """Uses Alpaca session quotes and opens charts on the configured venues.

    Args:
        heatmap_routes: Installer of the heatmap routes.
        exchange_routes: Installer of the Alpaca and Nasdaq Trader routes.
    """
    settings = _settings(
        enabled_venues=(instruments.Venue.NASDAQ, instruments.Venue.NYSE),
        alpaca_key_id="kid",
        alpaca_secret_key="sek",
    )
    with respx.mock(assert_all_called=False) as router:
        heatmap_routes(router)
        exchange_routes(router)
        env, container = await _env(router, settings)
        snap = await env.service.snapshot("stocks")
        apple = _tile(snap, "AAPL")
        assert apple.instrument_id == "nasdaq:stock:AAPL"
        assert D(apple.metrics["change_1d"]) == (
            ((D("230.35") - D("229.95")) / D("229.95") * 100).quantize(
                D("0.0001")
            )
        )
        assert apple.price == "230.35"
        assert {"gap", "volatility_1d", "volume_1d"} <= set(apple.metrics)
        assert apple.metrics["market_cap"] == "5167353275560"
        reasons = _reasons(snap)
        assert "gap" not in reasons and "perf_1y" in reasons
        sources = {row.source for row in snap.provenance}
        assert any("Alpaca daily bars" in source for source in sources)
        calls = [
            call.request.url.path
            for call in router.calls
            if call.request.url.host == "data.alpaca.markets"
        ]
        assert "/v2/stocks/snapshots" in calls and "/v2/stocks/bars" in calls
        etfs = await env.service.snapshot("etfs", dataset="us_etfs")
        spy = _tile(etfs, "SPY")
        assert spy.group == "Equity" and spy.instrument_id == "nyse:etf:SPY"
        assert env.service.catalog().defaults["etfs"] == "us_etfs"
        await env.service.close()
        await container.aclose()
