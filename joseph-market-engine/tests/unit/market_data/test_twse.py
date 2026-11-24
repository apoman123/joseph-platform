# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Unit tests for the TWSE adapter helpers, MIS client and recorder."""

from __future__ import annotations

import asyncio
import datetime
import decimal
import json
import pathlib
from collections.abc import Iterator
from typing import Any

import pytest

from joseph.domain import clock as clock_lib
from joseph.domain import errors
from joseph.domain import instruments
from joseph.domain import sessions
from joseph.domain import timeframes
from joseph.market_data.adapters import bar_archive_sql
from joseph.market_data.adapters import tpex
from joseph.market_data.adapters import twse
from joseph.market_data.domain import snapshot_bars
from joseph.platform import db

_D = decimal.Decimal
_TPE = datetime.timezone(datetime.timedelta(hours=8))
_FIXTURES = pathlib.Path(__file__).resolve().parents[3] / "fixtures" / "venues"
_DAY = datetime.date(2026, 11, 23)
_SESSION = sessions.XTAI.session(_DAY)
assert _SESSION is not None
_OPEN = _SESSION.open_ms
_ID = instruments.InstrumentId.parse("twse:stock:2330")
_M1 = timeframes.Timeframe.M1


def _load(name: str) -> Any:
    """Reads a TWSE fixture.

    Args:
        name: File name without extension.

    Returns:
        Parsed JSON.
    """
    return json.loads((_FIXTURES / "twse" / f"{name}.json").read_text("utf-8"))


class _Http:
    """Scripted ``VenueHttp`` stand-in.

    Attributes:
        calls: Requested ``(path, params)``.
    """

    def __init__(self, answers: dict[str, Any]) -> None:
        """Initializes the stub.

        Args:
            answers: Response (or exception) per path.
        """
        self.answers = answers
        self.calls: list[tuple[str, dict[str, Any] | None]] = []

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """Returns the scripted answer of a path.

        Args:
            path: Path.
            params: Query parameters.

        Returns:
            Scripted body.

        Raises:
            errors.JosephError: When scripted.
        """
        self.calls.append((path, params))
        answer = self.answers[path]
        if isinstance(answer, errors.JosephError):
            raise answer
        return answer(params) if callable(answer) else answer

    async def close(self) -> None:
        """Closes nothing."""


def _adapter(
    clock: clock_lib.Clock,
    openapi: dict[str, Any] | None = None,
    www: dict[str, Any] | None = None,
    mis: dict[str, Any] | None = None,
    **kwargs: Any,
) -> twse.TwseAdapter:
    """Builds an adapter over scripted clients.

    Args:
        clock: Clock.
        openapi: OpenAPI answers.
        www: www answers.
        mis: MIS answers.
        **kwargs: Adapter options.

    Returns:
        Adapter.
    """
    return twse.TwseAdapter(
        _Http(openapi or {}),  # type: ignore[arg-type]
        _Http(www or {}),  # type: ignore[arg-type]
        _Http(mis or {}),  # type: ignore[arg-type]
        clock,
        **kwargs,
    )


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


def test_dates_and_numbers_parse_twse_formats() -> None:
    """ROC, Gregorian and grouped numbers parse; junk is rejected."""
    assert twse.roc_date("1151120") == datetime.date(2026, 11, 20)
    assert twse.roc_date("115/11/02") == datetime.date(2026, 11, 2)
    assert twse.listing_date("19940905") == datetime.date(1994, 9, 5)
    assert twse.listing_date("0920630") == datetime.date(2003, 6, 30)
    with pytest.raises(ValueError, match="ROC"):
        twse.roc_date("11/2")
    assert twse.number("41,165,068") == _D(41165068)
    assert twse.number("--") is None
    assert twse.number("abc") is None
    assert twse.mis_key("2330") == "tse_2330.tw"


def test_schedule_closures_keep_trading_days_open() -> None:
    """Trading-day entries, weekends and past dates are not closures."""
    entries = twse.parse_holidays_rwd(_load("holiday_rwd_2027"))
    entries += twse.parse_holidays_openapi(_load("holiday_openapi"))
    closures = twse.schedule_closures(entries, datetime.date(2026, 12, 1))
    assert datetime.date(2027, 2, 2) in closures  # Settlement-only day.
    assert datetime.date(2027, 1, 4) not in closures  # 開始交易.
    assert datetime.date(2027, 2, 1) not in closures  # 最後交易.
    assert all(day.weekday() < 5 for day in closures)
    assert twse.schedule_closures(entries, datetime.date(2030, 1, 1)) == set()
    assert twse.parse_holidays_rwd({"stat": "no"}) == []
    assert twse.parse_holidays_rwd({"data": [["bad", "x"], [None]]}) == []
    assert twse.parse_holidays_openapi([{"Date": "x", "Name": "y"}]) == []


async def test_refresh_calendar_adds_new_closures_once_a_day() -> None:
    """A newly announced weekday closure is added to XTAI, once per day."""
    typhoon = datetime.date(2030, 3, 5)
    assert sessions.XTAI.session(typhoon) is not None
    clock = clock_lib.ManualClock(_OPEN)
    rwd = {"stat": "ok", "data": [[typhoon.isoformat(), "颱風停止上班", ""]]}
    adapter = _adapter(
        clock,
        openapi={"/v1/holidaySchedule/holidaySchedule": []},
        www={"/rwd/zh/holidaySchedule/holidaySchedule": rwd},
    )
    assert await adapter.refresh_calendar() == {typhoon}
    assert sessions.XTAI.session(typhoon) is None
    assert await adapter.refresh_calendar() == set()


async def test_refresh_calendar_tolerates_unreachable_schedules() -> None:
    """Schedule failures are logged and retried later."""
    adapter = _adapter(
        clock_lib.ManualClock(_OPEN),
        openapi={
            "/v1/holidaySchedule/holidaySchedule": errors.VenueUnavailableError(
                "down"
            )
        },
    )
    assert await adapter.refresh_calendar() == set()


def test_listing_rules_mark_halts_and_first_five_days() -> None:
    """Suspensions halt a code; first-five-day listings have no limit."""
    now = int(
        datetime.datetime(2026, 11, 24, 10, tzinfo=_TPE).timestamp() * 1000
    )
    day_all = [
        {"Code": "2330", "Name": "台積電"},
        {"Code": "9999", "Name": "x"},
    ]
    companies = [{"公司代號": "2330", "上市日期": ""}]
    halts = [
        {
            "Code": "2330",
            "TradingHaltDate": "1151124",
            "TradingResumptionDate": "",
        },
        {"Code": "1", "TradingHaltDate": "bad"},
    ]
    first_five = [
        {"SecurCode": "2330", "5thTradingDate": "1151128"},
        {"SecurCode": "2", "5thTradingDate": "bad"},
    ]
    rows, meta = twse.parse_instruments(
        day_all, companies, [], first_five, halts, now_ms=now
    )
    assert [str(r.instrument_id) for r in rows] == ["twse:stock:2330"]
    assert rows[0].status is instruments.InstrumentStatus.HALTED
    assert rows[0].price_limit_pct is None
    assert meta["2330"].listed is None and not meta["2330"].etf


def test_etf_classes_follow_code_type_and_index() -> None:
    """Bond suffixes, bond fund types and bond indexes mean BOND."""
    bond = instruments.AssetClass.BOND
    assert twse.etf_asset_class("00865B", {}) is bond
    assert twse.etf_asset_class("00982D", {"基金類型": "主動式(債券)"}) is bond
    assert (
        twse.etf_asset_class("00680L", {"標的指數/追蹤指數名稱": "美國公債2倍"})
        is bond
    )
    assert (
        twse.etf_asset_class("00981T", {"標的指數/追蹤指數名稱": "債股平衡"})
        is instruments.AssetClass.EQUITY
    )


def test_mis_records_parse_or_are_rejected() -> None:
    """Empty, malformed and market-order levels are handled."""
    assert twse.parse_mis_record({"c": "", "tv": "-"}, 0) is None
    assert (
        twse.parse_mis_record({"c": "1", "tlong": "x", "d": "20261123"}, 0)
        is None
    )
    assert (
        twse.parse_mis_record({"c": "1", "tlong": "1", "d": "20261399"}, 0)
        is None
    )
    quote = twse.parse_mis_record(
        {
            "c": "2454",
            "tlong": str(_OPEN + 60_000),
            "d": "20261123",
            "z": "-",
            "v": "-",
            "a": "-",
            "f": "-",
            "b": "0.0000_3685.0000_",
            "g": "12_34_",
            "trade": {"t": "bad", "z": "3685.0000"},
        },
        5,
    )
    assert quote is not None
    assert quote.asks == ()
    assert quote.bids == ((_D(3685), _D(34000)),)
    assert quote.snapshot.cumulative_volume == 0
    assert quote.snapshot.trade_ms is None
    assert quote.last == _D(3685)


def _mis_body(codes: list[str], tlong: int, lots: int) -> dict[str, Any]:
    """Builds a minimal MIS body.

    Args:
        codes: Codes with records.
        tlong: Data time.
        lots: Cumulative board lots.

    Returns:
        Body.
    """
    return {
        "rtcode": "0000",
        "msgArray": [
            {
                "c": code,
                "tlong": str(tlong),
                "d": "20261123",
                "z": "100.0000",
                "v": str(lots),
                "a": "100.5000_",
                "f": "1_",
                "b": "100.0000_",
                "g": "2_",
            }
            for code in codes
        ],
    }


async def test_mis_client_caches_and_coalesces() -> None:
    """Concurrent callers share one request; fresh codes come from memory."""
    clock = clock_lib.ManualClock(_OPEN)
    path = "/stock/api/getStockInfo.jsp"

    def answer(params: dict[str, Any]) -> dict[str, Any]:
        """Returns records for the requested codes.

        Args:
            params: Query.

        Returns:
            Body.
        """
        codes = [c[4:-3] for c in params["ex_ch"].split("|")]
        return _mis_body([c for c in codes if c != "0000"], _OPEN, 1)

    http = _Http({path: answer})
    client = twse.MisClient(http, clock, batch=2)  # type: ignore[arg-type]
    first, second = await asyncio.gather(
        client.quotes(["2330", "0050"]), client.quotes(["2317", "0000"])
    )
    assert set(first) == {"2330", "0050"} and set(second) == {"2317"}
    assert client.requests == 2  # Three codes in batches of two.
    await client.quotes(["2330"])
    assert client.requests == 2
    clock.advance(5_000)
    await client.quotes(["2330"])
    assert client.requests == 3


async def test_mis_client_serves_callers_arriving_mid_request() -> None:
    """A caller joining while a batch is in flight gets the next batch."""
    release = asyncio.Event()
    started = asyncio.Event()
    seen: list[str] = []

    class _Slow(_Http):
        """Stub whose first request waits for a release."""

        async def get(
            self, path: str, params: dict[str, Any] | None = None
        ) -> Any:
            """Answers after the release.

            Args:
                path: Path.
                params: Query.

            Returns:
                Body.
            """
            assert params is not None
            seen.append(params["ex_ch"])
            started.set()
            if len(seen) == 1:
                await release.wait()
            codes = [c[4:-3] for c in params["ex_ch"].split("|")]
            return _mis_body(codes, _OPEN, 1)

    client = twse.MisClient(_Slow({}), clock_lib.ManualClock(_OPEN))  # type: ignore[arg-type]
    first = asyncio.create_task(client.quotes(["2330"]))
    await asyncio.wait_for(started.wait(), 5)
    second = asyncio.create_task(client.quotes(["0050"]))
    await asyncio.sleep(0.05)
    release.set()
    assert set(await asyncio.wait_for(first, 5)) == {"2330"}
    assert set(await asyncio.wait_for(second, 5)) == {"0050"}
    assert seen == ["tse_2330.tw", "tse_0050.tw"]


async def test_mis_client_reports_errors_to_every_caller() -> None:
    """A MIS error reaches all coalesced callers."""
    http = _Http({"/stock/api/getStockInfo.jsp": {"rtcode": "9999"}})
    client = twse.MisClient(http, clock_lib.ManualClock(0))  # type: ignore[arg-type]
    results = await asyncio.gather(
        client.quotes(["2330"]), client.quotes(["0050"]), return_exceptions=True
    )
    assert all(isinstance(r, errors.VenueUnavailableError) for r in results)


async def test_recorder_polls_during_sessions_and_expires_watches() -> None:
    """The poller waits outside sessions, polls inside and stops when idle."""
    clock = clock_lib.ManualClock(_OPEN - 3_600_000)
    sleeps: list[float] = []
    lots = iter(range(1, 100))

    async def sleep(seconds: float) -> None:
        """Advances the clock instead of sleeping.

        Args:
            seconds: Requested sleep.
        """
        sleeps.append(seconds)
        clock.advance(int(seconds * 1000))
        if len(sleeps) > 70:
            adapter._watch.clear()
        else:
            adapter._watch["2330"] = clock.now_ms()  # Charts keep asking.

    def answer(params: dict[str, Any]) -> dict[str, Any]:
        """Returns a snapshot with growing volume.

        Args:
            params: Query.

        Returns:
            Body.
        """
        del params
        return _mis_body(["2330"], clock.now_ms(), next(lots))

    adapter = _adapter(
        clock, mis={"/stock/api/getStockInfo.jsp": answer}, sleep=sleep
    )
    assert adapter._recording_window(clock.now_ms()) > 0
    adapter.watch("2330")
    assert adapter._poller is not None
    await asyncio.wait_for(adapter._poller, 5)
    assert 60.0 in sleeps and twse.POLL_INTERVAL_S in sleeps
    assert adapter._builders["2330"].applied > 1
    adapter._watch["2330"] = clock.now_ms() - 31 * 60_000
    assert await adapter.poll_once() == 0
    assert adapter._watch == {}
    assert (
        adapter.ingest(
            [
                q
                for r in _mis_body(["9999"], _OPEN, 1)["msgArray"]
                if (q := twse.parse_mis_record(r, 0)) is not None
            ]
        )
        == 0
    )
    await adapter.close()


def _snap(
    offset_s: float,
    lots: int,
    *,
    z: str | None = None,
    trade: tuple[float, str] | None = None,
    high: str = "100",
    low: str = "100",
    bid: str = "99.5",
    ask: str = "100.5",
    day: datetime.date = _DAY,
) -> snapshot_bars.Snapshot:
    """Builds a snapshot relative to the open.

    Args:
        offset_s: Seconds after the open.
        lots: Cumulative board lots.
        z: Price printed at the data time.
        trade: ``(offset_s, price)`` of the reported last trade.
        high: Day high.
        low: Day low.
        bid: Best bid.
        ask: Best ask.
        day: Trading date.

    Returns:
        Snapshot.
    """
    base = _OPEN
    if day != _DAY:
        found = sessions.XTAI.session(day)
        assert found is not None
        base = found.open_ms
    return snapshot_bars.Snapshot(
        day=day,
        data_ms=base + int(offset_s * 1000),
        cumulative_volume=_D(lots * 1000),
        last_price=None if z is None else _D(z),
        trade_price=None if trade is None else _D(trade[1]),
        trade_ms=None if trade is None else base + int(trade[0] * 1000),
        day_high=_D(high),
        day_low=_D(low),
        best_bid=_D(bid),
        best_ask=_D(ask),
    )


def test_snapshot_builder_prices_volumes_and_classifies() -> None:
    """Derived trades are priced, attributed and classified."""
    builder = snapshot_bars.SnapshotBarBuilder(_ID, source="test")
    assert builder.apply(_snap(-60, 0)) == []  # Pre-open baseline.
    opening = builder.apply(_snap(0, 10, z="100"))
    assert opening[0].quantity == _D(10_000) and opening[0].price == _D(100)
    builder.apply(_snap(20, 12, trade=(18, "100.5"), high="100.5"))
    unpriced = builder.apply(_snap(40, 13, trade=(5, "101")))
    assert unpriced[0].price is None
    assert unpriced[0].side.value == "unknown"
    builder.apply(_snap(65, 15, z="99.5", low="99.5", bid="99.5", ask="100"))
    assert builder.apply(_snap(64, 16)) == []  # Older data is ignored.
    assert builder.apply(_snap(70, 14)) == []  # Falling volume is ignored.
    assert builder.ignored == 2
    bars = builder.bars(_OPEN, _OPEN + 3_600_000, _OPEN + 70_000)
    first, second = bars
    assert first.start_ms == _OPEN and first.is_final
    assert (first.open, first.high, first.close) == (
        _D(100),
        _D("100.5"),
        _D("100.5"),
    )
    assert first.base_volume == _D(13_000)
    assert first.taker_buy_base_volume is None  # One unpriced increment.
    assert second.start_ms == _OPEN + 60_000 and not second.is_final
    assert second.low == _D("99.5") and second.taker_buy_base_volume == _D(0)


def test_snapshot_builder_quiet_bars_extremes_and_gaps() -> None:
    """Quiet minutes are flat; in-bar extremes are exact; gaps segment."""
    builder = snapshot_bars.SnapshotBarBuilder(
        _ID, source="test", max_gap_ms=600_000, retention_bars=50
    )
    builder.apply(_snap(-30, 0))
    builder.apply(_snap(5, 1, z="100"))
    builder.apply(_snap(10, 2, z="100", high="102"))
    builder.apply(_snap(190, 2))  # Two quiet minutes in between.
    bars = {
        (c.start_ms - _OPEN) // 60_000: c
        for c in builder.bars(_OPEN, _OPEN + 600_000, _OPEN + 200_000)
    }
    assert bars[0].high == _D(102)  # The day high moved inside bar 0.
    assert bars[1].base_volume == 0 and bars[2].base_volume == 0
    assert bars[1].close == _D(100) and bars[1].is_final
    builder.apply(_snap(1_000, 5, z="101"))  # A 13-minute gap.
    later = builder.bars(_OPEN + 900_000, _OPEN + 1_200_000, _OPEN)
    assert later == []  # The baseline's minute is partial.
    builder.apply(_snap(1_030, 6, z="101"))
    assert builder.segment_at(_OPEN + 1_020_000) == (
        _OPEN + 1_000_000,
        _OPEN + 1_030_000,
    )
    builder.apply(_snap(16_210, 9, z="102"))  # Closing auction, in grace.
    tail = builder.bars(_SESSION.close_ms - 60_000, _SESSION.close_ms, _OPEN)
    assert tail == []  # Separated from the earlier segment by the gap.
    builder.apply(_snap(16_300, 10, z="102"))  # Past the close grace.
    tomorrow = _snap(-10, 0, day=datetime.date(2026, 11, 24))
    builder.apply(tomorrow)
    assert builder.latest == tomorrow


def test_snapshot_builder_on_recorded_mis_data() -> None:
    """Recorded MIS snapshots attribute exactly the observed volume."""
    builder = snapshot_bars.SnapshotBarBuilder(
        instruments.InstrumentId.parse("twse:etf:0050"), source="test"
    )
    quotes = [
        q
        for item in _load("mis_recorded")["responses"]
        for r in item["response"]["msgArray"]
        if (q := twse.parse_mis_record(r, item["received_ms"])) is not None
        and q.code == "0050"
    ]
    trades = [t for q in quotes for t in builder.apply(q.snapshot)]
    baseline, latest = quotes[0].snapshot, builder.latest
    assert latest is not None
    assert sum(t.quantity for t in trades) == (
        latest.cumulative_volume - baseline.cumulative_volume
    )
    assert builder.ignored > 0  # Cached and stale responses repeat data.
    bars = builder.bars(_OPEN, _SESSION.close_ms, latest.data_ms)
    assert sum(b.base_volume for b in bars) <= sum(t.quantity for t in trades)


def test_closing_auction_lands_in_the_last_bar() -> None:
    """The 13:30 auction print belongs to the session's last 1m bar."""
    builder = snapshot_bars.SnapshotBarBuilder(_ID, source="test")
    trades = []
    for item in _load("mis_close")["responses"]:
        for record in item["response"]["msgArray"]:
            quote = twse.parse_mis_record(record, 0)
            if quote is not None and quote.code == "2330":
                trades += builder.apply(quote.snapshot)
    auction = trades[-1]
    assert auction.data_ms == _SESSION.close_ms
    assert auction.quantity == _D(5897) * 1000  # tv: one print, in lots.
    assert auction.price == _D(2030)
    last = builder.bars(_SESSION.close_ms - 60_000, _SESSION.close_ms, 0)
    assert len(last) == 1 and last[0].is_final
    assert last[0].close == _D(2030) and last[0].base_volume == _D(5_897_000)


def test_snapshot_builder_coverage_is_the_recording() -> None:
    """Segments are coverage, minus unpriced minutes and evicted bars."""
    builder = snapshot_bars.SnapshotBarBuilder(
        _ID, source="test", retention_bars=3
    )
    assert builder.coverage() == []
    builder.apply(_snap(-30, 0))  # Pre-open baseline.
    builder.apply(_snap(5, 1, z="100"))  # Minute 0, priced.
    builder.apply(_snap(130, 2, trade=(1, "100")))  # Minute 2, unpriced.
    builder.apply(_snap(190, 2))  # No volume: nothing provable to add.
    assert builder.coverage() == [
        (_OPEN - 30_000, _OPEN + 120_000),
        (_OPEN + 180_000, _OPEN + 190_000),
    ]
    minute = 60_000
    for offset, lots in ((250, 3), (310, 4), (370, 5)):
        builder.apply(_snap(offset, lots, z="101"))
    covered = builder.coverage()
    assert covered[0][0] == _OPEN + 3 * minute  # Minutes 0-2 were dropped.
    assert covered[-1][1] == _OPEN + 370_000


def _tpe_ms(day: datetime.date, hour: int, minute: int = 0) -> int:
    """Returns the UTC epoch ms of a Taipei time.

    Args:
        day: Date.
        hour: Hour.
        minute: Minute.

    Returns:
        Milliseconds.
    """
    moment = datetime.datetime.combine(day, datetime.time(hour, minute))
    return int(moment.replace(tzinfo=_TPE).timestamp() * 1000)


def test_month_is_complete_only_after_it_ended() -> None:
    """A month read before its last day ended may still gain sessions."""
    september = datetime.date(2026, 9, 29)
    assert not twse.month_complete(2026, 9, _tpe_ms(september, 15))
    last = datetime.date(2026, 9, 30)
    assert not twse.month_complete(2026, 9, _tpe_ms(last, 23, 59))
    october = datetime.date(2026, 10, 1)
    assert twse.month_complete(2026, 9, _tpe_ms(october, 0))
    assert twse.month_complete(2026, 12, _tpe_ms(datetime.date(2027, 1, 1), 0))
    eve = datetime.date(2026, 12, 31)
    assert not twse.month_complete(2026, 12, _tpe_ms(eve, 20))


@pytest.mark.parametrize("venue", ["twse", "tpex"])
async def test_month_read_on_its_29th_is_read_again_later(venue: str) -> None:
    """A past month read on the 29th is re-read; read after its end, never."""
    clock = clock_lib.ManualClock(_tpe_ms(datetime.date(2026, 9, 29), 15))
    reads: list[str] = []

    def answer(params: dict[str, Any]) -> dict[str, Any]:
        """Counts reads of the monthly report.

        Args:
            params: Query.

        Returns:
            An empty month.
        """
        reads.append(str(params["date"]))
        return {"stat": "OK" if venue == "twse" else "ok", "data": []}

    adapter: twse.TaiwanExchangeAdapter
    if venue == "twse":
        adapter = _adapter(
            clock, www={"/rwd/zh/afterTrading/STOCK_DAY": answer}
        )
        instrument = _ID
    else:
        adapter = tpex.TpexAdapter(
            _Http({}),  # type: ignore[arg-type]
            _Http({"/www/zh-tw/afterTrading/tradingStock": answer}),  # type: ignore[arg-type]
            _Http({}),  # type: ignore[arg-type]
            clock,
        )
        instrument = instruments.InstrumentId.parse("tpex:stock:6488")
    await adapter._month(instrument, 2026, 9)
    clock.advance(2 * 86_400_000)  # 1 October: September has ended.
    await adapter._month(instrument, 2026, 9)
    assert len(reads) == 2  # Before the fix this came from the cache.
    clock.advance(40 * 86_400_000)
    await adapter._month(instrument, 2026, 9)
    assert len(reads) == 2  # Complete as of its read: cached for good.
    await adapter.close()


async def _parked(seconds: float) -> None:
    """Keeps the MIS recorder idle (tests apply snapshots by hand).

    Args:
        seconds: Ignored.
    """
    del seconds
    await asyncio.Event().wait()


def _listed(clock: clock_lib.ManualClock, archive: Any) -> twse.TwseAdapter:
    """Builds a TWSE adapter over an archive with the listings loaded.

    Args:
        clock: Clock.
        archive: Bar archive.

    Returns:
        Adapter.
    """
    adapter = _adapter(
        clock,
        mis={
            "/stock/api/getStockInfo.jsp": lambda params: _mis_body(
                ["2330"], clock.now_ms(), 1
            )
        },
        sleep=_parked,
        bar_archive=archive,
    )
    rows, meta = twse.parse_instruments(
        _load("stock_day_all"),
        _load("t187ap03_L"),
        _load("t187ap47_L"),
        _load("twt88u"),
        _load("twtawu"),
        now_ms=clock.now_ms(),
    )
    adapter._instruments = {r.instrument_id.venue_market_id: r for r in rows}
    adapter._loaded_at_ms = clock.now_ms()
    adapter.listing = meta
    return adapter


async def test_recorded_minutes_and_coverage_survive_a_restart() -> None:
    """MIS recordings are archived with their coverage and read back."""
    archive = bar_archive_sql.SqlBarArchive(db.create_engine("sqlite://"))
    clock = clock_lib.ManualClock(_OPEN + 200_000)
    first = _listed(clock, archive)
    first.watch("2330")
    builder = first._builders["2330"]
    builder.apply(_snap(-30, 0))
    builder.apply(_snap(5, 1, z="100"))
    builder.apply(_snap(70, 3, z="101", high="101"))
    builder.apply(_snap(190, 3))
    live = await first.fetch_candle_coverage(_ID, _M1, _OPEN, _OPEN + 600_000)
    assert first.persist_recordings(clock.now_ms()) == 3
    assert first.persist_recordings(clock.now_ms()) == 0  # Written recently.
    await first.close()
    stored = archive.minutes(_ID, twse.MIS_SOURCE, _OPEN, _OPEN + 600_000)
    assert [c.start_ms for c in stored] == [
        _OPEN,
        _OPEN + 60_000,
        _OPEN + 120_000,
    ]
    assert archive.coverage(_ID, twse.MIS_SOURCE, 0, 2**62) == [
        (_OPEN, _OPEN + 180_000)
    ]
    restarted = _listed(clock, archive)  # A new process: nothing recorded.
    again = await restarted.fetch_candle_coverage(
        _ID, _M1, _OPEN, _OPEN + 600_000
    )
    assert [c.start_ms for c in again.candles] == [
        c.start_ms for c in live.candles if c.is_final
    ]
    assert again.covered == ((_OPEN, _OPEN + 180_000),)
    five = await restarted.fetch_candle_coverage(
        _ID, timeframes.Timeframe.M5, _OPEN, _OPEN + 600_000
    )
    assert [(c.start_ms, c.base_volume, c.is_final) for c in five.candles] == [
        (_OPEN, _D(3_000), False)
    ]
    await restarted.close()


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


async def test_archive_failures_never_stop_the_recorder(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A broken archive is logged and the in-memory recording still serves."""
    clock = clock_lib.ManualClock(_OPEN + 200_000)
    adapter = _adapter(clock, bar_archive=_BrokenArchive())
    builder = snapshot_bars.SnapshotBarBuilder(_ID, source=twse.MIS_SOURCE)
    adapter._builders["2330"] = builder
    builder.apply(_snap(-30, 0))
    builder.apply(_snap(5, 1, z="100"))
    builder.apply(_snap(70, 3, z="101"))
    assert adapter.persist_recordings(clock.now_ms(), force=True) == 0
    bars, covered = adapter._mis_minutes(_ID, _OPEN, _OPEN + 600_000)
    assert [b.start_ms for b in bars] == [_OPEN, _OPEN + 60_000]
    assert covered
    assert "archiving recorded bars failed" in caplog.text
    assert "bar archive prune failed" in caplog.text
    assert "bar archive read failed" in caplog.text
