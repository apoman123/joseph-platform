# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Opt-in live tests against TWSE, TPEx, Nasdaq Trader, Treasury and Alpaca.

Run with ``JOSEPH_NETWORK_TESTS=1 uv run pytest tests/network -m network``.
Requests are few and paced (TWSE blocks clients above roughly 3 requests
per 5 seconds; TPEx is paced at one request per 2 seconds, and its MIS
data comes from TWSE's host). Alpaca tests also need
``JOSEPH_ALPACA_KEY_ID`` and ``JOSEPH_ALPACA_SECRET_KEY`` and skip without
them.
"""

from __future__ import annotations

import os
from collections.abc import Callable

import httpx
import pytest

from joseph.domain import clock as clock_lib
from joseph.domain import instruments
from joseph.domain import sessions
from joseph.domain import timeframes
from joseph.market_data.adapters import alpaca
from joseph.market_data.adapters import factory
from joseph.market_data.adapters import tpex
from joseph.market_data.adapters import twse
from joseph.market_data.app import ports

pytestmark = [
    pytest.mark.network,
    pytest.mark.skipif(
        os.environ.get("JOSEPH_NETWORK_TESTS") != "1",
        reason="set JOSEPH_NETWORK_TESTS=1 to hit live APIs",
    ),
]

Validate = Callable[[str, str, object], None]
_TF = timeframes.Timeframe


def _live(
    venue: instruments.Venue, config: alpaca.AlpacaConfig | None = None
) -> tuple[ports.MarketDataPort, clock_lib.Clock]:
    """Builds a live adapter.

    Args:
        venue: Venue.
        config: Alpaca configuration.

    Returns:
        Adapter and clock.
    """
    clock = clock_lib.SystemClock()
    adapter = factory.build_adapters(
        [venue],
        clock,
        client=httpx.AsyncClient(timeout=30),
        alpaca_config=config,
    )[venue]
    return adapter, clock


async def test_live_twse_listings_history_and_mis(
    contract_validate: Validate,
) -> None:
    """TWSE instruments, STOCK_DAY history and MIS data work live."""
    adapter, clock = _live(instruments.Venue.TWSE)
    rows = {str(r.instrument_id): r for r in await adapter.list_instruments()}
    assert len(rows) > 1_000
    for key in ("twse:stock:2330", "twse:etf:0050", "twse:etf:00865B"):
        contract_validate(
            "market.schema.json", "Instrument", rows[key].to_contract()
        )
    assert rows["twse:etf:00865B"].asset_class is instruments.AssetClass.BOND
    tsmc = instruments.InstrumentId.parse("twse:stock:2330")
    now = clock.now_ms()
    days = await adapter.fetch_candles(tsmc, _TF.D1, now - 20 * 86_400_000, now)
    assert len(days) >= 10
    for candle in days:
        contract_validate("market.schema.json", "Candle", candle.to_contract())
        assert sessions.XTAI.is_aligned(_TF.D1, candle.start_ms)
    snapshot = await adapter.fetch_snapshot(tsmc)
    assert snapshot.last is not None and snapshot.volume_24h_base
    book = await adapter.fetch_order_book(tsmc, 5)
    assert book.best_bid is not None and book.best_ask is not None
    assert book.best_bid < book.best_ask
    tick = rows["twse:stock:2330"].tick_for(book.best_bid)
    assert book.best_ask - book.best_bid >= tick
    assert all(level.quantity % 1000 == 0 for level in book.bids)
    await adapter.close()


async def test_live_tpex_listings_history_and_mis(
    contract_validate: Validate,
) -> None:
    """TPEx instruments, tradingStock history and MIS otc data work live."""
    adapter, clock = _live(instruments.Venue.TPEX)
    rows = {str(r.instrument_id): r for r in await adapter.list_instruments()}
    assert len(rows) > 900
    for key in ("tpex:stock:6488", "tpex:etf:00679B", "tpex:etf:00937B"):
        contract_validate(
            "market.schema.json", "Instrument", rows[key].to_contract()
        )
    for bond in ("tpex:etf:00679B", "tpex:etf:00937B"):
        assert rows[bond].asset_class is instruments.AssetClass.BOND
        assert rows[bond].price_limit_pct is None
    assert rows["tpex:stock:6488"].price_limit_pct is not None
    assert not any(key.startswith("tpex:etf:00687C") for key in rows)
    stock = instruments.InstrumentId.parse("tpex:stock:6488")
    now = clock.now_ms()
    days = await adapter.fetch_candles(
        stock, _TF.D1, now - 20 * 86_400_000, now
    )
    assert len(days) >= 10
    for candle in days:
        contract_validate("market.schema.json", "Candle", candle.to_contract())
        assert sessions.XTAI.is_aligned(_TF.D1, candle.start_ms)
        assert candle.base_volume % 1000 == 0
    for key in ("tpex:stock:6488", "tpex:etf:00679B"):
        instrument = instruments.InstrumentId.parse(key)
        snapshot = await adapter.fetch_snapshot(instrument)
        assert snapshot.last is not None and snapshot.volume_24h_base
        book = await adapter.fetch_order_book(instrument, 5)
        assert book.best_bid is not None and book.best_ask is not None
        assert book.best_bid < book.best_ask
        tick = rows[key].tick_for(book.best_bid)
        assert (book.best_ask - book.best_bid) % tick == 0
        assert all(level.price % tick == 0 for level in book.bids)
        assert all(level.quantity % 1000 == 0 for level in book.bids)
    await adapter.close()


async def test_live_tpex_schedule_matches_xtai() -> None:
    """TPEx's published closures are XTAI closures (same Taiwan calendar)."""
    adapter, clock = _live(instruments.Venue.TPEX)
    assert isinstance(adapter, tpex.TpexAdapter)
    today = twse.taipei_date(clock.now_ms())
    for year in (today.year, today.year + 1):
        payload = await adapter._www.get(
            "/www/zh-tw/bulletin/tradingDate",
            params={"date": str(year), "response": "json"},
        )
        for day in tpex.schedule_closures(payload or {}, year, today):
            assert sessions.XTAI.session(day) is None, day
    await adapter.close()


async def test_live_nasdaq_trader_directory(
    contract_validate: Validate,
) -> None:
    """The symbol directory lists Nasdaq, NYSE-group and Cboe BZX listings."""
    nasdaq, _ = _live(instruments.Venue.NASDAQ)
    nyse, _ = _live(instruments.Venue.NYSE)
    cboe, _ = _live(instruments.Venue.CBOE)
    nasdaq_rows = {
        r.instrument_id.venue_market_id: r
        for r in await nasdaq.list_instruments()
    }
    nyse_rows = {
        r.instrument_id.venue_market_id: r
        for r in await nyse.list_instruments()
    }
    cboe_rows = {
        r.instrument_id.venue_market_id: r
        for r in await cboe.list_instruments()
    }
    assert len(nasdaq_rows) > 3_000 and len(nyse_rows) > 3_000
    assert len(cboe_rows) > 1_000
    assert nasdaq_rows["TLT"].asset_class is instruments.AssetClass.BOND
    assert nyse_rows["BRK.B"].listing_exchange == "NYSE"
    assert nyse_rows["SPY"].listing_exchange == "NYSE Arca"
    govt = cboe_rows["GOVT"]
    assert str(govt.instrument_id) == "cboe:etf:GOVT"
    assert govt.listing_exchange == "Cboe BZX"
    assert govt.asset_class is instruments.AssetClass.BOND
    assert "GOVT" not in nyse_rows and "GOVT" not in nasdaq_rows
    for row in (nyse_rows["BRK.B"], govt):
        contract_validate("market.schema.json", "Instrument", row.to_contract())
    assert not nasdaq.capabilities().credentials_configured or (
        os.environ.get("JOSEPH_ALPACA_KEY_ID")
    )
    await nasdaq.close()
    await nyse.close()
    await cboe.close()


async def test_live_treasury_yields(contract_validate: Validate) -> None:
    """The current year's par and real yields become USBOND bars."""
    adapter, clock = _live(instruments.Venue.TREASURY)
    now = clock.now_ms()
    for code in ("UST10Y", "TIPS10Y", "UST1.5M"):
        instrument = instruments.InstrumentId.parse(f"treasury:rate:{code}")
        days = await adapter.fetch_candles(
            instrument, _TF.D1, now - 30 * 86_400_000, now
        )
        assert len(days) >= 10, code
        for candle in days:
            contract_validate(
                "market.schema.json", "Candle", candle.to_contract()
            )
            assert candle.open == candle.close and candle.is_final
    weeks = await adapter.fetch_candles(
        instruments.InstrumentId.parse("treasury:rate:UST10Y"),
        _TF.W1,
        now - 60 * 86_400_000,
        now,
    )
    assert len(weeks) >= 6
    await adapter.close()


_KEYS = alpaca.AlpacaConfig.from_env()


@pytest.mark.skipif(not _KEYS.configured, reason="needs Alpaca keys")
async def test_live_alpaca_bars_and_snapshot(
    contract_validate: Validate,
) -> None:
    """With keys, Alpaca daily/minute bars and snapshots validate."""
    adapter, clock = _live(instruments.Venue.NASDAQ, _KEYS)
    aapl = instruments.InstrumentId.parse("nasdaq:stock:AAPL")
    now = clock.now_ms()
    days = await adapter.fetch_candles(aapl, _TF.D1, now - 20 * 86_400_000, now)
    assert len(days) >= 10
    minutes = await adapter.fetch_candles(
        aapl, _TF.M5, now - 5 * 86_400_000, now, 50
    )
    for candle in [*days, *minutes]:
        contract_validate("market.schema.json", "Candle", candle.to_contract())
        assert sessions.XNYS.is_aligned(candle.timeframe, candle.start_ms)
    snapshot = await adapter.fetch_snapshot(aapl)
    assert snapshot.last is not None
    await adapter.close()
