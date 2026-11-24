# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Session-aware market data on exchange calendars (XNYS, XTAI)."""

from __future__ import annotations

import datetime
import decimal
import zoneinfo
from collections.abc import AsyncGenerator
from collections.abc import Sequence

import pytest

from joseph.domain import clock as clock_lib
from joseph.domain import errors
from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import quality
from joseph.domain import sessions
from joseph.domain import timeframes
from joseph.indicators import testing
from joseph.market_data.adapters import base
from joseph.market_data.adapters import mock
from joseph.market_data.adapters import ws
from joseph.market_data.app import candle_store
from joseph.market_data.app import feed_router
from joseph.market_data.app import registry
from joseph.market_data.app import service as service_lib
from joseph.market_data.domain import candles as candle_rules
from joseph.market_data.domain import trade_bars
from joseph.market_data.domain import trades as trades_lib

TF = timeframes.Timeframe
NY = zoneinfo.ZoneInfo("America/New_York")
TPE = zoneinfo.ZoneInfo("Asia/Taipei")
ACME = testing.STOCK_INSTRUMENT
AAPL = instruments.InstrumentId.parse("nasdaq:stock:AAPL")
TSMC = instruments.InstrumentId.parse("twse:stock:2330")
XNYS = sessions.XNYS
_D = decimal.Decimal


def _ny(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> int:
    """Returns UTC epoch ms of a New York wall time.

    Args:
        year: Year.
        month: Month.
        day: Day.
        hour: Hour.
        minute: Minute.

    Returns:
        Epoch milliseconds.
    """
    moment = datetime.datetime(year, month, day, hour, minute, tzinfo=NY)
    return int(moment.timestamp() * 1000)


def _tpe(
    year: int, month: int, day: int, hour: int = 0, minute: int = 0
) -> int:
    """Returns UTC epoch ms of a Taipei wall time.

    Args:
        year: Year.
        month: Month.
        day: Day.
        hour: Hour.
        minute: Minute.

    Returns:
        Epoch milliseconds.
    """
    moment = datetime.datetime(year, month, day, hour, minute, tzinfo=TPE)
    return int(moment.timestamp() * 1000)


def _hour_starts(first: int, count: int) -> list[int]:
    """Returns consecutive XNYS 1h bar starts.

    Args:
        first: Start of the first bar in epoch milliseconds.
        count: Number of bars.

    Returns:
        The starts of ``count`` AAPL session bars from ``first`` on.
    """
    return testing.bar_starts(TF.H1, first, count, AAPL)


def _trade(ts: int, price: str, trade_id: str) -> market.Trade:
    """Builds a buyer-aggressed AAPL trade.

    Args:
        ts: Venue and receive time in epoch milliseconds.
        price: Price as a decimal string.
        trade_id: Venue trade id.

    Returns:
        A one-share trade.
    """
    return market.Trade(
        instrument_id=AAPL,
        trade_id=trade_id,
        venue_event_ts_ms=ts,
        received_at_ms=ts,
        price=_D(price),
        base_quantity=_D(1),
        aggressor_side=market.AggressorSide.BUY,
        is_aggregated=False,
    )


# Gaps -----------------------------------------------------------------


def test_no_gaps_across_a_weekend_and_a_holiday() -> None:
    """Wed 25 Nov to Mon 30 Nov 2026 skips Thanksgiving and the weekend."""
    first = _ny(2026, 11, 25, 9, 30)
    starts = _hour_starts(first, 7 + 4 + 7)
    assert starts[7] == _ny(2026, 11, 27, 9, 30)  # Thursday was closed.
    assert starts[11] == _ny(2026, 11, 30, 9, 30)
    end = XNYS.end_of(TF.H1, starts[-1])
    gaps = candle_rules.find_gaps(starts, TF.H1, first, end, calendar=XNYS)
    assert gaps == []


def test_a_missing_session_bar_is_a_gap() -> None:
    """Only the absent 10:30 Monday bar is reported, not the night."""
    first = _ny(2026, 11, 27, 9, 30)
    starts = _hour_starts(first, 11)
    missing = _ny(2026, 11, 30, 10, 30)
    present = [s for s in starts if s != missing]
    gaps = candle_rules.find_gaps(
        present, TF.H1, first, XNYS.end_of(TF.H1, starts[-1]), calendar=XNYS
    )
    assert gaps == [(missing, _ny(2026, 11, 30, 11, 30))]


def test_gap_scan_rounds_up_to_the_next_session_bar() -> None:
    """A range starting at night begins at the next 09:30 open."""
    friday_night = _ny(2026, 11, 27, 20)
    gaps = candle_rules.find_gaps(
        [], TF.H1, friday_night, _ny(2026, 11, 30, 10, 0), calendar=XNYS
    )
    assert gaps == [(_ny(2026, 11, 30, 9, 30), _ny(2026, 11, 30, 10, 0))]


def test_store_final_gaps_use_the_instrument_calendar() -> None:
    """The candle store reports no gap over an XNYS weekend."""
    store = candle_store.CandleStore()
    starts = _hour_starts(_ny(2026, 11, 27, 9, 30), 5)
    for start in starts:
        store.upsert(
            testing.make_candle(TF.H1, start, 1, 2, 1, 2, instrument_id=AAPL)
        )
    end = XNYS.end_of(TF.H1, starts[-1])
    assert store.final_gaps(AAPL, TF.H1, starts[0], end) == []


# Adapter helpers --------------------------------------------------------


def test_make_candle_follows_the_session_grid() -> None:
    """09:30 bars are accepted, the last bar ends at the close."""
    last = _ny(2026, 11, 27, 12, 30)
    candle = base.make_candle(
        instrument_id=AAPL,
        timeframe=TF.H1,
        start_ms=last,
        open_=_D(1),
        high=_D(2),
        low=_D(1),
        close=_D(2),
        base_volume=_D(5),
        now_ms=_ny(2026, 11, 27, 14),
        source="test",
        taker_buy_base_volume=_D(3),
        taker_buy_method=market.BuyVolumeMethod.CLASSIFIED,
    )
    assert candle.end_ms == _ny(2026, 11, 27, 13)  # Early close.
    assert candle.is_final
    assert candle.buy_volume_method is market.BuyVolumeMethod.CLASSIFIED
    with pytest.raises(errors.UnsupportedError, match="XNYS sessions"):
        base.make_candle(
            instrument_id=AAPL,
            timeframe=TF.H1,
            start_ms=_ny(2026, 11, 27, 10),
            open_=_D(1),
            high=_D(1),
            low=_D(1),
            close=_D(1),
            base_volume=_D(1),
            now_ms=_ny(2026, 11, 27, 14),
            source="test",
        )


def test_paging_helpers_take_the_calendar() -> None:
    """``first_start`` and ``newest_window_start`` count session bars."""
    saturday = _ny(2026, 11, 28, 12)
    monday_open = _ny(2026, 11, 30, 9, 30)
    assert base.first_start(TF.H1, saturday, calendar=XNYS) == monday_open
    end = _ny(2026, 11, 30, 10, 30)
    start = base.newest_window_start(
        TF.H1, _ny(2026, 11, 20), end, 3, calendar=XNYS
    )
    assert start == _ny(2026, 11, 27, 11, 30)
    assert (
        base.newest_window_start(
            TF.H1, saturday, saturday + 1, 3, calendar=XNYS
        )
        == monday_open
    )


def test_guard_calendar_comes_from_the_streamed_candles() -> None:
    """Stream recovery uses the streamed instrument's calendar."""
    guard = ws._Guard()
    assert guard.calendar(sessions.CONTINUOUS) is sessions.CONTINUOUS
    guard.accept(
        testing.make_candle(
            TF.H1, _ny(2026, 11, 27, 9, 30), 1, 1, 1, 1, instrument_id=AAPL
        )
    )
    assert guard.calendar(sessions.CONTINUOUS) is XNYS


# Trade-built bars -----------------------------------------------------


def test_trade_bars_follow_sessions_and_never_fill_overnight() -> None:
    """Flat 1s fills stop at the close; the next session starts fresh."""
    close = _ny(2026, 11, 27, 13)
    builder = trade_bars.TradeBarBuilder(AAPL, TF.S1, source="test")
    builder.start_segment(close - 3_000)
    builder.add([_trade(close - 3_000, "10", "a")], close)
    after_hours = _trade(close + 600_000, "11", "b")
    monday = _ny(2026, 11, 30, 9, 30)
    rows = builder.add([after_hours, _trade(monday + 2_000, "12", "c")], monday)
    assert builder.out_of_session == 1
    starts = [c.start_ms for c in rows]
    assert starts == [
        close - 3_000,
        close - 2_000,
        close - 1_000,
        monday,
        monday + 1_000,
        monday + 2_000,
    ]
    assert all(c.end_ms <= close or c.start_ms >= monday for c in rows)


def test_closing_auction_print_joins_the_last_twse_bar() -> None:
    """A print stamped within the XTAI grace belongs to the 13:29:59 bar."""
    close = _tpe(2026, 11, 27, 13, 30)
    trade = market.Trade(
        instrument_id=TSMC,
        trade_id="auction",
        venue_event_ts_ms=close + 30_000,
        received_at_ms=close + 30_000,
        price=_D("1000"),
        base_quantity=_D(1000),
        aggressor_side=market.AggressorSide.UNKNOWN,
        is_aggregated=False,
    )
    builder = trade_bars.TradeBarBuilder(
        TSMC,
        TF.S1,
        source="twse.trades",
        buy_volume_method=market.BuyVolumeMethod.CLASSIFIED,
    )
    builder.start_segment(close - 1_000)
    rows = builder.add([trade], close + 30_000)
    assert [c.start_ms for c in rows] == [close - 1_000]
    assert rows[0].taker_buy_base_volume is None
    assert builder.finalize_through(close, close) == []
    final = builder.finalize_through(close + 60_000, close + 60_000)
    assert [c.is_final for c in final] == [True]


def test_bars_from_coverage_label_classified_buy_volume() -> None:
    """Trade-built bars carry the venue's buy-volume method."""
    open_ = _ny(2026, 11, 30, 9, 30)
    coverage = _market_trades(open_)
    rows = trade_bars.bars_from_coverage(
        coverage,
        AAPL,
        TF.S1,
        source="test",
        now_ms=open_ + 5_000,
        buy_volume_method=market.BuyVolumeMethod.CLASSIFIED,
    )
    assert rows
    assert {c.buy_volume_method for c in rows} == {
        market.BuyVolumeMethod.CLASSIFIED
    }


def _market_trades(open_: int) -> trades_lib.TradeCoverage:
    """Builds a complete two-second trade coverage after the open.

    Args:
        open_: Session open in epoch milliseconds.

    Returns:
        Coverage of ``[open_, open_ + 2 s)`` holding two AAPL trades.
    """
    return trades_lib.TradeCoverage(
        from_ms=open_,
        to_ms=open_ + 2_000,
        trades=(_trade(open_, "10", "x"), _trade(open_ + 1_500, "11", "y")),
    )


# DEMO stock ------------------------------------------------------------


def _adapter(now: int) -> mock.MockAdapter:
    """Builds the DEMO adapter at a fixed time.

    Args:
        now: Clock time in epoch milliseconds.

    Returns:
        The DEMO adapter on a manual clock stopped at ``now``.
    """
    return mock.MockAdapter(clock_lib.ManualClock(now))


async def test_demo_stock_hourly_bars_start_at_the_open() -> None:
    """1h ACME bars run 09:30-15:30, the last one cut at 16:00."""
    adapter = _adapter(_ny(2026, 11, 26, 12))
    rows = await adapter.fetch_candles(
        ACME, TF.H1, _ny(2026, 11, 25), _ny(2026, 11, 26)
    )
    opens = [
        datetime.datetime.fromtimestamp(c.start_ms / 1000, NY) for c in rows
    ]
    assert [(t.hour, t.minute) for t in opens] == [
        (9, 30),
        (10, 30),
        (11, 30),
        (12, 30),
        (13, 30),
        (14, 30),
        (15, 30),
    ]
    assert rows[-1].end_ms == _ny(2026, 11, 25, 16)
    assert all(c.is_final for c in rows)
    assert {c.buy_volume_method for c in rows} == {
        market.BuyVolumeMethod.CLASSIFIED
    }


async def test_demo_stock_daily_and_weekly_bars_are_sessions() -> None:
    """Daily bars are sessions and the Thanksgiving week is one bar."""
    adapter = _adapter(_ny(2026, 12, 1, 12))
    days = await adapter.fetch_candles(
        ACME, TF.D1, _ny(2026, 11, 23), _ny(2026, 11, 30)
    )
    assert [c.start_ms for c in days] == [
        _ny(2026, 11, day, 9, 30) for day in (23, 24, 25, 27)
    ]
    assert days[-1].end_ms == _ny(2026, 11, 27, 13)
    weeks = await adapter.fetch_candles(
        ACME, TF.W1, _ny(2026, 11, 23), _ny(2026, 11, 28)
    )
    assert [(c.start_ms, c.end_ms) for c in weeks] == [
        (_ny(2026, 11, 23, 9, 30), _ny(2026, 11, 27, 13))
    ]
    hours = await adapter.fetch_candles(
        ACME, TF.H1, _ny(2026, 11, 27), _ny(2026, 11, 28)
    )
    assert days[-1].base_volume > sum(
        (c.base_volume for c in hours), _D(0)
    ) * _D("0.5")


async def test_demo_stock_has_no_bars_trades_or_funding_off_session() -> None:
    """Weekends have no candles or trades; funding and OI are refused."""
    adapter = _adapter(_ny(2026, 11, 29, 12))
    saturday = _ny(2026, 11, 28)
    assert (
        await adapter.fetch_candles(ACME, TF.M5, saturday, saturday + 1) == []
    )
    assert await adapter.fetch_trades(ACME, saturday, _ny(2026, 11, 29)) == []
    with pytest.raises(errors.UnsupportedError, match="funding"):
        await adapter.fetch_funding(ACME)
    with pytest.raises(errors.UnsupportedError, match="open interest"):
        await adapter.fetch_open_interest(ACME)
    snapshot = await adapter.fetch_snapshot(ACME)
    assert snapshot.funding is None and snapshot.open_interest is None
    assert snapshot.turnover_24h_quote is not None
    assert snapshot.turnover_24h_quote > 0
    with pytest.raises(errors.NotFoundError):
        await adapter.fetch_candles(
            instruments.InstrumentId.parse("mock:perp:ACME"),
            TF.H1,
            saturday,
            saturday + 1,
        )


async def test_demo_stock_seconds_bars_and_live_stream() -> None:
    """Seconds bars start at 09:30:00; the stream emits session bars."""
    now = _ny(2026, 11, 30, 9, 31)
    adapter = _adapter(now)
    rows = await adapter.fetch_candles(
        ACME, TF.S5, _ny(2026, 11, 30, 9, 29), now
    )
    assert rows[0].start_ms == _ny(2026, 11, 30, 9, 30)
    assert rows[0].buy_volume_method is market.BuyVolumeMethod.CLASSIFIED
    stream = adapter.stream_candles(ACME, TF.H1)
    live = await anext(stream)
    assert live.start_ms == _ny(2026, 11, 30, 9, 30)
    assert not live.is_final
    # The port promises an AsyncIterator; the DEMO adapter's is a generator.
    assert isinstance(stream, AsyncGenerator)
    await stream.aclose()


async def test_demo_lists_the_stock_with_listing_metadata() -> None:
    """mock:stock:ACME is an EQUITY on XNYS without funding terms."""
    adapter = _adapter(_ny(2026, 11, 30, 12))
    listed = {str(i.instrument_id): i for i in await adapter.list_instruments()}
    stock = listed["mock:stock:ACME"]
    assert stock.asset_class is instruments.AssetClass.EQUITY
    assert stock.calendar == "XNYS"
    assert stock.funding_interval_hours is None
    caps = adapter.capabilities()
    assert instruments.MarketType.STOCK in caps.market_types
    assert caps.market_data.classified_delta


# Service ---------------------------------------------------------------


def _service(
    now: int, adapter: mock.MockAdapter | None = None
) -> service_lib.MarketDataService:
    """Builds the market-data service over the DEMO venue.

    Args:
        now: Clock time in epoch milliseconds.
        adapter: Optional adapter replacing the DEMO one.

    Returns:
        The service, storing up to 60,000 bars per series.
    """
    clock = clock_lib.ManualClock(now)
    adapters = {instruments.Venue.MOCK: adapter or mock.MockAdapter(clock)}
    store = candle_store.CandleStore(max_bars_per_series=60_000)
    return service_lib.MarketDataService(
        adapters,
        registry.InstrumentRegistry(adapters),
        store,
        feed_router.FeedRouter(adapters, store, clock),
        clock,
    )


async def test_batch_over_a_weekend_is_complete() -> None:
    """A weekend batch ends at Friday's close with every bar final."""
    svc = _service(_ny(2026, 11, 29, 12))
    batch = await svc.get_batch(ACME, TF.H1, limit=20)
    assert batch.complete
    assert batch.candles[-1].end_ms == _ny(2026, 11, 27, 13)
    assert all(c.is_final for c in batch.candles)
    assert len(batch.candles) == 20


async def test_closed_market_needs_no_live_refresh() -> None:
    """Between sessions a repeated query makes no venue request."""
    adapter = _CountingMock(clock_lib.ManualClock(_ny(2026, 11, 29, 12)))
    svc = _service(_ny(2026, 11, 29, 12), adapter)
    await svc.get_candles(ACME, TF.H1, _ny(2026, 11, 25), _ny(2026, 11, 29, 12))
    calls = adapter.calls
    await svc.get_candles(ACME, TF.H1, _ny(2026, 11, 25), _ny(2026, 11, 29, 12))
    assert adapter.calls == calls


async def test_refresh_continues_past_an_adapter_page_cap() -> None:
    """A history call capped at 1,000 bars is repeated until complete."""
    now = _ny(2026, 11, 30, 12)
    adapter = _CountingMock(clock_lib.ManualClock(now), cap=1_000)
    svc = _service(now, adapter)
    start = _ny(2026, 10, 1, 9, 30)
    rows = await svc.get_candles(ACME, TF.M5, start, now)
    expected = XNYS.bars_between(TF.M5, start, XNYS.floor(TF.M5, now))
    assert expected > 3_000
    assert len(rows) == expected
    assert adapter.calls >= 4


async def test_times_before_the_calendar_are_validation_errors() -> None:
    """Asking XNYS bars before 1990 is a client error."""
    svc = _service(_ny(2026, 11, 30, 12))
    with pytest.raises(errors.ValidationFailedError, match="XNYS"):
        await svc.get_batch(ACME, TF.H1, to_ms=1_000, limit=5)


def test_calendar_sessions_list_and_limits() -> None:
    """Sessions are listed per calendar with a 400-day window cap."""
    week = service_lib.calendar_sessions(
        "XNYS", _ny(2026, 11, 23), _ny(2026, 11, 30)
    )
    assert [s["day"] for s in week["sessions"]] == [  # type: ignore[attr-defined]  # the contract dict types its values as object
        "2026-11-23",
        "2026-11-24",
        "2026-11-25",
        "2026-11-27",
    ]
    always = service_lib.calendar_sessions("24x7", 0, 86_400_000)
    assert always["sessions"] == []
    with pytest.raises(errors.NotFoundError, match="XTAI"):
        service_lib.calendar_sessions("LSE", 0, 1)
    with pytest.raises(errors.ValidationFailedError, match="400 days"):
        service_lib.calendar_sessions("XTAI", 0, 401 * 86_400_000)
    with pytest.raises(errors.ValidationFailedError):
        service_lib.calendar_sessions("XTAI", 10, 10)


class _CountingMock(mock.MockAdapter):
    """DEMO adapter that counts history calls and can cap their size.

    Attributes:
        calls: Number of ``fetch_candles`` calls so far.
    """

    def __init__(self, clock: clock_lib.Clock, cap: int | None = None) -> None:
        """Initializes the adapter.

        Args:
            clock: Time source.
            cap: Maximum bars per history call (oldest kept, like Binance).
        """
        super().__init__(clock)
        self.calls = 0
        self._cap = cap

    async def fetch_candles(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        start_ms: int,
        end_ms: int,
        limit: int | None = None,
    ) -> list[market.Candle]:
        """Counts the call and returns at most ``cap`` of the oldest bars.

        Args:
            instrument: Instrument.
            timeframe: Resolution.
            start_ms: Inclusive start in epoch milliseconds.
            end_ms: Exclusive end in epoch milliseconds.
            limit: Optional maximum bars, passed to the DEMO adapter.

        Returns:
            The DEMO bars of the window, cut to the oldest ``cap`` if set.
        """
        self.calls += 1
        rows: Sequence[market.Candle] = await super().fetch_candles(
            instrument, timeframe, start_ms, end_ms, limit
        )
        return list(rows if self._cap is None else rows[: self._cap])


def test_feed_state_of_demo_stock_is_demo() -> None:
    """DEMO stock candles keep the DEMO provenance."""
    candle = mock.MockAdapter(clock_lib.ManualClock(_ny(2026, 11, 30, 12))).bar(
        ACME, TF.H1, _ny(2026, 11, 30, 9, 30)
    )
    assert candle is not None
    assert candle.feed_state is quality.FeedState.DEMO


def test_trades_before_the_calendar_are_out_of_session() -> None:
    """A print before XNYS's first session has no bar."""
    builder = trade_bars.TradeBarBuilder(AAPL, TF.S1, source="test")
    builder.start_segment(None)
    assert builder.add([_trade(1_000, "1", "early")], 2_000) == []
    assert builder.out_of_session == 1
