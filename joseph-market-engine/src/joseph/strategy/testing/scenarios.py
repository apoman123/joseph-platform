"""Canonical market worlds for the proposal's Long A and Short B rules.

Each builder returns frames in which every condition of the rule holds on
``entry_index`` only; keyword switches remove individual conditions so
tests can show that each one is necessary.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence

from joseph.domain import quality
from joseph.domain import structures
from joseph.domain import timeframes
from joseph.strategy.testing import frames

TF = timeframes.Timeframe
K = structures.AoiKind
E = structures.StructureEventName


@dataclasses.dataclass(frozen=True, slots=True)
class WorldOptions:
    """Switches for scenario conditions.

    Attributes:
        bars: Number of frames.
        entry_index: Bar on which all conditions hold.
        bias: Trend published on the bias timeframes.
        in_zone: Whether the entry close is inside discount/premium + OTE.
        touch: Whether the entry bar touches the AOI.
        sweep: Whether the sweep event precedes the entry bar.
        failure: Whether the failure event occurs on the entry bar.
        target: Whether opposing liquidity exists for the target.
        entry_feed: Provenance of the entry bar.
        aoi_state: State of the anchoring AOI.
    """

    bars: int = 40
    entry_index: int = 30
    bias: str | None = None
    in_zone: bool = True
    touch: bool = True
    sweep: bool = True
    failure: bool = True
    target: bool = True
    entry_feed: quality.FeedState = quality.FeedState.LIVE
    aoi_state: structures.AoiState = structures.AoiState.ACTIVE


def _htf_as_of(
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


def long_a_frames(
    options: WorldOptions | None = None,
    instrument_id: str = "binance:perp:BTCUSDT",
) -> list[structures.BarFrame]:
    """Builds the Long A world.

    Long A: 4h bullish bias, 1h discount and OTE in one range, bullish FVG
    touched within 3 bars, SSL sweep then seller failure within 4 bars and
    net R:R >= 2.

    Args:
        options: Condition switches.
        instrument_id: Instrument of the frames.

    Returns:
        Frames, oldest first.
    """
    opts = options or WorldOptions()
    factory = frames.FrameFactory(instrument_id=instrument_id)
    bias = opts.bias or "BULLISH"
    fvg = factory.aoi(
        K.FVG,
        "bullish",
        "92",
        "94",
        confirmed_index=10,
        aoi_id="fvg-5m-long",
        quality_score=76,
        state=opts.aoi_state,
    )
    result = []
    for index in range(opts.bars):
        entry = index == opts.entry_index
        spec = frames.bar("104", "105", "103", "104")
        if entry:
            spec = _entry_bar(opts, long=True)
        snaps = [
            factory.snapshot(
                "daily_bias",
                TF.H4,
                index,
                as_of_ms=_htf_as_of(factory, index, TF.H4),
                values={
                    "trend": bias,
                    "last_event": "BOS",
                    "last_event_direction": bias,
                },
            ),
            _range_snapshot(factory, index, opts, bullish=True),
            factory.snapshot(
                "fvg_ifvg",
                TF.M5,
                index,
                aois=[fvg] if index >= 10 else [],
                values={"active_count": 1},
            ),
            factory.snapshot(
                "order_breaker_blocks", TF.M5, index, values={"active_count": 0}
            ),
            _liquidity_snapshot(factory, index, opts),
            factory.snapshot(
                "order_flow_pressure",
                TF.M5,
                index,
                values={"delta_pct": frames.dec("-25")},
            ),
        ]
        events = []
        if opts.sweep and index == opts.entry_index - 2:
            events.append(factory.event(E.SSL_SWEEP, index, price="90"))
        if opts.failure and entry:
            events.append(factory.event(E.SELLER_FAILURE, index))
        feed = opts.entry_feed if entry else quality.FeedState.LIVE
        result.append(
            factory.frame(index, spec, snaps, events, feed_state=feed)
        )
    return result


def short_b_frames(
    options: WorldOptions | None = None,
    instrument_id: str = "binance:perp:BTCUSDT",
) -> list[structures.BarFrame]:
    """Builds the Short B world.

    Short B: 2 of 1d/4h/1h bearish, 1h premium and OTE in one range,
    bearish FVG/breaker touched within 3 bars, BSL sweep then buyer failure
    within 4 bars, and open interest not falling more than 5%.

    Args:
        options: Condition switches.
        instrument_id: Instrument of the frames.

    Returns:
        Frames, oldest first.
    """
    opts = options or WorldOptions()
    factory = frames.FrameFactory(instrument_id=instrument_id)
    bias = opts.bias or "BEARISH"
    breaker = factory.aoi(
        K.BREAKER,
        "bearish",
        "106",
        "108",
        confirmed_index=12,
        aoi_id="brk-5m-short",
        quality_score=64,
        state=opts.aoi_state,
    )
    result = []
    for index in range(opts.bars):
        entry = index == opts.entry_index
        spec = frames.bar("96", "97", "95", "96")
        if entry:
            spec = _entry_bar(opts, long=False)
        snaps = [
            factory.snapshot(
                "daily_bias",
                tf,
                index,
                as_of_ms=_htf_as_of(factory, index, tf),
                values={
                    "trend": bias if tf is not TF.H1 else "BULLISH",
                    "last_event": "BOS",
                    "last_event_direction": bias,
                },
            )
            for tf in (TF.D1, TF.H4, TF.H1)
        ]
        snaps += [
            _range_snapshot(factory, index, opts, bullish=False),
            factory.snapshot(
                "fvg_ifvg", TF.M5, index, values={"active_count": 0}
            ),
            factory.snapshot(
                "order_breaker_blocks",
                TF.M5,
                index,
                aois=[breaker] if index >= 12 else [],
                values={"active_count": 1},
            ),
            _liquidity_snapshot(factory, index, opts),
            factory.snapshot(
                "order_flow_pressure",
                TF.M5,
                index,
                values={"delta_pct": frames.dec("30")},
            ),
        ]
        events = []
        if opts.sweep and index == opts.entry_index - 2:
            events.append(factory.event(E.BSL_SWEEP, index, price="110"))
        if opts.failure and entry:
            events.append(factory.event(E.BUYER_FAILURE, index))
        feed = opts.entry_feed if entry else quality.FeedState.LIVE
        result.append(
            factory.frame(index, spec, snaps, events, feed_state=feed)
        )
    return result


def _entry_bar(opts: WorldOptions, *, long: bool) -> frames.BarSpec:
    """Returns the entry bar for the touch/zone switches.

    Args:
        opts: Scenario options.
        long: Long A (True) or Short B (False).

    Returns:
        Bar specification.
    """
    if long:
        table = {
            (True, True): ("95", "95.5", "93.5", "94.5"),
            (True, False): ("95", "101.5", "93.5", "101"),
            (False, True): ("95", "95.5", "94.2", "94.5"),
            (False, False): ("101", "101.5", "100.5", "101"),
        }
    else:
        table = {
            (True, True): ("106.5", "107.8", "106.2", "107"),
            (True, False): ("107", "112.5", "107", "112"),
            (False, True): ("105.2", "105.6", "104.9", "105"),
            (False, False): ("99", "99.5", "98.5", "99"),
        }
    o, h, low, c = table[(opts.touch, opts.in_zone)]
    return frames.bar(o, h, low, c)


def _range_snapshot(
    factory: frames.FrameFactory, index: int, opts: WorldOptions, bullish: bool
) -> structures.IndicatorSnapshot:
    """Builds the 1h premium/discount/OTE snapshot.

    Args:
        factory: Frame factory.
        index: Signal bar index.
        opts: Scenario options.
        bullish: Leg direction (bullish OTE sits in discount).

    Returns:
        The snapshot.
    """
    range_id = "rng-1h-1"
    ote = ("88.4", "95.2") if bullish else ("104.8", "111.6")
    side = "bullish" if bullish else "bearish"
    aois = [
        factory.aoi(
            K.DISCOUNT,
            "bullish",
            "80",
            "100",
            confirmed_index=0,
            tf=TF.H1,
            aoi_id="disc-1",
            range_id=range_id,
            quality_score=None,
        ),
        factory.aoi(
            K.PREMIUM,
            "bearish",
            "100",
            "120",
            confirmed_index=0,
            tf=TF.H1,
            aoi_id="prem-1",
            range_id=range_id,
            quality_score=None,
        ),
        factory.aoi(
            K.OTE,
            side,
            ote[0],
            ote[1],
            confirmed_index=0,
            tf=TF.H1,
            aoi_id="ote-1",
            range_id=range_id,
            quality_score=None,
        ),
    ]
    return factory.snapshot(
        "premium_discount_ote",
        TF.H1,
        index,
        as_of_ms=_htf_as_of(factory, index, TF.H1),
        aois=aois,
        values={
            "range_id": range_id,
            "direction": "BULLISH" if bullish else "BEARISH",
            "range_high": frames.dec("120"),
            "range_low": frames.dec("80"),
            "eq": frames.dec("100"),
            "ote_lower": frames.dec(ote[0]),
            "ote_upper": frames.dec(ote[1]),
        },
    )


def _liquidity_snapshot(
    factory: frames.FrameFactory, index: int, opts: WorldOptions
) -> structures.IndicatorSnapshot:
    """Builds the 5m liquidity snapshot with BSL above and SSL below.

    Args:
        factory: Frame factory.
        index: Signal bar index.
        opts: Scenario options.

    Returns:
        The snapshot.
    """
    pools: list[structures.Aoi] = []
    if opts.target:
        pools = [
            factory.aoi(
                K.BSL,
                "neutral",
                "110",
                "110",
                confirmed_index=2,
                aoi_id="bsl-110",
                quality_score=None,
            ),
            factory.aoi(
                K.SSL,
                "neutral",
                "90",
                "90",
                confirmed_index=2,
                aoi_id="ssl-90",
                quality_score=None,
            ),
        ]
    return factory.snapshot(
        "liquidity_sweeps",
        TF.M5,
        index,
        aois=pools,
        values={"active_count": len(pools)},
    )


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
