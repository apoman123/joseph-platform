# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Canonical market worlds for generic strategies.

``ZONE_LONG`` is a generic AOI-anchored long rule over the declared-only
test plugins of ``testing.vocabulary``; ``zone_long_frames`` builds its
world. The three example templates of the contract ``strategy`` fixture
(``fixtures.EMA_TREND_LONG``, ``RSI_REVERSION_SHORT`` and
``PIVOT_BOUNCE_LONG``) get worlds built from the built-in plugin ids
(``ema``, ``rsi``, ``volume_delta``, ``bollinger_bands`` and
``pivot_points``).

Each builder returns frames in which every condition of its rule holds on
``entry_index`` only; keyword switches remove individual conditions so
tests can show that each one is necessary.
"""

from __future__ import annotations

import copy
import dataclasses
from collections.abc import Sequence

from joseph.domain import quality
from joseph.domain import structures
from joseph.domain import timeframes
from joseph.strategy.domain import ast
from joseph.strategy.testing import frames
from joseph.strategy.testing import vocabulary

_TF = timeframes.Timeframe

ZONE_LONG_ID = "zone-touch-long"
"""Strategy id of ``ZONE_LONG``."""

ZONE_LONG: dict[str, object] = {
    "schema_version": 2,
    "strategy_id": ZONE_LONG_ID,
    "name": "Zone touch (long): 4h trend up, 1h zone, 5m zone touch, signals",
    "direction": "LONG",
    "signal_timeframe": "5m",
    "universe": {
        "qualification": "QUALIFIED_ONLY",
        "venues": ["binance", "hyperliquid"],
    },
    "when": {
        "op": "ALL",
        "id": "root",
        "children": [
            {
                "op": "STATE",
                "id": "trend",
                "plugin_id": vocabulary.TEST_STATE,
                "timeframes": ["4h"],
                "where": [{"field": "trend", "any_of": ["BULLISH"]}],
            },
            {
                "op": "IN_ZONE",
                "id": "zone",
                "kinds": [vocabulary.ZONE_UP],
                "side": "bullish",
                "tf": "1h",
                "group": "A",
            },
            {
                "op": "IN_ZONE",
                "id": "core",
                "kinds": [vocabulary.ZONE_UP],
                "tf": "1h",
                "price_source": "low",
                "group": "A",
            },
            {
                "op": "AOI_TOUCH",
                "id": "touch",
                "kinds": [vocabulary.ZONE_UP],
                "side": "bullish",
                "within_bars": 3,
                "touch": "WICK",
            },
            {
                "op": "SEQUENCE",
                "id": "seq",
                "within_bars": 4,
                "steps": [
                    {"name": vocabulary.SIGNAL_DOWN},
                    {"name": vocabulary.SIGNAL_UP},
                ],
            },
            {"op": "RISK_REWARD", "id": "rr", "min_rr": "2.0"},
        ],
    },
    "risk": {
        "min_rr": "2.0",
        "stop_model": "AOI_INVALIDATION",
        "target_model": "NEAREST_AOI",
        "target_kinds": [vocabulary.LEVEL_HIGH],
        "fee_bps_estimate": "5",
    },
    "notification": {
        "cooldown_bars": 12,
        "once_per_aoi": True,
        "emit": ["ENTRY_CONFIRMED", "ENTRY_BLOCKED"],
        "channels": ["UI"],
    },
    "setup": {"expire_after_bars": 24},
    "data_policy": {"max_data_age_ms": 120000, "allow_rest_poll": True},
    "execution_mode": "PAPER",
}
"""A generic AOI-anchored long rule over the test plugins.

Conditions: ``test_state.trend`` is ``BULLISH`` on 4h; the close is inside
a bullish 1h ``ZONE_UP`` and the bar low inside a 1h ``ZONE_UP`` of the
same group (group ``A``); a bullish 5m ``ZONE_UP`` was touched within 3
bars; ``SIGNAL_DOWN`` then ``SIGNAL_UP`` within 4 bars; net R:R >= 2 with
an AOI invalidation stop and the nearest ``LEVEL_HIGH`` as the target.
"""


def zone_long_payload(**changes: object) -> dict[str, object]:
    """Returns a deep copy of ``ZONE_LONG`` with top-level changes.

    Args:
        **changes: Top-level fields to replace.

    Returns:
        The payload.
    """
    payload = copy.deepcopy(ZONE_LONG)
    payload.update(changes)
    return payload


def zone_long_definition(**changes: object) -> ast.StrategyDefinition:
    """Parses ``ZONE_LONG`` with top-level changes.

    Args:
        **changes: Top-level fields to replace.

    Returns:
        The definition.
    """
    return ast.parse_definition(zone_long_payload(**changes))


@dataclasses.dataclass(frozen=True, slots=True)
class WorldOptions:
    """Switches for the ``ZONE_LONG`` world.

    Attributes:
        bars: Number of frames.
        entry_index: Bar on which all conditions hold.
        trend: Trend published by ``test_state`` on 4h.
        in_zone: Whether the entry close is inside the 1h zone.
        touch: Whether the entry bar touches the 5m zone.
        first_signal: Whether ``SIGNAL_DOWN`` precedes the entry bar.
        final_signal: Whether ``SIGNAL_UP`` occurs on the entry bar.
        target: Whether a ``LEVEL_HIGH`` exists above for the target.
        entry_feed: Provenance of the entry bar.
        aoi_state: State of the anchoring 5m zone.
    """

    bars: int = 40
    entry_index: int = 30
    trend: str = "BULLISH"
    in_zone: bool = True
    touch: bool = True
    first_signal: bool = True
    final_signal: bool = True
    target: bool = True
    entry_feed: quality.FeedState = quality.FeedState.LIVE
    aoi_state: structures.AoiState = structures.AoiState.ACTIVE


def htf_as_of(
    factory: frames.FrameFactory, index: int, tf: timeframes.Timeframe
) -> int:
    """Returns the close of the latest ``tf`` bar visible at a frame.

    Args:
        factory: Frame factory.
        index: Signal bar index.
        tf: Higher timeframe.

    Returns:
        As-of time (UTC ms), on the instrument's session calendar.
    """
    close = factory.close(index)
    start = factory.calendar.last_closed_start(tf, close)
    if start is None:
        return factory.calendar.floor(tf, close)
    return factory.calendar.end_of(tf, start)


def _zone_entry_bar(opts: WorldOptions) -> frames.BarSpec:
    """Returns the ``ZONE_LONG`` entry bar for the touch/zone switches.

    Args:
        opts: World options.

    Returns:
        Bar specification.
    """
    table = {
        (True, True): ("95", "95.5", "93.5", "94.5"),
        (True, False): ("95", "101.5", "93.5", "101"),
        (False, True): ("95", "95.5", "94.2", "94.5"),
        (False, False): ("101", "101.5", "100.5", "101"),
    }
    open_, high, low, close = table[(opts.touch, opts.in_zone)]
    return frames.bar(open_, high, low, close)


def _zone_snapshots(
    factory: frames.FrameFactory,
    index: int,
    opts: WorldOptions,
    touch_zone: structures.Aoi,
) -> list[structures.IndicatorSnapshot]:
    """Builds the snapshots of one ``ZONE_LONG`` frame.

    Args:
        factory: Frame factory.
        index: Signal bar index.
        opts: World options.
        touch_zone: The 5m zone the entry bar touches.

    Returns:
        Snapshots of ``test_state`` (4h), ``test_zones`` (1h and 5m),
        ``test_levels`` (5m) and ``volume_delta`` (5m).
    """
    group = "grp-1h-1"
    wide = factory.aoi(
        vocabulary.ZONE_UP,
        "bullish",
        "80",
        "100",
        confirmed_index=0,
        tf=_TF.H1,
        aoi_id="zone-1h-wide",
        group_id=group,
        quality_score=None,
    )
    core = factory.aoi(
        vocabulary.ZONE_UP,
        "neutral",
        "88.4",
        "95.2",
        confirmed_index=0,
        tf=_TF.H1,
        aoi_id="zone-1h-core",
        group_id=group,
        quality_score=None,
    )
    levels: list[structures.Aoi] = []
    if opts.target:
        levels = [
            factory.aoi(
                vocabulary.LEVEL_HIGH,
                "neutral",
                "110",
                "110",
                confirmed_index=2,
                aoi_id="level-high-110",
                quality_score=None,
            ),
            factory.aoi(
                vocabulary.LEVEL_LOW,
                "neutral",
                "90",
                "90",
                confirmed_index=2,
                aoi_id="level-low-90",
                quality_score=None,
            ),
        ]
    return [
        factory.snapshot(
            vocabulary.TEST_STATE,
            _TF.H4,
            index,
            as_of_ms=htf_as_of(factory, index, _TF.H4),
            values={"trend": opts.trend, "armed": True},
        ),
        factory.snapshot(
            vocabulary.TEST_ZONES,
            _TF.H1,
            index,
            as_of_ms=htf_as_of(factory, index, _TF.H1),
            aois=[wide, core],
            values={"active_count": 2},
        ),
        factory.snapshot(
            vocabulary.TEST_ZONES,
            _TF.M5,
            index,
            aois=[touch_zone] if index >= 10 else [],
            values={"active_count": 1 if index >= 10 else 0},
        ),
        factory.snapshot(
            vocabulary.TEST_LEVELS,
            _TF.M5,
            index,
            aois=levels,
            values={"active_count": len(levels)},
        ),
        factory.snapshot(
            "volume_delta",
            _TF.M5,
            index,
            values={"delta_pct": frames.dec("-25"), "method": "taker_volume"},
        ),
    ]


def zone_long_frames(
    options: WorldOptions | None = None,
    instrument_id: str = "binance:perp:BTCUSDT",
) -> list[structures.BarFrame]:
    """Builds the ``ZONE_LONG`` world.

    The 5m zone ``zone-5m-long`` (92-94, confirmed at bar 10) anchors the
    setup; the 1h zones ``zone-1h-wide`` (80-100) and ``zone-1h-core``
    (88.4-95.2) share group ``grp-1h-1``; ``LEVEL_HIGH`` at 110 is the
    target and ``LEVEL_LOW`` at 90 sits below.

    Args:
        options: Condition switches.
        instrument_id: Instrument of the frames.

    Returns:
        Frames, oldest first.
    """
    opts = WorldOptions() if options is None else options
    factory = frames.FrameFactory(instrument_id=instrument_id)
    touch_zone = factory.aoi(
        vocabulary.ZONE_UP,
        "bullish",
        "92",
        "94",
        confirmed_index=10,
        aoi_id="zone-5m-long",
        quality_score=76,
        state=opts.aoi_state,
    )
    result = []
    for index in range(opts.bars):
        entry = index == opts.entry_index
        spec = (
            _zone_entry_bar(opts) if entry else frames.bar(104, 105, 103, 104)
        )
        events = []
        if opts.first_signal and index == opts.entry_index - 2:
            events.append(
                factory.event(vocabulary.SIGNAL_DOWN, index, price="90")
            )
        if opts.final_signal and entry:
            events.append(factory.event(vocabulary.SIGNAL_UP, index))
        feed = opts.entry_feed if entry else quality.FeedState.LIVE
        result.append(
            factory.frame(
                index,
                spec,
                _zone_snapshots(factory, index, opts, touch_zone),
                events,
                feed_state=feed,
            )
        )
    return result


@dataclasses.dataclass(frozen=True, slots=True)
class EmaTrendOptions:
    """Switches for the ``ema-trend-long`` template world (15m).

    Attributes:
        bars: Number of frames.
        entry_index: Bar on which all conditions hold.
        cross: Whether the entry close crosses above the EMA.
        rsi: 1h RSI published (the rule needs more than 50).
        delta_pct: Volume delta published (the rule needs more than 0).
    """

    bars: int = 40
    entry_index: int = 30
    cross: bool = True
    rsi: str = "56"
    delta_pct: str = "12.5"


def ema_trend_frames(
    options: EmaTrendOptions | None = None,
    instrument_id: str = "binance:perp:BTCUSDT",
) -> list[structures.BarFrame]:
    """Builds the world of the ``ema-trend-long`` template.

    Closes sit at 100 under an EMA of 101 until the entry bar closes at 103
    over an EMA of 101.5; bars range 2 so ATR(14) is 2, the ATR stop sits
    3 below the entry and the 3R target 9 above it.

    Args:
        options: Condition switches.
        instrument_id: Instrument of the frames.

    Returns:
        Frames, oldest first.
    """
    opts = EmaTrendOptions() if options is None else options
    factory = frames.FrameFactory(
        instrument_id=instrument_id, timeframe=_TF.M15
    )
    result = []
    for index in range(opts.bars):
        entry = index == opts.entry_index
        spec = frames.bar(100, 101, 99, 100)
        ema = "101"
        if entry:
            spec = (
                frames.bar(100, 104, 100, 103)
                if opts.cross
                else frames.bar(100, 101, 99, "100.5")
            )
            ema = "101.5"
        snaps = [
            factory.snapshot(
                "ema", _TF.M15, index, values={"ema": frames.dec(ema)}
            ),
            factory.snapshot(
                "rsi",
                _TF.H1,
                index,
                as_of_ms=htf_as_of(factory, index, _TF.H1),
                values={"rsi": frames.dec(opts.rsi)},
            ),
            factory.snapshot(
                "volume_delta",
                _TF.M15,
                index,
                values={
                    "delta_pct": frames.dec(opts.delta_pct if entry else "-3"),
                    "method": "taker_volume",
                },
            ),
        ]
        result.append(factory.frame(index, spec, snaps))
    return result


@dataclasses.dataclass(frozen=True, slots=True)
class RsiReversionOptions:
    """Switches for the ``rsi-reversion-short`` template world (5m).

    Attributes:
        bars: Number of frames.
        entry_index: Bar on which all conditions hold.
        cross: Whether RSI falls back below 70 on the entry bar.
        band_break: Whether a close above the upper band precedes it.
    """

    bars: int = 40
    entry_index: int = 30
    cross: bool = True
    band_break: bool = True


def rsi_reversion_frames(
    options: RsiReversionOptions | None = None,
    instrument_id: str = "binance:perp:BTCUSDT",
) -> list[structures.BarFrame]:
    """Builds the world of the ``rsi-reversion-short`` template.

    RSI is 60 until it reads 74 on the bar before the entry and 68 from
    the entry bar on (72 without the cross); the bar two bars before the
    entry closes at 108 over an upper band of 107. The open-interest veto
    reads market context, which tests pass to the evaluator separately.

    Args:
        options: Condition switches.
        instrument_id: Instrument of the frames.

    Returns:
        Frames, oldest first.
    """
    opts = RsiReversionOptions() if options is None else options
    factory = frames.FrameFactory(instrument_id=instrument_id)
    result = []
    for index in range(opts.bars):
        offset = index - opts.entry_index
        spec = frames.bar(100, 101, 99, 100)
        rsi = "60"
        if offset == -2 and opts.band_break:
            spec = frames.bar(104, "108.5", "103.5", 108)
        if offset == -1:
            spec = frames.bar(106, 107, 104, 105)
            rsi = "74"
        if offset == 0:
            spec = frames.bar(105, "105.5", 103, "103.5")
        if offset >= 0:
            rsi = "68" if opts.cross else "72"
        snaps = [
            factory.snapshot(
                "rsi", _TF.M5, index, values={"rsi": frames.dec(rsi)}
            ),
            factory.snapshot(
                "bollinger_bands",
                _TF.M5,
                index,
                values={
                    "basis": frames.dec("100"),
                    "upper": frames.dec("107"),
                    "lower": frames.dec("93"),
                },
            ),
        ]
        result.append(factory.frame(index, spec, snaps))
    return result


PIVOT_INSTRUMENT = "nasdaq:stock:ACME"
"""Exchange instrument of the ``pivot-bounce-long`` world (XNYS)."""


@dataclasses.dataclass(frozen=True, slots=True)
class PivotBounceOptions:
    """Switches for the ``pivot-bounce-long`` template world (5m, XNYS).

    Bar 0 opens at 09:30 New York time on Friday 2026-11-20, so bar 18
    starts at 11:00 and bar 24 at 11:30, the end of the rule's window.

    Attributes:
        bars: Number of frames.
        entry_index: Bar on which all conditions hold.
        touch: Whether the entry bar wicks into ``PIVOT_S1``.
        method: Volume delta method published (the rule accepts
            ``taker_volume`` and ``trade_classification``).
        target: Whether ``PIVOT_P`` and ``PIVOT_R1`` exist above.
        aoi_state: State of ``PIVOT_S1``.
    """

    bars: int = 30
    entry_index: int = 18
    touch: bool = True
    method: str = "trade_classification"
    target: bool = True
    aoi_state: structures.AoiState = structures.AoiState.ACTIVE


def pivot_bounce_frames(
    options: PivotBounceOptions | None = None,
    instrument_id: str = PIVOT_INSTRUMENT,
) -> list[structures.BarFrame]:
    """Builds the world of the ``pivot-bounce-long`` template.

    ``PIVOT_S1`` sits at 95, ``PIVOT_P`` at 100 and ``PIVOT_R1`` at 105,
    published by ``pivot_points`` on the signal timeframe. Bars range 1
    around 97; the entry bar wicks to 94.8 and closes at 96, so the stop is
    95 minus 0.2 ATR and the target is the pivot at 100.

    Args:
        options: Condition switches.
        instrument_id: Exchange instrument of the frames.

    Returns:
        Frames, oldest first.
    """
    opts = PivotBounceOptions() if options is None else options
    factory = frames.FrameFactory(instrument_id=instrument_id)
    levels = [
        factory.aoi(
            "PIVOT_S1",
            "bullish",
            "95",
            "95",
            confirmed_index=0,
            aoi_id="pivot-s1",
            quality_score=None,
            state=opts.aoi_state,
        )
    ]
    if opts.target:
        levels += [
            factory.aoi(
                "PIVOT_P",
                "neutral",
                "100",
                "100",
                confirmed_index=0,
                aoi_id="pivot-p",
                quality_score=None,
            ),
            factory.aoi(
                "PIVOT_R1",
                "bearish",
                "105",
                "105",
                confirmed_index=0,
                aoi_id="pivot-r1",
                quality_score=None,
            ),
        ]
    result = []
    for index in range(opts.bars):
        spec = frames.bar("97", "97.5", "96.5", "97")
        if index == opts.entry_index:
            spec = (
                frames.bar("96.5", "97", "94.8", "96")
                if opts.touch
                else frames.bar("96.5", "97", "95.5", "96")
            )
        snaps = [
            factory.snapshot("pivot_points", _TF.M5, index, aois=levels),
            factory.snapshot(
                "volume_delta",
                _TF.M5,
                index,
                values={"delta_pct": frames.dec("4"), "method": opts.method},
            ),
        ]
        result.append(factory.frame(index, spec, snaps))
    return result


def with_duplicates(
    seq: Sequence[structures.BarFrame], every: int = 3
) -> list[structures.BarFrame]:
    """Interleaves duplicate and stale (older) frames into a sequence.

    Args:
        seq: Clean frames.
        every: Insert noise after every ``every`` frames.

    Returns:
        Noisy frames that an idempotent consumer must treat like ``seq``.
    """
    noisy: list[structures.BarFrame] = []
    for index, frame in enumerate(seq):
        noisy.append(frame)
        if index % every == every - 1:
            noisy.append(frame)
            if index >= 2:
                noisy.append(seq[index - 2])
    return noisy
