"""Contract tests: TWSE, Nasdaq/NYSE (Alpaca) and Treasury adapters.

TWSE (OpenAPI, STOCK_DAY, holiday schedules, MIS), the Nasdaq Trader
symbol directory and the Treasury CSVs were captured live on 2026-11-23;
Alpaca responses are hand-written from its documentation (no key on the
build host, see ``fixtures/venues/alpaca/README.md``). Every produced
object must validate against the JSON contracts and follow the instrument's
session calendar.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime
import decimal
import json
import pathlib
from collections.abc import AsyncIterator
from collections.abc import Callable
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import respx

from joseph.domain import clock as clock_lib
from joseph.domain import errors
from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import sessions
from joseph.domain import timeframes
from joseph.market_data.adapters import alpaca
from joseph.market_data.adapters import factory
from joseph.market_data.adapters import treasury
from joseph.market_data.adapters import twse
from joseph.market_data.app import ports
from joseph.market_data.app import registry

Validate = Callable[[str, str, object], None]
_D = decimal.Decimal
_TF = timeframes.Timeframe
_FIXTURES = pathlib.Path(__file__).resolve().parents[3] / "fixtures" / "venues"
_TPE = datetime.timezone(datetime.timedelta(hours=8))
_TWSE_MS = 1_795_408_205_000
"""2026-11-23 12:30:05 Taipei, just after the newest MIS capture."""
_US_SESSION = sessions.XNYS.session(datetime.date(2026, 11, 20))
assert _US_SESSION is not None
_US_OPEN = _US_SESSION.open_ms
_ALPACA_MS = _US_OPEN + 7 * 60_000 + 30_000
"""2026-11-20 09:37:30 New York."""
_KEYS = alpaca.AlpacaConfig(key_id="test-key", secret_key="test-secret")
_TSMC = instruments.InstrumentId.parse("twse:stock:2330")
_ETF50 = instruments.InstrumentId.parse("twse:etf:0050")
_AAPL = instruments.InstrumentId.parse("nasdaq:stock:AAPL")
_UST10 = instruments.InstrumentId.parse("treasury:rate:UST10Y")


def _load(venue: str, name: str) -> Any:
    """Reads a JSON fixture.

    Args:
        venue: Venue directory.
        name: File name without extension.

    Returns:
        Parsed JSON.
    """
    return json.loads((_FIXTURES / venue / f"{name}.json").read_text("utf-8"))


class _Frames:
    """Replays WebSocket frames, then idles."""

    def __init__(self, frames: list[str]) -> None:
        """Initializes the connection.

        Args:
            frames: Frames to deliver.
        """
        self.frames = list(frames)
        self.sent: list[str] = []

    async def send(self, message: str) -> None:
        """Records a sent frame.

        Args:
            message: Frame.
        """
        self.sent.append(message)

    async def recv(self) -> str:
        """Returns the next frame or idles.

        Returns:
            Frame.
        """
        if self.frames:
            return self.frames.pop(0)
        await asyncio.sleep(3600)
        return ""


def _connector(
    frames: list[str], opened: list[_Frames] | None = None
) -> Callable[[str], object]:
    """Builds a connector replaying frames.

    Args:
        frames: Frames.
        opened: Optional list receiving each opened connection.

    Returns:
        Connector.
    """

    @contextlib.asynccontextmanager
    async def connect(url: str) -> AsyncIterator[_Frames]:
        """Yields a replaying connection.

        Args:
            url: Ignored URL.

        Yields:
            Connection.
        """
        del url
        connection = _Frames(frames)
        if opened is not None:
            opened.append(connection)
        yield connection

    return connect


def _adapter(
    venue: instruments.Venue,
    clock: clock_lib.Clock,
    *,
    keys: alpaca.AlpacaConfig | None = _KEYS,
    frames: list[str] | None = None,
    opened: list[_Frames] | None = None,
) -> ports.MarketDataPort:
    """Builds one adapter over the fixture routes.

    Args:
        venue: Venue.
        clock: Clock.
        keys: Alpaca configuration.
        frames: WebSocket frames.
        opened: Receives opened connections.

    Returns:
        Adapter.
    """
    return factory.build_adapters(
        [venue],
        clock,
        client=httpx.AsyncClient(),
        connect=_connector(frames or [], opened),  # type: ignore[arg-type]
        alpaca_config=keys,
    )[venue]


@pytest.fixture(autouse=True)
def _restore_xtai() -> Iterator[None]:
    """Restores runtime XTAI closures after each test.

    Yields:
        Nothing.
    """
    saved = set(sessions.XTAI._extra_closures)
    yield
    sessions.XTAI._extra_closures.clear()
    sessions.XTAI.add_closures(saved)


# TWSE ----------------------------------------------------------------


async def test_twse_instruments_follow_listing_rules(
    venue_api: respx.Router, contract_validate: Validate
) -> None:
    """Stocks and ETFs carry TWSE ticks, limits, lots and asset classes."""
    del venue_api
    rows = await _adapter(
        instruments.Venue.TWSE, clock_lib.ManualClock(_TWSE_MS)
    ).list_instruments()
    by_id = {str(r.instrument_id): r for r in rows}
    for row in rows:
        contract_validate("market.schema.json", "Instrument", row.to_contract())
        assert row.calendar == "XTAI"
        assert row.board_lot == _D(1000) and row.lot_size == _D(1)
        assert row.key.quote == "TWD" and row.listing_exchange == "TWSE"
    tsmc = by_id["twse:stock:2330"]
    assert (tsmc.display_symbol, tsmc.local_name, tsmc.name) == (
        "2330",
        "台積電",
        "TSMC",
    )
    assert by_id["twse:etf:00646"].name == "Yuanta S&P 500 ETF"
    assert tsmc.tick_ladder == twse.STOCK_TICKS
    assert tsmc.tick_for(_D(2030)) == _D(5)
    assert tsmc.tick_for(_D(209)) == _D("0.5")
    assert tsmc.price_limit_pct == _D(10)
    assert tsmc.status is instruments.InstrumentStatus.TRADING
    etf = by_id["twse:etf:0050"]
    assert etf.tick_ladder == twse.ETF_TICKS
    assert etf.tick_for(_D("86.9")) == _D("0.05")
    assert etf.tick_for(_D("28.21")) == _D("0.01")
    assert etf.asset_class is instruments.AssetClass.EQUITY
    bond = by_id["twse:etf:00865B"]
    assert bond.asset_class is instruments.AssetClass.BOND
    assert bond.price_limit_pct is None
    assert by_id["twse:etf:00631L"].price_limit_pct == _D(20)
    assert by_id["twse:etf:00632R"].price_limit_pct == _D(10)
    assert by_id["twse:etf:00646"].price_limit_pct is None
    for excluded in ("00625K", "01001T", "2881A"):
        assert not any(
            r.instrument_id.venue_market_id == excluded for r in rows
        )


async def test_registry_search_matches_local_and_english_names(
    venue_api: respx.Router,
) -> None:
    """Chinese and English names find TWSE listings."""
    del venue_api
    adapter = _adapter(instruments.Venue.TWSE, clock_lib.ManualClock(_TWSE_MS))
    lookup = registry.InstrumentRegistry({instruments.Venue.TWSE: adapter})
    by_chinese = await lookup.search("台積")
    assert [str(r.instrument_id) for r in by_chinese] == ["twse:stock:2330"]
    by_english = await lookup.search("hon hai")
    assert [str(r.instrument_id) for r in by_english] == ["twse:stock:2317"]


async def test_twse_daily_history_is_session_aligned(
    venue_api: respx.Router, contract_validate: Validate
) -> None:
    """STOCK_DAY rows become final XTAI daily bars in shares."""
    del venue_api
    adapter = _adapter(instruments.Venue.TWSE, clock_lib.ManualClock(_TWSE_MS))
    start = int(datetime.datetime(2026, 11, 1, tzinfo=_TPE).timestamp() * 1000)
    rows = await adapter.fetch_candles(_TSMC, _TF.D1, start, _TWSE_MS)
    raw = _load("twse", "stock_day_2330")["data"]
    final = [r for r in rows if r.is_final]
    assert len(final) == len(raw)
    first = final[0]
    assert first.base_volume == twse.number(raw[0][1])
    assert first.close == twse.number(raw[0][6])
    assert first.trade_count == int(twse.number(raw[0][8]) or 0)
    for candle in rows:
        contract_validate("market.schema.json", "Candle", candle.to_contract())
        found = sessions.XTAI.session(
            datetime.datetime.fromtimestamp(candle.start_ms / 1000, _TPE).date()
        )
        assert found is not None and found.open_ms == candle.start_ms
    live = rows[-1]
    assert not live.is_final and live.source == twse.MIS_SOURCE
    assert live.base_volume == _D(53164) * 1000
    weeks = await adapter.fetch_candles(_TSMC, _TF.W1, start, _TWSE_MS)
    assert all(not w.is_final for w in weeks[-1:])
    assert all(w.is_final for w in weeks[:-1])
    assert sum(w.base_volume for w in weeks) == sum(r.base_volume for r in rows)
    months = await adapter.fetch_candles(_TSMC, _TF.MN1, start, _TWSE_MS, 1)
    assert len(months) == 1 and months[0].high == max(r.high for r in rows)


async def test_twse_snapshot_and_book_from_mis(
    venue_api: respx.Router, contract_validate: Validate
) -> None:
    """MIS gives the last trade, best quotes and a 5-level book in shares."""
    del venue_api
    clock = clock_lib.ManualClock(_TWSE_MS)
    adapter = _adapter(instruments.Venue.TWSE, clock)
    snapshot = await adapter.fetch_snapshot(_ETF50)
    contract_validate(
        "market.schema.json",
        "MarketSnapshot",
        snapshot.to_contract(_TWSE_MS, 30_000),
    )
    assert snapshot.last == _D(87)
    assert (snapshot.best_bid, snapshot.best_ask) == (_D(87), _D("87.05"))
    assert snapshot.volume_24h_base == _D(431093) * 1000
    assert snapshot.change_24h_pct is not None and snapshot.change_24h_pct > 0
    book = await adapter.fetch_order_book(_TSMC, 10)
    contract_validate("market.schema.json", "OrderBook", book.to_contract())
    assert len(book.bids) == len(book.asks) == 5
    assert book.asks[0].price == _D(2045) and book.asks[0].quantity == _D(
        112000
    )
    assert book.bids[0].price == _D(2040)
    assert [b.price for b in book.bids] == sorted(
        (b.price for b in book.bids), reverse=True
    )
    tsmc = await adapter.fetch_snapshot(_TSMC)
    assert tsmc.last == _D(2040)  # MIS trade fallback when z is "-".


async def test_twse_bulk_snapshots_are_last_session_values(
    venue_api: respx.Router, contract_validate: Validate
) -> None:
    """STOCK_DAY_ALL rows are stamped at that session's close."""
    del venue_api
    adapter = _adapter(instruments.Venue.TWSE, clock_lib.ManualClock(_TWSE_MS))
    rows = {str(s.instrument_id): s for s in await adapter.fetch_snapshots()}
    friday = sessions.XTAI.session(datetime.date(2026, 11, 20))
    assert friday is not None
    etf = rows["twse:etf:0050"]
    assert etf.as_of_ms == friday.close_ms
    assert etf.last == _D("84.45")
    assert etf.turnover_24h_quote == _D(74858941327)
    for row in rows.values():
        contract_validate(
            "market.schema.json",
            "MarketSnapshot",
            row.to_contract(_TWSE_MS, 30_000),
        )


async def test_twse_intraday_bars_come_from_recorded_snapshots(
    venue_api: respx.Router, contract_validate: Validate
) -> None:
    """Recorded MIS snapshots become 1m and 5m bars with classified delta."""
    del venue_api
    responses = _load("twse", "mis_recorded")["responses"]
    clock = clock_lib.ManualClock(responses[0]["received_ms"])
    adapter = _adapter(instruments.Venue.TWSE, clock)
    assert isinstance(adapter, twse.TwseAdapter)
    session = sessions.XTAI.session(datetime.date(2026, 11, 23))
    assert session is not None
    assert (
        await adapter.fetch_candles(
            _ETF50, _TF.M1, session.open_ms, session.close_ms
        )
        == []
    )
    for item in responses:
        clock.current_ms = item["received_ms"]
        quotes = [
            q
            for r in item["response"]["msgArray"]
            if (q := twse.parse_mis_record(r, item["received_ms"])) is not None
        ]
        adapter.ingest(quotes)
    ones = await adapter.fetch_candles(
        _ETF50, _TF.M1, session.open_ms, session.close_ms
    )
    assert ones
    for candle in ones:
        contract_validate("market.schema.json", "Candle", candle.to_contract())
        assert sessions.XTAI.is_aligned(_TF.M1, candle.start_ms)
    finals = [c for c in ones if c.is_final]
    assert finals and not ones[-1].is_final
    classified = [c for c in ones if c.taker_buy_base_volume is not None]
    assert all(
        c.buy_volume_method is market.BuyVolumeMethod.CLASSIFIED
        for c in classified
    )
    fives = await adapter.fetch_candles(
        _ETF50, _TF.M5, session.open_ms, session.close_ms
    )
    assert fives and all(
        sessions.XTAI.is_aligned(_TF.M5, c.start_ms) for c in fives
    )
    assert await adapter.close() is None


async def test_twse_refuses_what_it_does_not_have(
    venue_api: respx.Router,
) -> None:
    """Seconds bars, streams, trades, funding and OI are unsupported."""
    del venue_api
    adapter = _adapter(instruments.Venue.TWSE, clock_lib.ManualClock(_TWSE_MS))
    with pytest.raises(errors.UnsupportedError):
        await adapter.fetch_candles(_TSMC, _TF.S5, 0, 1)
    with pytest.raises(errors.UnsupportedError):
        adapter.stream_candles(_TSMC, _TF.M1)
    with pytest.raises(errors.UnsupportedError):
        adapter.stream_trades(_TSMC)
    with pytest.raises(errors.UnsupportedError):
        await adapter.fetch_trades(_TSMC, 0, 1)
    with pytest.raises(errors.UnsupportedError):
        await adapter.fetch_open_interest(_TSMC)
    with pytest.raises(errors.UnsupportedError):
        await adapter.fetch_funding(_TSMC)
    caps = adapter.capabilities()
    assert caps.calendar == "XTAI"
    assert caps.market_data.classified_delta
    assert not caps.market_data.taker_volume
    assert not set(caps.timeframes) & set(timeframes.SECONDS)


# Nasdaq / NYSE via Alpaca ------------------------------------------------


async def test_directory_maps_listings_to_venues(
    venue_api: respx.Router, contract_validate: Validate
) -> None:
    """Listings split by exchange; tests, preferreds and units are skipped."""
    del venue_api
    clock = clock_lib.ManualClock(_ALPACA_MS)
    nasdaq = {
        r.instrument_id.venue_market_id: r
        for r in await _adapter(
            instruments.Venue.NASDAQ, clock
        ).list_instruments()
    }
    nyse = {
        r.instrument_id.venue_market_id: r
        for r in await _adapter(
            instruments.Venue.NYSE, clock
        ).list_instruments()
    }
    for row in [*nasdaq.values(), *nyse.values()]:
        contract_validate("market.schema.json", "Instrument", row.to_contract())
        assert row.calendar == "XNYS"
        assert row.tick_for(_D("0.5")) == _D("0.0001")
        assert row.tick_for(_D(5)) == _D("0.01")
    assert str(nasdaq["AAPL"].instrument_id) == "nasdaq:stock:AAPL"
    assert nasdaq["AAPL"].name == "Apple Inc. - Common Stock"
    assert nasdaq["AAPL"].local_name is None
    assert str(nasdaq["QQQ"].instrument_id) == "nasdaq:etf:QQQ"
    assert nasdaq["TLT"].asset_class is instruments.AssetClass.BOND
    assert nasdaq["QQQ"].asset_class is instruments.AssetClass.EQUITY
    assert "ZXYZ.A" not in nasdaq
    assert nyse["BRK.B"].listing_exchange == "NYSE"
    assert str(nyse["BRK.B"].instrument_id) == "nyse:stock:BRK.B"
    assert nyse["AGG"].listing_exchange == "NYSE Arca"
    assert nyse["AGG"].asset_class is instruments.AssetClass.BOND
    assert nyse["SPY"].asset_class is instruments.AssetClass.EQUITY
    assert nyse["SGOV"].asset_class is instruments.AssetClass.BOND
    for skipped in (
        "GOVT",
        "ABR$D",
        "AAC.U",
        "AAC.W",
        "AIIA.R",
        "ZEXIT",
        "MTEST",
    ):
        assert skipped not in nyse


async def test_alpaca_without_keys_asks_for_them(
    venue_api: respx.Router,
) -> None:
    """The venues stay listed but data requests ask for Alpaca keys."""
    del venue_api
    adapter = _adapter(
        instruments.Venue.NASDAQ, clock_lib.ManualClock(_ALPACA_MS), keys=None
    )
    caps = adapter.capabilities()
    assert caps.requires_credentials and not caps.credentials_configured
    assert await adapter.list_instruments()
    with pytest.raises(
        errors.VenueUnavailableError, match="configure Alpaca keys"
    ):
        await adapter.fetch_candles(_AAPL, _TF.M1, 0, _ALPACA_MS)
    with pytest.raises(
        errors.VenueUnavailableError, match="configure Alpaca keys"
    ):
        await adapter.fetch_snapshots()


async def test_alpaca_minute_bars_are_session_bars(
    venue_api: respx.Router, contract_validate: Validate
) -> None:
    """Pages join, pre-market drops, settled bars are final, 5m aggregates."""
    calls = venue_api
    adapter = _adapter(
        instruments.Venue.NASDAQ, clock_lib.ManualClock(_ALPACA_MS)
    )
    rows = await adapter.fetch_candles(
        _AAPL, _TF.M1, _US_OPEN - 60_000, _ALPACA_MS
    )
    assert [r.start_ms - _US_OPEN for r in rows] == [
        i * 60_000 for i in range(7)
    ]
    assert [r.is_final for r in rows] == [True] * 6 + [False]
    for candle in rows:
        contract_validate("market.schema.json", "Candle", candle.to_contract())
    requests = [
        c.request
        for c in calls.calls
        if c.request.url.host == "data.alpaca.markets"
    ]
    assert requests[-1].url.params["page_token"]
    assert requests[-1].url.params["adjustment"] == "split"
    assert requests[-1].url.params["feed"] == "iex"
    assert "test-secret" not in repr(_KEYS)
    fives = await adapter.fetch_candles(_AAPL, _TF.M5, _US_OPEN, _ALPACA_MS)
    assert [f.start_ms for f in fives] == [_US_OPEN, _US_OPEN + 300_000]
    assert fives[0].base_volume == sum(r.base_volume for r in rows[:5])
    assert fives[0].is_final and not fives[1].is_final
    hours = await adapter.fetch_candles(_AAPL, _TF.H1, _US_OPEN, _ALPACA_MS)
    assert len(hours) == 1 and hours[0].start_ms == _US_OPEN


async def test_alpaca_daily_bars_are_restamped(
    venue_api: respx.Router, contract_validate: Validate
) -> None:
    """New York midnight bars move to the XNYS session open."""
    del venue_api
    adapter = _adapter(
        instruments.Venue.NASDAQ, clock_lib.ManualClock(_ALPACA_MS)
    )
    start = _US_OPEN - 4 * 86_400_000
    days = await adapter.fetch_candles(
        _AAPL, _TF.D1, start - 3_600_000, _ALPACA_MS
    )
    assert len(days) == 5
    assert days[-1].start_ms == _US_OPEN and not days[-1].is_final
    assert all(d.is_final for d in days[:-1])
    for candle in days:
        contract_validate("market.schema.json", "Candle", candle.to_contract())
    weeks = await adapter.fetch_candles(
        _AAPL, _TF.W1, start - 3_600_000, _ALPACA_MS
    )
    assert len(weeks) == 1 and weeks[0].base_volume == sum(
        d.base_volume for d in days
    )


async def test_alpaca_seconds_history_uses_conditions_and_lee_ready(
    venue_api: respx.Router, contract_validate: Validate
) -> None:
    """REST trades and quotes build classified 1s bars inside the session."""
    del venue_api
    adapter = _adapter(
        instruments.Venue.NASDAQ, clock_lib.ManualClock(_ALPACA_MS)
    )
    rows = await adapter.fetch_candles(
        _AAPL, _TF.S1, _US_OPEN, _US_OPEN + 5_000
    )
    by_second = {(r.start_ms - _US_OPEN) // 1000: r for r in rows}
    assert sorted(by_second) == [0, 1, 3, 4]
    assert by_second[0].base_volume == _D(105)
    assert by_second[0].taker_buy_base_volume == _D(105)
    assert by_second[1].taker_buy_base_volume == _D(200)
    assert by_second[1].low == _D("230.11")
    assert by_second[3].base_volume == _D(300)
    assert by_second[4].taker_buy_base_volume == _D(0)
    for candle in rows:
        contract_validate("market.schema.json", "Candle", candle.to_contract())
        assert candle.buy_volume_method is market.BuyVolumeMethod.CLASSIFIED
    trades = await adapter.fetch_trades(
        _AAPL, _US_OPEN - 5_000, _US_OPEN + 5_000
    )
    assert len(trades) == 8
    assert {t.aggressor_side for t in trades} == {market.AggressorSide.UNKNOWN}


async def test_alpaca_snapshots_and_unsupported_parts(
    venue_api: respx.Router, contract_validate: Validate
) -> None:
    """Snapshots validate; book, OI and funding are unsupported."""
    del venue_api
    clock = clock_lib.ManualClock(_ALPACA_MS)
    adapter = _adapter(instruments.Venue.NASDAQ, clock)
    snapshot = await adapter.fetch_snapshot(_AAPL)
    contract_validate(
        "market.schema.json",
        "MarketSnapshot",
        snapshot.to_contract(_ALPACA_MS, 30_000),
    )
    assert snapshot.last == _D("230.35")
    assert snapshot.best_bid == _D("230.34")
    bulk = {
        s.instrument_id.venue_market_id for s in await adapter.fetch_snapshots()
    }
    assert bulk == {"AAPL", "QQQ", "TLT"}
    nyse = _adapter(instruments.Venue.NYSE, clock)
    assert {
        s.instrument_id.venue_market_id for s in await nyse.fetch_snapshots()
    } == {"BRK.B", "SPY"}
    for call in (
        adapter.fetch_order_book(_AAPL, 5),
        adapter.fetch_open_interest(_AAPL),
        adapter.fetch_funding(_AAPL),
    ):
        with pytest.raises(errors.UnsupportedError):
            await call


async def test_alpaca_stream_delivers_bars_trades_and_halts(
    venue_api: respx.Router, contract_validate: Validate
) -> None:
    """One authenticated connection streams bars, revisions and statuses."""
    del venue_api
    frames = _load("alpaca", "ws_frames")["frames"]
    opened: list[_Frames] = []
    clock = clock_lib.ManualClock(_ALPACA_MS)
    adapter = _adapter(
        instruments.Venue.NASDAQ, clock, frames=frames, opened=opened
    )
    stream = adapter.stream_candles(_AAPL, _TF.M1)
    seen = [await asyncio.wait_for(anext(stream), 5) for _ in range(3)]
    await stream.aclose()  # type: ignore[attr-defined]
    for candle in seen:
        contract_validate("market.schema.json", "Candle", candle.to_contract())
    assert [c.start_ms - _US_OPEN for c in seen] == [300_000, 300_000, 360_000]
    assert seen[1].close == _D("230.27")  # updatedBars revision.
    assert all(c.is_final and c.source == "alpaca.ws" for c in seen)
    auth = json.loads(opened[0].sent[0])
    assert auth == {
        "action": "auth",
        "key": "test-key",
        "secret": "test-secret",
    }
    subscribe = json.loads(opened[0].sent[1])
    assert subscribe["bars"] == ["AAPL"] and subscribe["statuses"] == ["AAPL"]


async def test_alpaca_trade_and_seconds_streams(
    venue_api: respx.Router,
) -> None:
    """Streamed trades have no aggressor; seconds bars are classified."""
    del venue_api
    frames = _load("alpaca", "ws_frames")["frames"]
    clock = clock_lib.ManualClock(_ALPACA_MS)
    adapter = _adapter(instruments.Venue.NASDAQ, clock, frames=frames)
    trades = adapter.stream_trades(_AAPL)
    first = await asyncio.wait_for(anext(trades), 5)
    await trades.aclose()  # type: ignore[attr-defined]
    assert first.aggressor_side is market.AggressorSide.UNKNOWN
    adapter = _adapter(instruments.Venue.NASDAQ, clock, frames=frames)
    bars = adapter.stream_candles(_AAPL, _TF.S1)
    candle = await asyncio.wait_for(anext(bars), 5)
    await bars.aclose()  # type: ignore[attr-defined]
    # The first streamed trade's second is partial and never emitted.
    assert candle.start_ms == _US_OPEN + 1_000
    assert candle.taker_buy_base_volume == _D(200)
    assert candle.buy_volume_method is market.BuyVolumeMethod.CLASSIFIED


async def test_alpaca_status_marks_halts(venue_api: respx.Router) -> None:
    """A trading-halt status marks the streamed instrument HALTED."""
    del venue_api
    frames = _load("alpaca", "ws_frames")["frames"]
    clock = clock_lib.ManualClock(_ALPACA_MS + 86_400_000)
    adapter = _adapter(instruments.Venue.NASDAQ, clock, frames=frames)
    stream = adapter.stream_candles(_AAPL, _TF.D1)
    first = await asyncio.wait_for(anext(stream), 5)
    assert first.timeframe is _TF.D1
    for _ in range(50):
        listed = {
            r.instrument_id.venue_market_id: r
            for r in await adapter.list_instruments()
        }
        if listed["AAPL"].status is instruments.InstrumentStatus.HALTED:
            break
        await asyncio.sleep(0.01)
    await stream.aclose()  # type: ignore[attr-defined]
    assert listed["AAPL"].status is instruments.InstrumentStatus.HALTED


async def test_alpaca_stream_auth_failure_is_reported(
    venue_api: respx.Router,
) -> None:
    """A rejected key ends the stream with a clear error."""
    del venue_api
    errors_ = _load("alpaca", "ws_errors")
    frames = [_load("alpaca", "ws_frames")["frames"][0], errors_["auth_failed"]]
    adapter = _adapter(
        instruments.Venue.NASDAQ,
        clock_lib.ManualClock(_ALPACA_MS),
        frames=frames,
    )
    stream = adapter.stream_trades(_AAPL)
    with pytest.raises(errors.VenueUnavailableError, match="402"):
        await asyncio.wait_for(anext(stream), 5)


# Treasury ----------------------------------------------------------------


async def test_treasury_lists_nominal_and_real_series(
    venue_api: respx.Router, contract_validate: Validate
) -> None:
    """Every published tenor is a non-tradable BOND rate in percent."""
    del venue_api
    rows = await _adapter(
        instruments.Venue.TREASURY, clock_lib.ManualClock(_TWSE_MS)
    ).list_instruments()
    assert len(rows) == len(treasury.TENORS) == 19
    for row in rows:
        contract_validate("market.schema.json", "Instrument", row.to_contract())
        assert row.asset_class is instruments.AssetClass.BOND
        assert row.status is instruments.InstrumentStatus.UNKNOWN
        assert row.calendar == "USBOND"
    ids = {str(r.instrument_id) for r in rows}
    assert {"treasury:rate:UST1.5M", "treasury:rate:TIPS30Y"} <= ids
    names = {r.instrument_id.venue_market_id: r.name for r in rows}
    assert names["UST10Y"] == "US Treasury 10-year par yield"
    assert names["TIPS5Y"] == "US Treasury 5-year par real yield"


async def test_treasury_daily_weekly_monthly_bars(
    venue_api: respx.Router, contract_validate: Validate
) -> None:
    """Yields are flat USBOND bars; weeks and months aggregate them."""
    del venue_api
    adapter = _adapter(
        instruments.Venue.TREASURY, clock_lib.ManualClock(_TWSE_MS)
    )
    start = int(
        datetime.datetime(2026, 11, 1, tzinfo=datetime.UTC).timestamp() * 1000
    )
    days = await adapter.fetch_candles(_UST10, _TF.D1, start, _TWSE_MS)
    assert days[-1].close == _D("4.92")
    friday = sessions.USBOND.session(datetime.date(2026, 11, 20))
    assert friday is not None and days[-1].start_ms == friday.open_ms
    for candle in days:
        contract_validate("market.schema.json", "Candle", candle.to_contract())
        assert candle.open == candle.high == candle.low == candle.close
        assert candle.base_volume == 0 and candle.is_final
    weeks = await adapter.fetch_candles(_UST10, _TF.W1, start, _TWSE_MS)
    assert weeks[-1].close == _D("4.92") and weeks[-1].is_final
    months = await adapter.fetch_candles(_UST10, _TF.MN1, start, _TWSE_MS)
    assert len(months) == 1 and not months[0].is_final
    assert months[0].high == max(d.high for d in days)
    limited = await adapter.fetch_candles(_UST10, _TF.D1, 0, _TWSE_MS, 3)
    assert [d.start_ms for d in limited] == [d.start_ms for d in days[-3:]]


async def test_treasury_handles_tenor_gaps(venue_api: respx.Router) -> None:
    """Tenors absent from a year (or blank) produce no bars."""
    del venue_api
    adapter = _adapter(
        instruments.Venue.TREASURY, clock_lib.ManualClock(_TWSE_MS)
    )
    year = (
        int(
            datetime.datetime(2002, 1, 1, tzinfo=datetime.UTC).timestamp()
            * 1000
        ),
        int(
            datetime.datetime(2003, 1, 1, tzinfo=datetime.UTC).timestamp()
            * 1000
        ),
    )
    thirty = await adapter.fetch_candles(
        instruments.InstrumentId.parse("treasury:rate:UST30Y"), _TF.D1, *year
    )
    gap = sessions.USBOND.session(datetime.date(2002, 2, 19))
    assert gap is not None
    assert thirty and all(c.start_ms < gap.open_ms for c in thirty)
    one = await adapter.fetch_candles(
        instruments.InstrumentId.parse("treasury:rate:UST1M"),
        _TF.D1,
        int(
            datetime.datetime(1990, 1, 1, tzinfo=datetime.UTC).timestamp()
            * 1000
        ),
        int(
            datetime.datetime(1991, 1, 1, tzinfo=datetime.UTC).timestamp()
            * 1000
        ),
    )
    assert one == []


async def test_treasury_snapshots_and_unsupported(
    venue_api: respx.Router, contract_validate: Validate
) -> None:
    """Snapshots carry the newest yield; books and streams are refused."""
    del venue_api
    adapter = _adapter(
        instruments.Venue.TREASURY, clock_lib.ManualClock(_TWSE_MS)
    )
    snapshot = await adapter.fetch_snapshot(_UST10)
    assert snapshot.last == _D("4.92")
    rows = await adapter.fetch_snapshots()
    assert len(rows) == 19
    for row in rows:
        contract_validate(
            "market.schema.json",
            "MarketSnapshot",
            row.to_contract(_TWSE_MS, 30_000),
        )
    with pytest.raises(errors.UnsupportedError):
        await adapter.fetch_order_book(_UST10, 5)
    with pytest.raises(errors.UnsupportedError):
        adapter.stream_candles(_UST10, _TF.D1)
    with pytest.raises(errors.UnsupportedError):
        await adapter.fetch_candles(_UST10, _TF.H1, 0, 1)
    with pytest.raises(errors.NotFoundError):
        await adapter.fetch_snapshot(
            instruments.InstrumentId.parse("treasury:rate:UST99Y")
        )
