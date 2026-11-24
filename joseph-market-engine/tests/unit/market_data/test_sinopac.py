# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Unit tests for the SinoPac Shioaji market-data source (fake SDK)."""

from __future__ import annotations

import asyncio
import datetime
import decimal
import logging
import zoneinfo
from collections.abc import Callable
from typing import Any

import pytest

from joseph.domain import clock as clock_lib
from joseph.domain import errors
from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import sessions
from joseph.market_data.adapters import sinopac
from joseph.market_data.testing import fake_shioaji

_D = decimal.Decimal
_TPE = zoneinfo.ZoneInfo("Asia/Taipei")
_DAY = datetime.date(2026, 11, 23)
_PREV = datetime.date(2026, 11, 20)
_ID = instruments.InstrumentId.parse("twse:stock:2330")


def _local(
    day: datetime.date,
    hour: int = 0,
    minute: int = 0,
    second: int = 0,
    microsecond: int = 0,
) -> datetime.datetime:
    """Builds a naive Taipei time.

    Args:
        day: Date.
        hour: Hour.
        minute: Minute.
        second: Second.
        microsecond: Microsecond.

    Returns:
        The naive Taipei wall time.
    """
    stamp = datetime.datetime(
        day.year,
        day.month,
        day.day,
        hour,
        minute,
        second,
        microsecond,
        tzinfo=_TPE,
    )
    return stamp.replace(tzinfo=None)  # Shioaji streams naive Taipei time.


def _ms(
    day: datetime.date,
    hour: int = 0,
    minute: int = 0,
    second: int = 0,
    microsecond: int = 0,
) -> int:
    """Returns the UTC epoch ms of a Taipei time.

    Args:
        day: Date.
        hour: Hour.
        minute: Minute.
        second: Second.
        microsecond: Microsecond.

    Returns:
        UTC epoch milliseconds.
    """
    local = _local(day, hour, minute, second, microsecond)
    return int(local.replace(tzinfo=_TPE).timestamp() * 1000)


async def _inline(fn: Callable[[], Any]) -> Any:
    """Runs an SDK call on the loop (tests need no thread).

    Args:
        fn: Zero-argument SDK call.

    Returns:
        The call's result.
    """
    return fn()


def _source(
    clock: clock_lib.Clock,
    api: fake_shioaji.FakeShioaji | None = None,
    **config: Any,
) -> tuple[sinopac.SinopacSource, fake_shioaji.FakeShioaji]:
    """Builds and acquires a source over the fake SDK.

    Args:
        clock: Clock.
        api: Fake client (a fresh one by default).
        **config: ``SinopacConfig`` overrides.

    Returns:
        ``(source, fake)``.
    """
    fake = api or fake_shioaji.FakeShioaji()
    settings = sinopac.SinopacConfig(
        api_key="key-abcdef", secret_key="secret-123456", **config
    )
    source = sinopac.SinopacSource(
        settings,
        clock,
        factory=lambda: fake_shioaji.FakeShioajiFactory(fake),
        runner=_inline,
    )
    source.acquire()
    return source, fake


def test_timestamps_sides_and_ids_follow_shioaji_conventions() -> None:
    """Wall-clock ns, naive stream times and tick_type map as documented."""
    nine = _local(_DAY, 9, 0, 8, 306075)
    assert (
        sinopac.wall_ms(fake_shioaji.wall_ns(nine)) == _ms(_DAY, 9, 0, 8) + 306
    )
    # The documented example: 1779094808306075000 is 2026-05-18 09:00:08.306.
    example = sinopac.wall_ms(1779094808306075000)
    assert example == _ms(datetime.date(2026, 5, 18), 9, 0, 8) + 306
    assert sinopac.local_ms(nine) == sinopac.local_ms(nine.replace(tzinfo=_TPE))
    assert sinopac.tick_side(1) is market.AggressorSide.BUY
    assert sinopac.tick_side("2") is market.AggressorSide.SELL
    assert sinopac.tick_side(0) is market.AggressorSide.UNKNOWN
    assert sinopac.tick_side("x") is market.AggressorSide.UNKNOWN
    assert sinopac.trade_id("2330", _DAY, 15) == "2330-20261123-15"
    assert (
        sinopac.error_kind(fake_shioaji.MarketDataQueryUsageLimitError())
        == "quota"
    )
    assert sinopac.error_kind(fake_shioaji.TokenError()) == "auth"
    assert sinopac.error_kind(RuntimeError()) == "outage"
    assert sinopac.quota_reset_ms(_ms(_DAY, 7, 0)) == _ms(_DAY, 8, 0)
    assert sinopac.quota_reset_ms(_ms(_DAY, 9, 0)) == _ms(
        datetime.date(2026, 11, 24), 8, 0
    )
    assert sinopac.in_trading_hours(_ms(_DAY, 8, 30))
    assert sinopac.in_trading_hours(_ms(_DAY, 13, 44))
    assert not sinopac.in_trading_hours(_ms(_DAY, 13, 45))
    assert not sinopac.in_trading_hours(_ms(datetime.date(2026, 11, 22), 10))


def test_kbars_are_labeled_by_their_minute_end() -> None:
    """09:01 holds 09:00; 13:30 holds the close; late bars are dropped."""
    rows = fake_shioaji.FakeKBars(
        ts=[
            fake_shioaji.wall_ns(_local(_DAY, 9, 1)),
            fake_shioaji.wall_ns(_local(_DAY, 13, 30)),
            fake_shioaji.wall_ns(_local(_DAY, 14, 31)),
            fake_shioaji.wall_ns(_local(_DAY, 9, 2, 30)),
            fake_shioaji.wall_ns(_local(_DAY, 9, 3)),
        ],
        Open=[100.0, 101.5, 1.0, 1.0, 1.0],
        High=[101.0, 102.0, 1.0, 1.0, 1.0],
        Low=[99.5, 101.0, 1.0, 1.0, 1.0],
        Close=[100.5, 101.5, 1.0, 1.0, "bad"],  # type: ignore[list-item]  # deliberately malformed close; parse_kbars must skip the row
        Volume=[10, 25, 1, 1, 1],
        Amount=[1_005_000.0, 2_537_500.0, 1.0, 1.0, 1.0],
    )
    bars = sinopac.parse_kbars(rows, _ID, fetched_ms=_ms(_DAY, 13, 31, 6))
    assert [b.start_ms for b in bars] == [_ms(_DAY, 9, 0), _ms(_DAY, 13, 29)]
    first = bars[0]
    assert (first.open, first.close, first.base_volume) == (
        _D("100.0"),
        _D("100.5"),
        _D(10_000),
    )
    assert first.quote_turnover == _D("1005000.0")
    assert first.is_final and first.source == sinopac.KBAR_SOURCE
    assert first.taker_buy_base_volume is None
    # The closing minute needs the closing grace before it is complete.
    early = sinopac.parse_kbars(rows, _ID, fetched_ms=_ms(_DAY, 13, 30, 30))
    assert [b.start_ms for b in early] == [_ms(_DAY, 9, 0)]
    mapping = {
        "ts": [fake_shioaji.wall_ns(_local(_DAY, 9, 1))],
        "Open": [1],
        "High": [1],
        "Low": [1],
        "Close": [1],
        "Volume": [1],
        "Amount": [1],
    }
    assert len(sinopac.parse_kbars(mapping, _ID, fetched_ms=_ms(_DAY, 10))) == 1


def test_ticks_keep_regular_session_board_lots() -> None:
    """Ids count the day volume; the 14:30 fixed-price match is excluded."""
    rows = fake_shioaji.FakeTicks(
        ts=[
            fake_shioaji.wall_ns(_local(_DAY, 9, 0, 0, 500_000)),
            fake_shioaji.wall_ns(_local(_DAY, 9, 0, 1)),
            fake_shioaji.wall_ns(_local(_DAY, 9, 0, 2)),
            fake_shioaji.wall_ns(_local(_DAY, 13, 30)),
            fake_shioaji.wall_ns(_local(_DAY, 14, 30)),
            fake_shioaji.wall_ns(_local(_DAY, 9, 0, 3)),
        ],
        close=[100.0, 100.5, 100.0, 101.0, 101.0, "x"],  # type: ignore[list-item]  # deliberately malformed price; parse_ticks must skip the tick
        volume=[50, 2, 0, 30, 7, 1],
        tick_type=[0, 1, 2, 0, 2, 1],
    )
    day = sinopac.parse_ticks(rows, _ID, _DAY, fetched_ms=1)
    assert [t.trade_id for t in day.trades] == [
        "2330-20261123-50",
        "2330-20261123-52",
        "2330-20261123-82",
    ]
    assert day.cumulative == (50, 52, 82)
    assert [t.aggressor_side for t in day.trades] == [
        market.AggressorSide.UNKNOWN,
        market.AggressorSide.BUY,
        market.AggressorSide.UNKNOWN,
    ]
    assert day.trades[1].base_quantity == _D(2000)
    assert day.trades[1].quote_quantity == _D("201000.0")


def test_minute_bars_classify_buy_volume_and_settle_with_grace() -> None:
    """Bars carry classified buy volume unless a tick is undetermined."""
    trades = [
        sinopac._trade(
            _ID,
            trade_id=f"t{i}",
            ts_ms=ts,
            received_ms=ts,
            price=_D(price),
            lots=lots,
            side=side,
        )
        for i, (ts, price, lots, side) in enumerate(
            [
                (_ms(_DAY, 9, 0, 5), "100", 2, market.AggressorSide.BUY),
                (_ms(_DAY, 9, 0, 9), "99", 1, market.AggressorSide.SELL),
                (_ms(_DAY, 9, 1, 2), "101", 1, market.AggressorSide.UNKNOWN),
                (_ms(_DAY, 13, 30), "102", 3, market.AggressorSide.UNKNOWN),
                (_ms(_DAY, 14, 30), "102", 3, market.AggressorSide.BUY),
            ]
        )
    ]
    bars = sinopac.minute_bars(
        trades,
        _ID,
        from_ms=_ms(_DAY, 9, 0),
        proven_ms=_ms(_DAY, 13, 30, 30),
        now_ms=1,
    )
    assert [b.start_ms for b in bars] == [
        _ms(_DAY, 9, 0),
        _ms(_DAY, 9, 1),
        _ms(_DAY, 13, 29),
    ]
    first, second, last = bars
    assert first.taker_buy_base_volume == _D(2000)
    assert first.taker_buy_method is market.BuyVolumeMethod.CLASSIFIED
    assert (first.open, first.low, first.close) == (_D(100), _D(99), _D(99))
    assert second.taker_buy_base_volume is None
    assert first.is_final and not last.is_final  # Close + grace not proven.
    partial = sinopac.minute_bars(
        trades,
        _ID,
        from_ms=_ms(_DAY, 9, 0, 7),
        proven_ms=_ms(_DAY, 9, 1, 30),
        now_ms=1,
        final_only=True,
    )
    assert partial == []


def test_whole_minutes_keep_only_settled_minutes() -> None:
    """Spans shrink to whole minutes; the last minute needs its grace."""
    span = (_ms(_DAY, 9, 0, 30), _ms(_DAY, 9, 5, 10))
    assert sinopac.whole_minutes(span) == (_ms(_DAY, 9, 1), _ms(_DAY, 9, 5))
    closing = (_ms(_DAY, 13, 0), _ms(_DAY, 13, 30, 20))
    assert sinopac.whole_minutes(closing) == (
        _ms(_DAY, 13, 0),
        _ms(_DAY, 13, 29),
    )
    assert sinopac.whole_minutes(closing, grace=False) == (
        _ms(_DAY, 13, 0),
        _ms(_DAY, 13, 30),
    )
    assert (
        sinopac.whole_minutes((_ms(_DAY, 9, 0, 10), _ms(_DAY, 9, 0, 50)))
        is None
    )
    assert sinopac.whole_minutes((0, 1)) is None


def _tick(
    at: datetime.datetime,
    price: str,
    lots: int,
    total: int,
    kind: int = 1,
    **extra: Any,
) -> fake_shioaji.FakeTick:
    """Builds a stream tick of 2330.

    Args:
        at: Naive Taipei time.
        price: Price as a decimal string.
        lots: Lots the tick traded.
        total: Day lots through the tick.
        kind: Shioaji ``tick_type`` (1 buy, 2 sell, 0 undetermined).
        **extra: Other ``FakeTick`` fields, such as ``simtrade``.

    Returns:
        A tick with a fixed day open, high, low and price change.
    """
    return fake_shioaji.FakeTick(
        "2330",
        at,
        price,
        lots,
        total,
        kind,
        open="99",
        high="101",
        low="98.5",
        price_chg="1.5",
        **extra,
    )


def _book(at: datetime.datetime, **extra: Any) -> fake_shioaji.FakeBidAsk:
    """Builds a five-level book update of 2330.

    Args:
        at: Naive Taipei time.
        **extra: Other ``FakeBidAsk`` fields, such as ``simtrade``.

    Returns:
        An update whose zero and empty bid levels carry no quote.
    """
    return fake_shioaji.FakeBidAsk(
        "2330",
        at,
        ["100", "99.5", "0", "", "99"],
        [3, 4, 5, 6, 0],
        ["100.5", "101"],
        [2, 1],
        **extra,
    )


def test_feed_proves_continuity_with_the_day_volume() -> None:
    """Continuous ticks extend a segment; a jump starts a new one."""
    feed = sinopac.Feed("2330", "TSE", _ID)
    assert (
        feed.apply_tick(
            _tick(_local(_DAY, 8, 59, 50), "99", 1, 1, simtrade=True), 0
        )
        == []
    )
    kinds = [
        e.kind
        for e in feed.apply_tick(
            _tick(_local(_DAY, 9, 0, 1), "99", 40, 40, 0), 0
        )
    ]
    assert kinds == ["quote", "segment", "trade", "proof"]
    assert feed.segments[0].from_ms == _ms(_DAY, 9, 0)  # Nothing before.
    feed.apply_tick(_tick(_local(_DAY, 9, 0, 30), "100", 2, 42), 0)
    assert len(feed.segments) == 1
    repeat = feed.apply_tick(_tick(_local(_DAY, 9, 0, 31), "100", 2, 42), 0)
    assert repeat[-1].kind == "quote"  # Same day volume: no new trade.
    events = feed.apply_tick(_tick(_local(_DAY, 9, 2, 0), "101", 1, 50, 2), 0)
    assert "segment" in [e.kind for e in events]
    assert feed.segments[1].from_ms == _ms(_DAY, 9, 2) + 1
    assert feed.segments[1].base_lots == 49
    assert feed.coverage() == [
        (_ms(_DAY, 9, 0), _ms(_DAY, 9, 0, 30)),
    ]
    assert feed.quote is not None and feed.quote.total_volume == _D(50_000)
    assert feed.quote.change == _D("1.5")
    after = feed.apply_tick(_tick(_local(_DAY, 15, 0), "101", 1, 99), 0)
    assert [e.kind for e in after] == ["quote"]
    assert feed.apply_tick(object(), 0) == []
    # The first segment never proved 09:01; 09:02 starts mid-minute.
    assert feed.minute_bars(0) == []
    assert feed.minute_bar(_ms(_DAY, 9, 2), 0) is None
    feed.apply_tick(_tick(_local(_DAY, 9, 3, 5), "102", 1, 51), 0)
    bars = feed.minute_bars(0)
    assert [(b.start_ms, b.is_final) for b in bars] == [
        (_ms(_DAY, 9, 3), False)
    ]
    assert feed.minute_bar(_ms(_DAY, 9, 3), 0) == bars[0]
    assert sinopac.Feed("1", "TSE", _ID).minute_bar(0, 0) is None


def test_feed_books_prove_delivery_unless_disconnected() -> None:
    """Bid/ask updates advance the proof by their time minus a grace."""
    feed = sinopac.Feed("2330", "TSE", _ID)
    assert feed.apply_book(_book(_local(_DAY, 9, 0), simtrade=True), 0) == []
    assert [
        e.kind for e in feed.apply_book(_book(_local(_DAY, 9, 0, 1)), 5)
    ] == ["book"]
    assert feed.book is not None
    assert [(lv.price, lv.quantity) for lv in feed.book.bids] == [
        (_D(100), _D(3000)),
        (_D("99.5"), _D(4000)),
    ]
    feed.apply_tick(_tick(_local(_DAY, 9, 0, 2), "100", 3, 3), 0)
    events = feed.apply_book(_book(_local(_DAY, 9, 1, 5)), 6)
    assert events[-1].kind == "proof"
    assert events[-1].proven_ms == _ms(_DAY, 9, 1, 3)
    feed.suspect = True
    assert [e.kind for e in feed.apply_book(_book(_local(_DAY, 9, 2)), 7)] == [
        "book"
    ]
    assert feed.apply_book(object(), 0) == []
    feed.apply_tick(_tick(_local(_DAY, 9, 2, 1), "100", 1, 4), 0)
    assert not feed.suspect and len(feed.segments) == 1
    feed.apply_book(_book(_local(datetime.date(2026, 11, 24), 9, 0)), 8)
    assert feed.day == datetime.date(2026, 11, 24) and not feed.trades


def test_feed_seed_joins_history_only_when_it_proves_continuity() -> None:
    """Today's tick history extends the first segment back to the open."""
    history = sinopac.parse_ticks(
        fake_shioaji.FakeTicks(
            ts=[
                fake_shioaji.wall_ns(_local(_DAY, 9, 0, 1)),
                fake_shioaji.wall_ns(_local(_DAY, 9, 3)),
            ],
            close=[99.0, 99.5],
            volume=[40, 10],
            tick_type=[0, 1],
        ),
        _ID,
        _DAY,
        fetched_ms=0,
    )
    feed = sinopac.Feed("2330", "TSE", _ID)
    assert not feed.seed(history)
    feed.apply_tick(_tick(_local(_DAY, 9, 5), "100", 5, 60), 0)
    assert not feed.seed(history)  # 50 lots known, the stream starts at 55.
    feed = sinopac.Feed("2330", "TSE", _ID)
    feed.apply_tick(_tick(_local(_DAY, 9, 5), "100", 5, 55), 0)
    feed.apply_tick(_tick(_local(_DAY, 9, 6), "100", 5, 70), 0)
    assert feed.seed(history)
    assert feed.segments[0].from_ms == _ms(_DAY, 9, 0)
    assert feed.segments[1].first == 3
    seeded = [t.trade_id for t in feed.segment_trades(0)]
    assert seeded[-1] == "2330-20261123-55"
    assert feed.seed(history)  # Already from the open.


async def test_source_logs_in_once_and_redacts_credentials(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """One login serves every query; keys never reach repr or logs."""
    clock = clock_lib.ManualClock(_ms(_DAY, 7, 0))
    source, fake = _source(clock)
    assert "key-abcdef" not in repr(source.config)
    assert source.status_note().endswith("(logs in on first use).")
    caplog.set_level(logging.DEBUG)
    rows = await source.kbars("2330", "TSE", _PREV, _PREV)
    await source.ticks("2330", "TSE", _PREV)
    assert fake.calls.count("login") == 1
    assert rows.ts == [] and "usage" in fake.calls  # Empty: allowance check.
    assert source.unavailable() is None
    assert "logged in" in source.status_note()
    with pytest.raises(ValueError, match="30 days"):
        await source.kbars("2330", "TSE", _PREV - datetime.timedelta(40), _PREV)
    with pytest.raises(errors.MissingDataError):
        await source.ticks("9999", "TSE", _PREV)
    assert (
        "key-abcdef" not in caplog.text and "secret-123456" not in caplog.text
    )
    await source.close()
    assert "logout" in fake.calls


def _missing_sdk() -> sinopac.SjDataFactory:
    """Fails like an image built without the ``sinopac`` extra.

    Returns:
        Never returns.

    Raises:
        errors.VenueUnavailableError: Always.
    """
    raise errors.VenueUnavailableError("shioaji is not installed")


async def test_source_without_credentials_or_sdk_is_disabled() -> None:
    """Missing keys or a missing SDK disable Shioaji for good."""
    clock = clock_lib.ManualClock(_ms(_DAY, 7, 0))
    source = sinopac.SinopacSource(sinopac.SinopacConfig(), clock)
    assert not source.enabled and not source.check_sdk()
    assert "not set" in source.status_note()
    with pytest.raises(errors.VenueUnavailableError):
        await source.ticks("2330", "TSE", _PREV)
    keyed = sinopac.SinopacSource(
        sinopac.SinopacConfig(api_key="a" * 8, secret_key="b" * 8),
        clock,
        factory=_missing_sdk,
    )
    assert not keyed.check_sdk()
    assert "not installed" in (keyed.unavailable() or "")
    late = sinopac.SinopacSource(
        sinopac.SinopacConfig(api_key="a" * 8, secret_key="b" * 8),
        clock,
        factory=_missing_sdk,
    )
    with pytest.raises(errors.VenueUnavailableError):
        await late.kbars("2330", "TSE", _PREV, _PREV)
    assert not late.enabled
    # The real factory needs the extra.
    assert isinstance(sinopac.ShioajiDataFactory, type)
    with pytest.raises(errors.VenueUnavailableError, match="sinopac"):
        sinopac.ShioajiDataFactory()


async def test_login_failure_backs_off_then_retries() -> None:
    """A refused login is retried only after the back-off."""
    clock = clock_lib.ManualClock(_ms(_DAY, 7, 0))
    source, fake = _source(clock)
    fake.failures["login"] = fake_shioaji.TokenError("bad key")
    with pytest.raises(errors.VenueUnavailableError, match="login failed"):
        await source.kbars("2330", "TSE", _PREV, _PREV)
    with pytest.raises(errors.VenueUnavailableError):
        await source.kbars("2330", "TSE", _PREV, _PREV)
    assert fake.calls.count("login") == 1
    assert "until 2026-11-23 07:10 Taipei" in source.status_note()
    del fake.failures["login"]
    clock.advance(10 * 60_000)
    await source.kbars("2330", "TSE", _PREV, _PREV)
    assert fake.calls.count("login") == 2


async def test_quota_errors_pause_shioaji_until_the_reset() -> None:
    """Usage-limit errors and empty answers with no bytes left both count."""
    clock = clock_lib.ManualClock(_ms(_DAY, 7, 0))
    source, fake = _source(clock)
    fake.failures["kbars"] = fake_shioaji.MarketDataQueryUsageLimitError()
    with pytest.raises(errors.VenueUnavailableError, match="allowance"):
        await source.kbars("2330", "TSE", _PREV, _PREV)
    assert "until 2026-11-23 08:00 Taipei" in source.status_note()
    assert "streams available, history paused" in source.status_note()
    assert source.unavailable() is None  # Streams keep running.
    assert "allowance" in (source.history_unavailable() or "")
    calls = len(fake.calls)
    with pytest.raises(errors.VenueUnavailableError, match="history"):
        await source.ticks("2330", "TSE", _PREV)
    assert len(fake.calls) == calls  # Refused without a query.
    feed = await source.watch("2330", "TSE", _ID)
    assert feed.code == "2330"
    clock.advance(3_600_000)
    del fake.failures["kbars"]
    fake.remaining_bytes = 0
    with pytest.raises(errors.VenueUnavailableError, match="came back empty"):
        await source.ticks("2330", "TSE", _PREV)
    assert "2026-11-24 08:00" in source.status_note()
    clock.advance(24 * 3_600_000)
    fake.failures["usage"] = fake_shioaji.ShioajiConnectionError()
    with pytest.raises(errors.VenueUnavailableError, match="outage"):
        await source.ticks("2330", "TSE", _PREV)


async def test_trading_hours_budgets_cap_history_queries() -> None:
    """In trading hours ticks and kbars queries stop at their budgets."""
    clock = clock_lib.ManualClock(_ms(_DAY, 9, 30))
    source, fake = _source(
        clock, intraday_ticks_calls=1, intraday_kbars_calls=0
    )
    assert source.budget_left("ticks") == 1
    await source.ticks("2330", "TSE", _PREV)
    assert source.budget_left("ticks") == 0
    with pytest.raises(errors.RateLimitedError, match="ticks budget"):
        await source.ticks("2330", "TSE", _PREV)
    with pytest.raises(errors.RateLimitedError, match="kbars budget"):
        await source.kbars("2330", "TSE", _PREV, _PREV)
    assert source.unavailable() is None  # A budget is not an outage.
    clock.advance(6 * 3_600_000)
    assert source.budget_left("ticks") == 1
    await source.ticks("2330", "TSE", _PREV)
    clock.advance(18 * 3_600_000)  # The next day's trading hours.
    assert source.budget_left("ticks") == 1
    assert fake.calls.count("ticks 2330 2026-11-20") == 2


async def test_streams_subscribe_dispatch_and_expire() -> None:
    """Ticks and books reach listeners; idle codes are unsubscribed."""
    clock = clock_lib.ManualClock(_ms(_DAY, 9, 30))
    source, fake = _source(clock, max_symbols=1)
    feed = await source.watch("2330", "TSE", _ID)
    assert {("2330", "tick"), ("2330", "bid_ask")} <= fake.subscriptions
    assert await source.watch("2330", "TSE", _ID) is feed
    queue = source.listen(feed)
    fake.push_tick(_tick(_local(_DAY, 9, 30, 1), "100", 2, 10))
    fake.push_bidask(_book(_local(_DAY, 9, 30, 5)))
    fake.push_tick(
        fake_shioaji.FakeTick("0050", _local(_DAY, 9, 30), "1", 1, 1)
    )
    await asyncio.sleep(0)
    kinds = []
    while not queue.empty():
        kinds.append(queue.get_nowait().kind)
    assert kinds == ["quote", "segment", "trade", "proof", "book", "proof"]
    snapshot = source.live_snapshot(feed)
    assert snapshot is not None
    assert (snapshot.last, snapshot.best_bid, snapshot.best_ask) == (
        _D(100),
        _D(100),
        _D("100.5"),
    )
    assert snapshot.change_24h_pct == _D("1.5") / _D("98.5") * 100
    with pytest.raises(errors.VenueUnavailableError, match="MAX_SYMBOLS"):
        await source.watch("6488", "OTC", _ID)
    source.unlisten(feed, queue)
    source.unlisten(feed, queue)
    clock.advance(31 * 60_000)
    other = await source.watch("6488", "OTC", _ID)
    assert source.feed("2330") is None and source.feed("6488") is other
    assert ("2330", "tick") not in fake.subscriptions
    assert source.live_snapshot(sinopac.Feed("1", "TSE", _ID)) is None


async def test_session_events_mark_the_source_down_and_up() -> None:
    """Session down stops streams; a reconnect makes Shioaji usable again."""
    clock = clock_lib.ManualClock(_ms(_DAY, 9, 30))
    source, fake = _source(clock)
    feed = await source.watch("2330", "TSE", _ID)
    queue = source.listen(feed)
    fake.push_event(12, "reconnecting")
    await asyncio.sleep(0)
    assert feed.suspect and source.unavailable() is None
    fake.push_event(1, "down")
    await asyncio.sleep(0)
    assert queue.get_nowait().kind == "down"
    assert "session event 1" in (source.unavailable() or "")
    with pytest.raises(errors.VenueUnavailableError):
        await source.watch("2330", "TSE", _ID)
    fake.push_event(13, "reconnected")
    await asyncio.sleep(0)
    assert source.unavailable() is None
    fake.failures["subscribe"] = fake_shioaji.ShioajiConnectionError()
    with pytest.raises(errors.VenueUnavailableError, match="subscribe"):
        await source.watch("6488", "OTC", _ID)
    source._loop = None
    fake.push_tick(_tick(_local(_DAY, 9, 31), "100", 1, 1))  # Dropped.
    await source.close()
    assert queue.get_nowait().kind == "down"


def test_xtai_session_constants_match_the_docs() -> None:
    """The fake's day is an XTAI session from 09:00 to 13:30 Taipei."""
    session = sessions.XTAI.session(_DAY)
    assert session is not None
    assert (session.open_ms, session.close_ms) == (
        _ms(_DAY, 9, 0),
        _ms(_DAY, 13, 30),
    )


class _SdkModule:
    """Stand-in for the imported ``shioaji`` module."""

    class QuoteVersion:
        """``sj.QuoteVersion``.

        Attributes:
            v1: The quote protocol version the source subscribes with.
        """

        v1 = "v1"

    @staticmethod  # noqa: GPY035 - the class stands in for the shioaji module and Shioaji is called on it, not on an instance
    def Shioaji(simulation: bool) -> str:  # noqa: N802 - the SDK's name.
        """Builds a client.

        Args:
            simulation: Simulation mode.

        Returns:
            A marker of the mode.
        """
        return f"client simulation={simulation}"


async def test_real_factory_wraps_the_sdk_module(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real factory builds production clients through ``shioaji``."""
    monkeypatch.setattr(
        "joseph.market_data.adapters.sinopac.importlib.import_module",
        lambda name: _SdkModule,
    )
    made = sinopac.ShioajiDataFactory()
    client: Any = made.client()
    assert client == "client simulation=False"
    assert made.quote_version() == "v1"
    assert await sinopac._thread_runner(lambda: 41 + 1) == 42


async def test_odd_inputs_are_tolerated() -> None:
    """Weekend times, malformed numbers and empty contracts are handled."""
    sunday = _ms(datetime.date(2026, 11, 22), 10)
    assert sinopac._session_of(sunday) is None
    assert sinopac._optional_decimal(None) is None
    assert sinopac._decimal_or("n/a", _D(7)) == _D(7)
    clock = clock_lib.ManualClock(_ms(_DAY, 7, 0))
    source, fake = _source(clock)
    group: Any = fake.contracts.stocks.TSE
    group.items["0000"] = None
    with pytest.raises(errors.MissingDataError):
        await source.kbars("0000", "TSE", _PREV, _PREV)
    fake.kbars_rows[("6488", _PREV)] = [(_local(_PREV, 9, 1), 1, 1, 1, 1, 1, 1)]
    stock = fake_shioaji.FakeStock("2330")
    fake.remaining_bytes = 0
    assert fake.kbars(stock, "2026-11-20", "2026-11-20").ts == []
    fake.remaining_bytes = 1
    assert fake.kbars(stock, "2026-11-20", "2026-11-20").ts == []
