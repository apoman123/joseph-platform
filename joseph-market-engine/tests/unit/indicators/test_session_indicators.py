# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Indicator engine, plugins and queries on exchange session calendars."""

from __future__ import annotations

import asyncio
import dataclasses
import datetime
import decimal
import zoneinfo
from collections.abc import Callable
from collections.abc import Sequence

import pytest

from joseph.domain import clock as clock_lib
from joseph.domain import errors
from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import sessions
from joseph.domain import structures
from joseph.domain import timeframes
from joseph.indicators import catalog
from joseph.indicators import service
from joseph.indicators import testing
from joseph.indicators.packaged import plugins as packaged
from joseph.indicators.sdk import engine as engine_lib
from joseph.indicators.sdk import protocol

TF = timeframes.Timeframe
NY = zoneinfo.ZoneInfo("America/New_York")
ACME = testing.STOCK_INSTRUMENT
XNYS = sessions.XNYS
Validate = Callable[[str, str, object], None]


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


def _stock_minutes(sessions_count: int, seed: int = 7) -> list[market.Candle]:
    """Builds ACME 1m bars for whole sessions from Mon 16 Nov 2026.

    Args:
        sessions_count: Sessions to cover.
        seed: Random seed.

    Returns:
        1m candles.
    """
    return testing.random_walk(
        390 * sessions_count,
        seed=seed,
        instrument_id=ACME,
        start_ms=testing.STOCK_START_MS,
    )


class _Source:
    """In-memory candle source recording requested windows."""

    def __init__(self, data: dict[TF, list[market.Candle]]) -> None:
        """Stores candles.

        Args:
            data: Candles per timeframe.
        """
        self.data = data
        self.requests: list[tuple[TF, int, int]] = []

    async def get_candles(
        self,
        instrument_id: instruments.InstrumentId,
        timeframe: TF,
        start_ms: int,
        end_ms: int,
    ) -> Sequence[market.Candle]:
        """Returns stored candles in the window.

        Args:
            instrument_id: Ignored (single instrument).
            timeframe: Timeframe.
            start_ms: Window start.
            end_ms: Window end.

        Returns:
            Candles.
        """
        del instrument_id
        self.requests.append((timeframe, start_ms, end_ms))
        return [
            c
            for c in self.data.get(timeframe, [])
            if start_ms <= c.start_ms < end_ms
        ]


# Test builders -----------------------------------------------------------


def test_builders_produce_session_bars() -> None:
    """ACME 1m bars aggregate into 7 hourly bars and one daily bar."""
    m1 = _stock_minutes(2)
    assert m1[0].start_ms == _ny(2026, 11, 16, 9, 30)
    assert m1[389].end_ms == _ny(2026, 11, 16, 16)
    assert m1[390].start_ms == _ny(2026, 11, 17, 9, 30)
    hours = testing.aggregate(m1, TF.H1)
    assert len(hours) == 14
    assert hours[6].start_ms == _ny(2026, 11, 16, 15, 30)
    assert hours[6].end_ms == _ny(2026, 11, 16, 16)
    days = testing.aggregate(m1, TF.D1)
    assert [(d.start_ms, d.end_ms) for d in days] == [
        (_ny(2026, 11, 16, 9, 30), _ny(2026, 11, 16, 16)),
        (_ny(2026, 11, 17, 9, 30), _ny(2026, 11, 17, 16)),
    ]
    assert days[0].base_volume == sum(
        (c.base_volume for c in m1[:390]), decimal.Decimal(0)
    )
    with pytest.raises(ValueError, match="does not nest"):
        testing.aggregate(testing.aggregate(m1, TF.M3), TF.M5)


def test_aggregate_sums_buy_volume_and_keeps_its_method() -> None:
    """Aggregated bars carry the members' classified buy volume."""
    m1 = [
        dataclasses.replace(
            c,
            taker_buy_base_volume=c.base_volume,
            taker_buy_method=market.BuyVolumeMethod.CLASSIFIED,
        )
        for c in _stock_minutes(1)
    ]
    hour = testing.aggregate(m1, TF.H1)[0]
    assert hour.taker_buy_base_volume == hour.base_volume
    assert hour.buy_volume_method is market.BuyVolumeMethod.CLASSIFIED


# Engine ------------------------------------------------------------------


def _daily_bias_engine() -> engine_lib.IndicatorEngine:
    """Builds an ACME engine with 1h frames and 1h/1d daily bias.

    Returns:
        Engine.
    """
    return engine_lib.IndicatorEngine(
        catalog.build_default_registry(),
        engine_lib.EngineConfig(
            TF.H1, {"daily_bias": engine_lib.PluginSpec((TF.H1, TF.D1))}
        ),
        ACME,
    )


def test_daily_bar_is_visible_to_the_last_hour_of_the_session() -> None:
    """At 16:00 the 15:30 frame sees the daily bar closing with it."""
    m1 = _stock_minutes(6)
    data = {tf: testing.aggregate(m1, tf) for tf in (TF.H1, TF.H4, TF.D1)}
    frames = _daily_bias_engine().run(data)
    assert len(frames) == len(data[TF.H1])
    for frame in frames:
        daily = frame.snapshot("daily_bias", TF.D1)
        closed = XNYS.last_closed_start(TF.D1, frame.close_ms)
        assert closed is not None
        if daily is None:
            assert closed < data[TF.D1][0].start_ms  # Before the data.
            continue
        assert daily.as_of_ms == XNYS.end_of(TF.D1, closed)
        assert daily.as_of_ms <= frame.close_ms
    last_hour = frames[6]
    assert last_hour.close_ms == _ny(2026, 11, 16, 16)
    snapshot = last_hour.snapshot("daily_bias", TF.D1)
    assert snapshot is not None
    assert snapshot.as_of_ms == _ny(2026, 11, 16, 16)


def test_late_daily_bar_holds_the_session_close_frame() -> None:
    """Feeding the 1d bars late yields the batch frames exactly."""
    m1 = _stock_minutes(4)
    hours = testing.aggregate(m1, TF.H1)
    fours = testing.aggregate(m1, TF.H4)
    days = testing.aggregate(m1, TF.D1)
    batch = _daily_bias_engine().run({TF.H1: hours, TF.H4: fours, TF.D1: days})
    engine = _daily_bias_engine()
    assert engine.feed(sorted(hours + fours, key=lambda c: c.end_ms)) == []
    late = engine.feed(days) + engine.flush()
    assert [f.close_ms for f in late] == [f.close_ms for f in batch]
    assert [f.snapshots for f in late] == [f.snapshots for f in batch]
    assert engine.stats.forced_items == 0


def test_nights_weekends_and_holidays_are_never_missing_bars() -> None:
    """Thanksgiving week 1h frames report no missing bar until one is cut."""
    hours = testing.random_walk(
        7 * 3 + 4 + 7,
        seed=3,
        timeframe=TF.H1,
        instrument_id=ACME,
        start_ms=_ny(2026, 11, 23, 9, 30),
    )
    assert hours[-1].start_ms == _ny(2026, 11, 30, 15, 30)
    engine = engine_lib.IndicatorEngine(
        catalog.build_default_registry(),
        engine_lib.EngineConfig(
            TF.H1, {"market_structure": engine_lib.PluginSpec((TF.H1,))}
        ),
        ACME,
    )
    frames = engine.run({TF.H1: hours})
    assert frames[-1].missing_bars == 0
    snapshot = frames[-1].snapshot("market_structure", TF.H1)
    assert snapshot is not None
    assert snapshot.data_quality.missing_bars == 0
    holed = engine_lib.IndicatorEngine(
        catalog.build_default_registry(),
        engine_lib.EngineConfig(
            TF.H1, {"market_structure": engine_lib.PluginSpec((TF.H1,))}
        ),
        ACME,
    ).run({TF.H1: hours[:-3] + hours[-2:]})
    assert holed[-1].missing_bars == 1


def test_series_header_counts_missing_bars_on_the_calendar() -> None:
    """Friday 13:00 close to Monday 09:30 is not a gap; a skipped bar is."""
    header = protocol.SeriesHeader(
        instrument_id=ACME, timeframe=TF.H1, settings=()
    )
    friday = testing.make_candle(
        TF.H1, _ny(2026, 11, 27, 12, 30), 1, 1, 1, 1, instrument_id=ACME
    )
    monday = testing.make_candle(
        TF.H1, _ny(2026, 11, 30, 9, 30), 1, 1, 1, 1, instrument_id=ACME
    )
    later = testing.make_candle(
        TF.H1, _ny(2026, 11, 30, 11, 30), 1, 1, 1, 1, instrument_id=ACME
    )
    header = header.advance(friday).advance(monday)
    assert header.missing_bars == 0
    assert header.advance(later).missing_bars == 1


def test_every_plugin_runs_on_session_bars() -> None:
    """All plugins run on ACME 5m frames with 1m/1h/4h/1d inputs."""
    m1 = _stock_minutes(5, seed=11)
    data = {
        tf: testing.aggregate(m1, tf) if tf is not TF.M1 else m1
        for tf in (TF.M1, TF.M5, TF.H1, TF.H4, TF.D1)
    }
    registry = catalog.build_default_registry()
    plugins = {pid: engine_lib.PluginSpec((TF.M5,)) for pid in registry.ids()}
    plugins["daily_bias"] = engine_lib.PluginSpec((TF.M5, TF.H1))
    engine = engine_lib.IndicatorEngine(
        registry, engine_lib.EngineConfig(TF.M5, plugins), ACME
    )
    assert engine.calendar is XNYS
    frames = engine.run(data)
    assert len(frames) == len(data[TF.M5])
    assert all(frame.missing_bars == 0 for frame in frames)
    aois = [
        aoi
        for frame in frames
        for snapshot in frame.snapshots.values()
        for aoi in snapshot.aois
    ]
    assert aois
    for aoi in aois:
        assert aoi.confirmed_at_ms <= aoi.as_of_ms
        assert XNYS.is_open(aoi.confirmed_at_ms - 1)


def test_engine_accepts_order_flow_on_every_timeframe_on_xnys() -> None:
    """Auto intrabars nest on the session grid for every chart timeframe."""
    registry = catalog.build_default_registry()
    for chart in TF:
        engine_lib.IndicatorEngine(
            registry,
            engine_lib.EngineConfig(
                chart, {"order_flow_pressure": engine_lib.PluginSpec((chart,))}
            ),
            ACME,
        )


# Order-flow pressure -------------------------------------------------------


def _classified(
    candles: Sequence[market.Candle], share: str
) -> list[market.Candle]:
    """Adds classified buy volume of ``share`` x volume to candles.

    Args:
        candles: Source candles.
        share: Fraction of the volume classified as buys.

    Returns:
        Candles with ``buy_volume_method = classified``.
    """
    return [
        dataclasses.replace(
            candle,
            taker_buy_base_volume=candle.base_volume * decimal.Decimal(share),
            taker_buy_method=market.BuyVolumeMethod.CLASSIFIED,
        )
        for candle in candles
    ]


def test_classified_delta_is_labelled_trade_classification(
    contract_validate: Validate,
) -> None:
    """Stock-exchange buy volume is never presented as true taker delta."""
    plugin = packaged.OrderFlowPressurePlugin()
    candles = _classified(
        testing.random_walk(
            30,
            timeframe=TF.M5,
            seed=3,
            instrument_id=ACME,
            start_ms=testing.STOCK_START_MS,
        ),
        "0.75",
    )
    snapshot, _ = testing.run_bars(plugin, candles)[-1]
    assert snapshot.values["method"] == "trade_classification"
    assert snapshot.method is structures.IndicatorMethod.TRADE_CLASSIFICATION
    assert snapshot.values["delta_pct"] == decimal.Decimal(50)
    contract = snapshot.to_contract()
    assert contract["method"] == "trade_classification"
    contract_validate("indicators.schema.json", "IndicatorSnapshot", contract)


# Anchored VWAP on calendars -----------------------------------------------


def test_session_vwap_on_xnys_anchors_at_each_session_open() -> None:
    """Session anchors restart at 09:30, weeks at their first session."""
    hours = testing.random_walk(
        7 * 3 + 4 + 7,
        seed=5,
        timeframe=TF.H1,
        instrument_id=ACME,
        start_ms=_ny(2026, 11, 23, 9, 30),
    )
    plugin = packaged.AnchoredVwapPlugin()
    daily = testing.run_bars(plugin, hours, {"source": "close"})
    assert daily[3][0].values["anchor_ms"] == _ny(2026, 11, 23, 9, 30)
    assert daily[7][0].values["anchor_ms"] == _ny(2026, 11, 24, 9, 30)
    assert daily[7][0].values["bars"] == 1
    weekly = testing.run_bars(plugin, hours, {"anchor": "week"})
    assert weekly[24][0].values["anchor_ms"] == _ny(2026, 11, 23, 9, 30)
    assert weekly[24][0].values["bars"] == 25
    assert weekly[25][0].values["anchor_ms"] == _ny(2026, 11, 30, 9, 30)
    monthly = testing.run_bars(plugin, hours, {"anchor": "month"})
    assert monthly[-1][0].values["bars"] == len(hours)


def test_session_vwap_follows_sessions_that_cross_utc_midnight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 10:00-16:00 Auckland session (21:00-03:00 UTC) is one anchor."""
    nzx = instruments.InstrumentId.parse("nyse:stock:NZX")
    calendar = sessions.ExchangeCalendar(
        sessions.CalendarId.XNYS,
        "Pacific/Auckland",
        datetime.time(10, 0),
        datetime.time(16, 0),
        is_closed=lambda day: False,
        first_day=datetime.date(2026, 1, 1),
    )
    original = sessions.for_instrument
    monkeypatch.setattr(
        sessions,
        "for_instrument",
        lambda iid: calendar if iid == nzx else original(iid),
    )
    opened = calendar.session(datetime.date(2026, 11, 17))
    assert opened is not None
    hours = testing.random_walk(
        12, seed=2, timeframe=TF.H1, instrument_id=nzx, start_ms=opened.open_ms
    )
    assert hours[3].start_ms % 86_400_000 == 0  # 00:00 UTC inside a session.
    results = testing.run_bars(packaged.AnchoredVwapPlugin(), hours)
    anchors = [snapshot.values["anchor_ms"] for snapshot, _ in results]
    assert anchors[:6] == [opened.open_ms] * 6
    assert anchors[6] == hours[6].start_ms
    assert [s.values["bars"] for s, _ in results[:7]] == [1, 2, 3, 4, 5, 6, 1]


def test_time_anchor_between_sessions_uses_the_last_bar_before_it() -> None:
    """A Saturday anchor on 1h bars anchors at Friday's last bar."""
    hours = testing.random_walk(
        8,
        seed=4,
        timeframe=TF.H1,
        instrument_id=ACME,
        start_ms=_ny(2026, 11, 27, 9, 30),
    )
    results = testing.run_bars(
        packaged.AnchoredVwapPlugin(),
        hours,
        {"anchor": "time", "anchor_ms": _ny(2026, 11, 28, 12)},
    )
    assert results[3][0].values["anchor_ms"] == _ny(2026, 11, 27, 12, 30)
    assert all(s.values["anchor_in_window"] for s, _ in results)
    with pytest.raises(errors.ValidationFailedError, match="XNYS"):
        packaged.AnchoredVwapPlugin().initialize(
            ACME, TF.H1, {"anchor": "time", "anchor_ms": 1}
        )


# Anchored series queries ---------------------------------------------------


def _query(
    bars: list[market.Candle], config: service.QueryConfig | None = None
) -> tuple[service.IndicatorQueryService, _Source, clock_lib.ManualClock]:
    """Builds a query service over 1m bars.

    Args:
        bars: 1m candles.
        config: Optional tuning.

    Returns:
        Service, source and clock.
    """
    source = _Source({TF.M1: bars})
    clock = clock_lib.ManualClock(bars[-1].end_ms)
    query = service.IndicatorQueryService(
        source, catalog.build_default_registry(), clock, config
    )
    return query, source, clock


@pytest.mark.parametrize(
    ("instrument", "start"),
    [
        (testing.DEFAULT_INSTRUMENT, testing.DEFAULT_START_MS),
        (ACME, testing.STOCK_START_MS),
    ],
    ids=["24x7", "XNYS"],
)
def test_anchored_vwap_beyond_two_thousand_bars(
    instrument: instruments.InstrumentId,
    start: int,
    contract_validate: Validate,
) -> None:
    """A 4,990-bar-old anchor is computed from the anchor bar onward."""
    bars = testing.random_walk(
        5_000, seed=9, instrument_id=instrument, start_ms=start
    )
    query, _, _ = _query(bars)
    anchor = bars[10].start_ms
    series = asyncio.run(
        query.series(
            instrument,
            TF.M1,
            "anchored_vwap",
            from_ms=anchor,
            settings={"anchor": "time", "anchor_ms": anchor},
        )
    )
    contract_validate("indicators.schema.json", "IndicatorSeries", series)
    points = series["points"]
    assert isinstance(points, list) and len(points) == 4_990
    assert all(p["values"]["anchor_in_window"] for p in points)
    assert points[0]["values"]["anchor_ms"] == anchor
    assert points[-1]["values"]["bars"] == 4_990


def test_anchored_regression_reaches_its_first_anchor() -> None:
    """The regression channel fits from a far t1 to the requested t2."""
    bars = testing.random_walk(4_000, seed=13)
    query, _, _ = _query(bars)
    t1, t2 = bars[100].start_ms, bars[3_900].start_ms
    series = asyncio.run(
        query.series(
            testing.DEFAULT_INSTRUMENT,
            TF.M1,
            "regression_channel",
            from_ms=t2,
            settings={"anchor_ms": t1},
        )
    )
    points = series["points"]
    assert isinstance(points, list)
    assert points[0]["bar_start_ms"] == t2
    assert points[0]["values"]["start_ms"] == t1
    assert points[0]["values"]["bars"] == 3_801


def test_anchor_beyond_the_hard_cap_is_refused() -> None:
    """An anchor more than ``max_anchored_bars`` back is a clear error."""
    bars = testing.random_walk(3_000, seed=1)
    query, _, _ = _query(bars, service.QueryConfig(max_anchored_bars=2_500))
    with pytest.raises(errors.ValidationFailedError, match="at most 2500"):
        asyncio.run(
            query.series(
                testing.DEFAULT_INSTRUMENT,
                TF.M1,
                "anchored_vwap",
                settings={"anchor": "time", "anchor_ms": bars[0].start_ms},
            )
        )


@pytest.mark.parametrize(
    ("instrument", "start"),
    [
        (testing.DEFAULT_INSTRUMENT, testing.DEFAULT_START_MS),
        (ACME, testing.STOCK_START_MS),
    ],
    ids=["24x7", "XNYS"],
)
def test_anchor_cap_counts_every_closed_bar_on_a_boundary(
    instrument: instruments.InstrumentId, start: int
) -> None:
    """At a bar boundary the cap counts all closed bars, not one fewer."""
    bars = testing.random_walk(
        2_600, seed=3, instrument_id=instrument, start_ms=start
    )
    query, _, _ = _query(bars, service.QueryConfig(max_anchored_bars=2_500))
    at_ms = bars[-1].end_ms
    if instrument is ACME:
        assert XNYS.is_open(at_ms)

    def snapshot(anchor_index: int) -> dict[str, object]:
        """Requests the anchored VWAP anchored at one bar.

        Args:
            anchor_index: Index of the anchor bar.

        Returns:
            Contract ``IndicatorSnapshotList``.
        """
        anchor = bars[anchor_index].start_ms
        return asyncio.run(
            query.snapshots(
                instrument,
                TF.M1,
                ["anchored_vwap"],
                at_ms,
                {"anchored_vwap": {"anchor": "time", "anchor_ms": anchor}},
            )
        )

    values = snapshot(-2_500)["snapshots"][0]["values"]  # type: ignore[index]
    assert values["bars"] == 2_500
    with pytest.raises(errors.ValidationFailedError, match="is 2501 closed"):
        snapshot(-2_501)


def test_only_anchored_requests_are_widened() -> None:
    """A snapshot set mixing plugins keeps the default window."""
    bars = testing.random_walk(3_000, seed=2)
    query, source, _ = _query(bars)
    settings = {
        "anchored_vwap": {"anchor": "time", "anchor_ms": bars[5].start_ms}
    }
    mixed = asyncio.run(
        query.snapshots(
            testing.DEFAULT_INSTRUMENT,
            TF.M1,
            ["anchored_vwap", "market_structure"],
            settings=settings,
        )
    )
    first_mixed = min(start for _, start, _ in source.requests)
    values = mixed["snapshots"][0]["values"]  # type: ignore[index]
    assert values["anchor_in_window"] is False
    alone = asyncio.run(
        query.snapshots(
            testing.DEFAULT_INSTRUMENT,
            TF.M1,
            ["anchored_vwap"],
            settings=settings,
        )
    )
    assert alone["snapshots"][0]["values"]["anchor_in_window"] is True  # type: ignore[index]
    assert min(start for _, start, _ in source.requests) < first_mixed


def test_anchored_series_continue_incrementally() -> None:
    """A repeated request feeds only new bars and equals a fresh run."""
    bars = testing.random_walk(3_003, seed=6)
    query, source, clock = _query(bars[:-3])
    settings = {"anchor": "time", "anchor_ms": bars[1].start_ms}
    first = asyncio.run(
        query.series(
            testing.DEFAULT_INSTRUMENT,
            TF.M1,
            "anchored_vwap",
            settings=settings,
        )
    )
    source.data[TF.M1] = bars
    clock.advance(bars[-1].end_ms - clock.now_ms())
    source.requests.clear()
    second = asyncio.run(
        query.series(
            testing.DEFAULT_INSTRUMENT,
            TF.M1,
            "anchored_vwap",
            settings=settings,
        )
    )
    assert [start for _, start, _ in source.requests] == [bars[-4].start_ms]
    fresh, _, _ = _query(bars)
    expected = asyncio.run(
        fresh.series(
            testing.DEFAULT_INSTRUMENT,
            TF.M1,
            "anchored_vwap",
            settings=settings,
        )
    )
    assert second == expected
    assert len(second["points"]) == len(first["points"]) + 3  # type: ignore[arg-type]


def test_revised_bar_restarts_an_anchored_run() -> None:
    """A correction of the last fed bar discards the cached run."""
    bars = testing.random_walk(2_500, seed=8)
    query, source, _ = _query(bars)
    settings = {"anchor": "time", "anchor_ms": bars[0].start_ms}
    asyncio.run(
        query.series(
            testing.DEFAULT_INSTRUMENT,
            TF.M1,
            "anchored_vwap",
            settings=settings,
        )
    )
    revised = dataclasses.replace(
        bars[-1],
        close=bars[-1].high,
        base_volume=bars[-1].base_volume * 3,
        revision=1,
    )
    source.data[TF.M1] = [*bars[:-1], revised]
    again = asyncio.run(
        query.series(
            testing.DEFAULT_INSTRUMENT,
            TF.M1,
            "anchored_vwap",
            settings=settings,
        )
    )
    fresh, _, _ = _query([*bars[:-1], revised])
    expected = asyncio.run(
        fresh.series(
            testing.DEFAULT_INSTRUMENT,
            TF.M1,
            "anchored_vwap",
            settings=settings,
        )
    )
    assert again == expected


def test_anchored_cache_respects_its_point_budget() -> None:
    """Runs over the budget are dropped; results stay correct."""
    bars = testing.random_walk(2_200, seed=4)
    query, source, _ = _query(
        bars, service.QueryConfig(anchored_cache_points=100)
    )
    settings = {"anchor": "time", "anchor_ms": bars[0].start_ms}
    for _ in range(2):
        source.requests.clear()
        series = asyncio.run(
            query.series(
                testing.DEFAULT_INSTRUMENT,
                TF.M1,
                "anchored_vwap",
                settings=settings,
            )
        )
        assert len(series["points"]) == 2_200  # type: ignore[arg-type]
        # Recomputed from the anchor each time (nothing cached).
        assert min(start for _, start, _ in source.requests) <= bars[0].start_ms


def test_anchor_time_detection() -> None:
    """Only time-anchored studies count as anchored."""
    registry = catalog.build_default_registry()
    vwap = registry.get("anchored_vwap")
    assert service.anchor_time_ms(vwap, {"anchor": "time", "anchor_ms": 5}) == 5
    assert service.anchor_time_ms(vwap, {"anchor_ms": 5}) is None
    regression = registry.get("regression_channel")
    assert service.anchor_time_ms(regression, {"anchor_ms": 7}) == 7
    assert service.anchor_time_ms(regression, {}) is None
    assert service.anchor_time_ms(registry.get("fvg_ifvg"), {}) is None


def test_far_and_future_anchors_on_exchange_calendars() -> None:
    """Anchors before XNYS are refused; a future anchor is not widened."""
    bars = testing.random_walk(
        900, seed=3, instrument_id=ACME, start_ms=testing.STOCK_START_MS
    )
    query, _, _ = _query(bars)
    with pytest.raises(errors.ValidationFailedError, match="XNYS has no"):
        asyncio.run(
            query.series(
                ACME,
                TF.M1,
                "regression_channel",
                settings={"anchor_ms": 1},
            )
        )
    with pytest.raises(errors.ValidationFailedError, match="mock:stock:ACME"):
        asyncio.run(query.series(ACME, TF.M1, "anchored_vwap", from_ms=1))
    future = asyncio.run(
        query.series(
            ACME,
            TF.M1,
            "anchored_vwap",
            settings={"anchor": "time", "anchor_ms": bars[-1].end_ms + 1},
        )
    )
    points = future["points"]
    assert isinstance(points, list) and len(points) == 400
    assert all(p["values"]["vwap"] is None for p in points)


def test_evicted_runs_release_their_locks() -> None:
    """A small cache keeps neither run nor lock of evicted anchors."""
    bars = testing.random_walk(600, seed=12)
    query, _, _ = _query(bars, service.QueryConfig(anchored_cache_points=1))
    for anchor in (bars[0].start_ms, bars[1].start_ms):
        asyncio.run(
            query.series(
                testing.DEFAULT_INSTRUMENT,
                TF.M1,
                "anchored_vwap",
                settings={"anchor": "time", "anchor_ms": anchor},
            )
        )
    assert len(query._locks) == 1
    assert not query._runs


def test_anchored_studies_build_on_a_24x7_calendar_by_default() -> None:
    """The calendar-free ``_build`` hook keeps UTC anchors."""
    vwap = packaged.AnchoredVwapPlugin()
    settings = vwap.resolve_settings({"anchor": "time", "anchor_ms": 90_000})
    built = vwap._build(settings, decimal.Decimal("0.01"), TF.M1)
    assert built["main"].anchor_time_ms == 60_000  # type: ignore[attr-defined]
    regression = packaged.RegressionChannelPlugin()
    settings = regression.resolve_settings({"anchor_ms": 90_000})
    built = regression._build(settings, decimal.Decimal("0.01"), TF.M1)
    assert built["main"].anchor_time_ms == 60_000  # type: ignore[attr-defined]
