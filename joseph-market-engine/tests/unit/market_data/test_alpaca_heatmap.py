# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Alpaca multi-symbol daily bars and session quotes for heatmaps."""

from __future__ import annotations

import datetime
import decimal
from collections.abc import Callable
from collections.abc import Iterator

import httpx
import pytest
import respx

from joseph.domain import clock as clock_lib
from joseph.domain import errors
from joseph.domain import instruments
from joseph.domain import sessions
from joseph.market_data.adapters import alpaca
from joseph.market_data.adapters import factory
from joseph.market_data.app import ports

_KEYS = alpaca.AlpacaConfig(key_id="kid", secret_key="sek")
_AAPL = instruments.InstrumentId.parse("nasdaq:stock:AAPL")
_SPY = instruments.InstrumentId.parse("nyse:etf:SPY")
_SESSION = sessions.XNYS.session(datetime.date(2026, 11, 20))
assert _SESSION is not None
_NOW = _SESSION.open_ms + 3_600_000


@pytest.fixture
def alpaca_api(
    exchange_routes: Callable[[respx.Router], None],
) -> Iterator[respx.Router]:
    """Serves the Alpaca and Nasdaq Trader fixtures.

    Args:
        exchange_routes: Installer of the exchange fixture routes.

    Yields:
        The respx router.
    """
    with respx.mock(assert_all_called=False) as router:
        exchange_routes(router)
        yield router


def _adapter(
    venue: instruments.Venue, clock: clock_lib.Clock, cfg: alpaca.AlpacaConfig
) -> alpaca.AlpacaAdapter:
    """Builds an Alpaca adapter over the fixtures.

    Args:
        venue: US venue.
        clock: Clock.
        cfg: Alpaca configuration.

    Returns:
        Adapter.
    """
    adapter = factory.build_adapters(
        [venue],
        clock,
        crypto_source="native",
        client=httpx.AsyncClient(),
        alpaca_config=cfg,
    )[venue]
    assert isinstance(adapter, alpaca.AlpacaAdapter)
    return adapter


async def test_daily_bars_follow_pages_by_symbol(
    alpaca_api: respx.Router,
) -> None:
    """Joins pages per symbol and keeps the split adjustment and feed.

    Args:
        alpaca_api: Fixture routes.
    """
    clock = clock_lib.ManualClock(_NOW)
    adapter = _adapter(instruments.Venue.NASDAQ, clock, _KEYS)
    assert isinstance(adapter, ports.HeatmapQuotesPort)
    bars = await adapter.fetch_daily_bars_batch(
        [_AAPL, _SPY], datetime.date(2025, 10, 16)
    )
    assert [bar.day for bar in bars[_AAPL]] == [
        datetime.date(2026, 11, 18),
        datetime.date(2026, 11, 19),
        datetime.date(2026, 11, 20),
    ]
    assert bars[_AAPL][-1].vwap == decimal.Decimal("230.2")
    assert bars[_SPY][0].vwap is None
    calls = [
        call.request.url.params
        for call in alpaca_api.calls
        if call.request.url.path == "/v2/stocks/bars"
    ]
    assert len(calls) == 2
    assert calls[0]["symbols"] == "AAPL,SPY"
    assert calls[0]["adjustment"] == "split"
    assert calls[0]["feed"] == "iex"
    assert calls[1]["page_token"] == "QUFQTHwyMDI2LTExLTE5"
    await adapter.close()


async def test_daily_bars_stop_at_the_heatmap_budget(
    alpaca_api: respx.Router,
) -> None:
    """Leaves symbols out instead of waiting once the budget is spent.

    Args:
        alpaca_api: Fixture routes.
    """
    del alpaca_api
    clock = clock_lib.ManualClock(_NOW)
    adapter = _adapter(instruments.Venue.NASDAQ, clock, _KEYS)
    budget = adapter._source.heatmap_budget
    while budget.take():
        pass
    assert (
        await adapter.fetch_daily_bars_batch([_AAPL], datetime.date(2026, 1, 1))
        == {}
    )
    await adapter.close()


async def test_session_quotes_carry_both_daily_bars(
    alpaca_api: respx.Router,
) -> None:
    """Reads the latest trade with the current and previous daily bars.

    Args:
        alpaca_api: Fixture routes.
    """
    del alpaca_api
    clock = clock_lib.ManualClock(_NOW)
    adapter = _adapter(instruments.Venue.NASDAQ, clock, _KEYS)
    quotes = await adapter.fetch_session_quotes([_AAPL])
    quote = quotes[_AAPL]
    assert quote.last == decimal.Decimal("230.35")
    assert quote.today is not None and quote.previous is not None
    assert quote.today.day == datetime.date(2026, 11, 20)
    assert quote.previous.close == decimal.Decimal("229.95")
    assert quote.last_ms is not None
    await adapter.close()


async def test_heatmap_methods_need_keys(alpaca_api: respx.Router) -> None:
    """Fails clearly without Alpaca keys.

    Args:
        alpaca_api: Fixture routes.
    """
    del alpaca_api
    clock = clock_lib.ManualClock(_NOW)
    adapter = _adapter(instruments.Venue.NASDAQ, clock, alpaca.AlpacaConfig())
    with pytest.raises(errors.VenueUnavailableError, match="Alpaca keys"):
        await adapter.fetch_session_quotes([_AAPL])
    with pytest.raises(errors.VenueUnavailableError, match="Alpaca keys"):
        await adapter.fetch_daily_bars_batch([_AAPL], datetime.date(2026, 1, 1))
    await adapter.close()


def test_heatmap_bar_rejects_incomplete_rows() -> None:
    """Skips bars missing a price or volume."""
    assert alpaca.heatmap_bar({"t": "2026-11-20T05:00:00Z", "o": 1}) is None
    bar = alpaca.heatmap_bar(
        {"t": "2026-11-20T05:00:00Z", "o": 1, "h": 2, "l": 1, "c": 2, "v": 3}
    )
    assert bar is not None and bar.day == datetime.date(2026, 11, 20)
