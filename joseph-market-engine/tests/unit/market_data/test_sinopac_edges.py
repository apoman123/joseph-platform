"""Unit tests: edge paths of the Shioaji-backed TWSE adapter (fake SDK)."""

from __future__ import annotations

import asyncio
import datetime
import decimal
import json
import logging
import pathlib
import zoneinfo
from collections.abc import AsyncIterator
from collections.abc import Callable
from typing import Any

import pytest

from joseph.domain import clock as clock_lib
from joseph.domain import errors
from joseph.domain import instruments
from joseph.domain import sessions
from joseph.domain import timeframes
from joseph.market_data.adapters import sinopac
from joseph.market_data.adapters import twse
from joseph.market_data.testing import fake_shioaji

_D = decimal.Decimal
_TPE = zoneinfo.ZoneInfo("Asia/Taipei")
_FIXTURES = pathlib.Path(__file__).resolve().parents[3] / "fixtures" / "venues"
_DAY = datetime.date(2026, 11, 23)
_PREV = datetime.date(2026, 11, 20)
_ID = instruments.InstrumentId.parse("twse:stock:2330")
_M1 = timeframes.Timeframe.M1
_S1 = timeframes.Timeframe.S1


def _local(day: datetime.date, *hms: int) -> datetime.datetime:
    """Builds a naive Taipei time.

    Args:
        day: Date.
        *hms: Hour, minute, second and microsecond.

    Returns:
        Naive datetime.
    """
    stamp = datetime.datetime(day.year, day.month, day.day, *hms, tzinfo=_TPE)
    return stamp.replace(tzinfo=None)  # Shioaji streams naive Taipei time.


def _ms(day: datetime.date, *hms: int) -> int:
    """Returns the UTC epoch ms of a Taipei time.

    Args:
        day: Date.
        *hms: Hour, minute, second and microsecond.

    Returns:
        Milliseconds.
    """
    return int(_local(day, *hms).replace(tzinfo=_TPE).timestamp() * 1000)


class _Http:
    """``VenueHttp`` stand-in answering one MIS path.

    Attributes:
        answers: Response per path.
    """

    def __init__(self, answers: dict[str, Any] | None = None) -> None:
        """Initializes the stub.

        Args:
            answers: Response per path.
        """
        self.answers = answers or {}

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """Returns the scripted answer of a path.

        Args:
            path: Path.
            params: Query parameters.

        Returns:
            Scripted body.

        Raises:
            errors.VenueUnavailableError: For unscripted paths.
        """
        answer = self.answers.get(path)
        if answer is None:
            raise errors.VenueUnavailableError(f"unscripted {path}")
        return answer(params) if callable(answer) else answer

    async def close(self) -> None:
        """Closes nothing."""


async def _inline(fn: Callable[[], Any]) -> Any:
    """Runs an SDK call on the loop.

    Args:
        fn: Call.

    Returns:
        Its result.
    """
    return fn()


async def _blocked(seconds: float) -> None:
    """Parks the MIS recorder.

    Args:
        seconds: Ignored.
    """
    del seconds
    await asyncio.Event().wait()


def _mis_answer(clock: clock_lib.Clock) -> Callable[[Any], dict[str, Any]]:
    """Builds an MIS answer for 2330 at the clock's time.

    Args:
        clock: Clock.

    Returns:
        Answer function.
    """

    def answer(params: Any) -> dict[str, Any]:
        """Returns one record.

        Args:
            params: Query.

        Returns:
            Body.
        """
        del params
        now = clock.now_ms()
        return {
            "rtcode": "0000",
            "msgArray": [
                {
                    "c": "2330",
                    "tlong": str(now),
                    "d": twse.taipei_date(now).strftime("%Y%m%d"),
                    "z": "100.0000",
                    "y": "99.0000",
                    "v": "5",
                    "o": "99.5",
                    "h": "100.5",
                    "l": "99.0",
                    "a": "100.5000_",
                    "f": "1_",
                    "b": "100.0000_",
                    "g": "2_",
                }
            ],
        }

    return answer


def _build(
    clock: clock_lib.ManualClock,
    fake: fake_shioaji.FakeShioaji | None = None,
    archive: Any = None,
    **config: Any,
) -> tuple[twse.TwseAdapter, fake_shioaji.FakeShioaji]:
    """Builds a Shioaji-backed TWSE adapter with 2330 listed.

    Args:
        clock: Clock.
        fake: Fake SDK.
        archive: Optional bar archive.
        **config: ``SinopacConfig`` overrides.

    Returns:
        ``(adapter, fake)``.
    """
    api = fake or fake_shioaji.FakeShioaji()
    source = sinopac.SinopacSource(
        sinopac.SinopacConfig(
            api_key="key-123456", secret_key="secret-7890", **config
        ),
        clock,
        factory=lambda: fake_shioaji.FakeShioajiFactory(api),
        runner=_inline,
    )
    stock_day = json.loads(
        (_FIXTURES / "twse" / "stock_day_2330.json").read_text("utf-8")
    )
    adapter = twse.TwseAdapter(
        _Http(),  # type: ignore[arg-type]
        _Http({"/rwd/zh/afterTrading/STOCK_DAY": stock_day}),  # type: ignore[arg-type]
        _Http({"/stock/api/getStockInfo.jsp": _mis_answer(clock)}),  # type: ignore[arg-type]
        clock,
        sleep=_blocked,
        sinopac=source,
        bar_archive=archive,
    )
    load = [
        json.loads((_FIXTURES / "twse" / f"{name}.json").read_text("utf-8"))
        for name in (
            "stock_day_all",
            "t187ap03_L",
            "t187ap47_L",
            "twt88u",
            "twtawu",
        )
    ]
    rows, meta = twse.parse_instruments(*load, now_ms=clock.now_ms())
    adapter._instruments = {r.instrument_id.venue_market_id: r for r in rows}
    adapter._loaded_at_ms = clock.now_ms()
    adapter.listing = meta
    return adapter, api


def _tick(
    at: datetime.datetime, price: str, lots: int, total: int
) -> fake_shioaji.FakeTick:
    """Builds a stream tick of 2330.

    Args:
        at: Naive Taipei time.
        price: Price.
        lots: Lots.
        total: Day lots through the tick.

    Returns:
        Tick.
    """
    return fake_shioaji.FakeTick(
        "2330", at, price, lots, total, 1, open="99", high="101", low="98"
    )


def _book(at: datetime.datetime) -> fake_shioaji.FakeBidAsk:
    """Builds a 2330 book update.

    Args:
        at: Naive Taipei time.

    Returns:
        Update.
    """
    return fake_shioaji.FakeBidAsk("2330", at, ["100"], [3], ["100.5"], [2])


async def _settle() -> None:
    """Lets callbacks hop onto the loop and streams run."""
    for _ in range(20):
        await asyncio.sleep(0)


async def _read(
    stream: AsyncIterator[Any], push: Callable[[], None], count: int = 1
) -> list[Any]:
    """Pushes input once the stream waits, then reads updates.

    Args:
        stream: Stream.
        push: Feeds the fake SDK.
        count: Updates to read.

    Returns:
        Updates.
    """
    task = asyncio.ensure_future(anext(stream))
    await _settle()
    push()
    out = [await asyncio.wait_for(task, 5)]
    for _ in range(count - 1):
        out.append(await asyncio.wait_for(anext(stream), 5))
    return out


class _BrokenArchive:
    """Bar archive whose every call fails."""

    def put(self, *args: object) -> None:
        """Fails.

        Args:
            *args: Ignored.

        Raises:
            RuntimeError: Always.
        """
        del args
        raise RuntimeError("disk full")

    minutes = coverage = prune = put


async def test_live_feed_needs_trading_hours_and_can_wait_for_a_book() -> None:
    """Outside trading hours MIS serves; inside, a book can be awaited."""
    clock = clock_lib.ManualClock(_ms(_DAY, 7))
    adapter, fake = _build(clock)
    assert await adapter._live_feed(_ID) is None
    book = await adapter.fetch_order_book(_ID, 5)
    assert book.source == twse.MIS_SOURCE
    clock.advance(3 * 3_600_000)  # 10:00.
    assert await adapter._live_feed(_ID, wait_s=0.01) is None  # No data yet.
    task = asyncio.ensure_future(adapter._live_feed(_ID, wait_s=5))
    await _settle()
    fake.push_bidask(_book(_local(_DAY, 10)))
    feed = await asyncio.wait_for(task, 5)
    assert feed is not None and feed.book is not None
    assert adapter._feed_daily(_ID, feed) is None  # No tick yet.
    sunday = sinopac.Feed("2330", "TSE", _ID)
    sunday.apply_tick(
        _tick(_local(datetime.date(2026, 11, 22), 10), "1", 1, 1), 0
    )
    assert adapter._feed_daily(_ID, sunday) is None
    await adapter.close()


async def test_history_paths_survive_archive_and_source_failures(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Broken archives and an unavailable source degrade, never fail."""
    monkeypatch.setattr(twse, "_KBAR_CACHE_DAYS", 1)
    monkeypatch.setattr(twse, "_TICK_CACHE_DAYS", 1)
    clock = clock_lib.ManualClock(_ms(_DAY, 7))
    adapter, fake = _build(clock, archive=_BrokenArchive())
    earlier = datetime.date(2026, 11, 19)
    for day in (_PREV, earlier):
        fake.kbars_rows[("2330", day)] = [
            (_local(day, 9, 1), 1.0, 1.0, 1.0, 1.0, 1, 1000.0)
        ]
        fake.ticks_rows[("2330", day)] = [(_local(day, 9, 0, 1), 1.0, 1, 1)]
    first = sessions.XTAI.session(earlier)
    last = sessions.XTAI.session(_PREV)
    assert first is not None and last is not None
    got = await adapter.fetch_candle_coverage(
        _ID, _M1, first.open_ms, last.close_ms
    )
    assert len(got.candles) == 2 and len(adapter._kbar_days) == 1
    assert "bar archive read failed" in caplog.text
    assert "bar archive write failed" in caplog.text
    for day_session in (first, last):
        start = day_session.open_ms
        await adapter.fetch_candles(_ID, _S1, start, start + 2_000)
    assert len(adapter._tick_days) == 1
    unarchived, _ = _build(clock)
    missing = [first]
    assert unarchived._archived_kbars(_ID, missing, [], []) == missing
    assert await unarchived._fetch_kbars(_ID, [], [], []) == []
    source = unarchived._sinopac
    assert source is not None
    source._mark_down("outage", "test")
    assert await unarchived._fetch_kbars(_ID, [last], [], []) == [last]
    assert await unarchived._day_ticks(_ID, last) is None
    old = sessions.XTAI.session(datetime.date(2019, 12, 2))
    assert old is not None
    assert await unarchived._fetch_kbars(_ID, [old], [], []) == [old]
    future = await unarchived.fetch_candle_coverage(
        _ID, _M1, _ms(_DAY, 12), _ms(_DAY, 13)
    )
    assert future.candles == () and future.complete
    early = await unarchived.fetch_candle_coverage(_ID, _M1, 0, 1)
    assert early.candles == ()
    ahead = await unarchived.fetch_candle_coverage(
        _ID, _S1, _ms(_DAY, 12), _ms(_DAY, 13)
    )
    assert ahead.candles == () and ahead.complete
    with pytest.raises(errors.UnsupportedError):
        off, _ = _build(clock)
        off._sinopac = None
        await off._trade_history(_ID, 0, 1, 1)
    for item in (adapter, unarchived):
        await item.close()


async def test_today_degrades_when_queries_fail(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Failed kbars and tick-history queries leave the stream's own data."""
    caplog.set_level(logging.INFO)
    clock = clock_lib.ManualClock(_ms(_DAY, 10, 0, 30))
    adapter, fake = _build(clock)
    session = sessions.XTAI.session(_DAY)
    assert session is not None
    await adapter.fetch_candle_coverage(
        _ID, _M1, session.open_ms, clock.now_ms()
    )  # Subscribes.
    fake.push_tick(_tick(_local(_DAY, 10, 0, 40), "100", 1, 151))
    fake.push_tick(_tick(_local(_DAY, 10, 0, 50), "100", 1, 160))  # Gap.
    fake.push_tick(_tick(_local(_DAY, 10, 1, 10), "101", 2, 162))
    fake.push_bidask(_book(_local(_DAY, 10, 2, 10)))
    await _settle()
    clock.advance(120_000)
    fake.failures["kbars"] = fake_shioaji.ShioajiConnectionError()
    got = await adapter.fetch_candle_coverage(
        _ID, _M1, session.open_ms, clock.now_ms()
    )
    assert [c.source for c in got.candles] == [sinopac.TICK_SOURCE]
    assert "serving official data" in caplog.text
    del fake.failures["kbars"]
    source = adapter._sinopac
    assert source is not None
    source._down_until_ms = None
    fake.failures["ticks"] = fake_shioaji.ShioajiConnectionError()
    secs = await adapter.fetch_candle_coverage(
        _ID, _S1, _ms(_DAY, 10, 0, 55), _ms(_DAY, 10, 1, 12)
    )
    assert [c.start_ms for c in secs.candles] == [_ms(_DAY, 10, 1, 10)]
    trades = await adapter._trade_history(
        _ID, session.open_ms, clock.now_ms(), 1
    )
    assert trades.from_ms == _ms(_DAY, 10, 0, 50) + 1  # The gap stops it.
    source._down_until_ms = None
    del fake.failures["ticks"]
    clock.advance(86_400_000)  # Tomorrow: yesterday's kbars are not today's.
    tomorrow = sessions.XTAI.session(datetime.date(2026, 11, 24))
    assert tomorrow is not None
    bars, covered = await adapter._today_minutes(_ID, tomorrow, clock.now_ms())
    assert bars == []  # Shioaji's kbars of the new day hold no trade.
    assert covered == [
        (tomorrow.open_ms, _ms(datetime.date(2026, 11, 24), 10, 2))
    ]
    assert "kbars 2330 2026-11-24 2026-11-24" in fake.calls
    await adapter.close()


async def test_streams_idle_reseed_and_prune(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Idle streams keep waiting; old minutes are pruned; books are skipped."""
    monkeypatch.setattr(twse, "_STREAM_IDLE_S", 0.01)
    clock = clock_lib.ManualClock(_ms(_DAY, 10, 0, 30))
    adapter, fake = _build(clock)
    minutes = adapter.stream_candle_coverage(_ID, _M1)
    await asyncio.wait_for(anext(minutes), 5)  # Seed (empty).
    fake.push_tick(_tick(_local(_DAY, 10, 0, 40), "100", 1, 151))
    await _settle()
    updates = await _read(
        minutes,
        lambda: fake.push_tick(_tick(_local(_DAY, 10, 1, 5), "101", 1, 152)),
        count=2,
    )
    live = [c for update in updates for c in update.candles]
    assert [(c.start_ms, c.is_final) for c in live] == [
        (_ms(_DAY, 10, 1), False)
    ]
    await asyncio.sleep(0.05)  # Idle wake-ups yield nothing.
    seconds = adapter.stream_candle_coverage(_ID, _S1)
    replay = await asyncio.wait_for(anext(seconds), 5)
    assert [c.start_ms for c in replay.candles] == [_ms(_DAY, 10, 1, 5)]
    days = adapter.stream_candles(_ID, timeframes.Timeframe.D1)
    today = await asyncio.wait_for(anext(days), 5)
    assert today.base_volume == _D(152_000)
    task = asyncio.ensure_future(anext(days))
    await _settle()
    fake.push_bidask(_book(_local(_DAY, 10, 1, 20)))  # Not a quote: skipped.
    fake.push_tick(_tick(_local(_DAY, 10, 1, 21), "102", 1, 153))
    newer = await asyncio.wait_for(task, 5)
    assert newer.close == _D(102)
    # A gap while 10:01 is open drops that minute from the stream.
    gap = await _read(
        minutes,
        lambda: fake.push_tick(_tick(_local(_DAY, 10, 1, 30), "101", 1, 160)),
        count=3,
    )
    assert all(
        c.start_ms != _ms(_DAY, 10, 1, 30) for u in gap for c in u.candles
    )
    clock.advance(3 * 86_400_000)
    await _read(minutes, lambda: fake.push_bidask(_book(_local(_DAY, 10, 2))))
    for stream in (minutes, seconds, days):
        await stream.aclose()  # type: ignore[attr-defined]
    await adapter.close()


async def test_trade_history_spans_a_quiet_newer_session() -> None:
    """A session without trades still counts as covered up to its end."""
    clock = clock_lib.ManualClock(_ms(_DAY, 7))
    adapter, fake = _build(clock)
    earlier = datetime.date(2026, 11, 19)
    fake.ticks_rows[("2330", earlier)] = [
        (_local(earlier, 9, 0, 1), 100.0, 5, 1)
    ]
    first = sessions.XTAI.session(earlier)
    last = sessions.XTAI.session(_PREV)
    assert first is not None and last is not None
    got = await adapter._trade_history(
        _ID, first.open_ms, last.close_ms + 600_000, 1
    )
    assert [t.trade_id for t in got.trades] == ["2330-20261119-5"]
    assert got.from_ms == first.open_ms
    assert got.to_ms == last.close_ms + sessions.XTAI.close_grace_ms
    await adapter.close()
