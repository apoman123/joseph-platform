# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Deterministic candles and test plugins for indicator and strategy tests.

Shipped with the package (no runtime cost) so every bounded context, and
every plugin package, can build consistent multi-timeframe fixtures:
lower-timeframe series are generated first and higher timeframes are
aggregated from them, exactly as a venue would. ``EchoPlugin`` and
``ZoneEchoPlugin`` are predictable plugins whose values, events and zones
follow the bars directly.

Every builder follows the instrument's session calendar: pass an exchange
instrument (``STOCK_INSTRUMENT`` is ``mock:stock:ACME`` on XNYS) and bars
start at the session open, the last bar of a session is cut at the close,
weekends and holidays are skipped, and ``aggregate`` builds session daily
and weekly bars.
"""

from __future__ import annotations

import dataclasses
import decimal
import random
from collections.abc import Iterable
from collections.abc import Mapping
from collections.abc import Sequence
from typing import TypeAlias

from joseph.domain import decimals
from joseph.domain import ids
from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import quality
from joseph.domain import sessions
from joseph.domain import structures
from joseph.domain import timeframes
from joseph.indicators.sdk import base
from joseph.indicators.sdk import metadata
from joseph.indicators.sdk import protocol
from joseph.indicators.sdk import settings as settings_lib

Number: TypeAlias = str | int | decimal.Decimal
"""Accepted numeric input for builders."""

DEFAULT_INSTRUMENT = instruments.InstrumentId.parse("binance:perp:BTCUSDT")
"""24x7 instrument used when a builder is given none."""

DEFAULT_START_MS = 1_794_787_200_000
"""2026-11-16 00:00:00 UTC (a Monday), aligned for every timeframe."""

STOCK_INSTRUMENT = instruments.InstrumentId.parse("mock:stock:ACME")
"""DEMO stock on the XNYS calendar (09:30-16:00 America/New_York)."""

STOCK_START_MS = 1_794_839_400_000
"""2026-11-16 09:30 America/New_York (14:30 UTC), a Monday session open."""


def bar_starts(
    timeframe: timeframes.Timeframe,
    start_ms: int,
    count: int,
    instrument_id: instruments.InstrumentId = DEFAULT_INSTRUMENT,
) -> list[int]:
    """Returns consecutive bar starts on the instrument's calendar.

    Args:
        timeframe: Bar resolution.
        start_ms: First bar start (a later time counts from the next bar).
        count: Number of bars.
        instrument_id: Instrument.

    Returns:
        Ascending bar starts.
    """
    calendar = sessions.for_instrument(instrument_id)
    starts: list[int] = []
    current = calendar.shift(timeframe, start_ms, 0)
    for _ in range(count):
        starts.append(current)
        current = calendar.shift(timeframe, current, 1)
    return starts


def make_candle(  # noqa: PLR0917 - OHLCV reads naturally positionally.
    timeframe: timeframes.Timeframe,
    start_ms: int,
    open_: Number,
    high: Number,
    low: Number,
    close: Number,
    volume: Number = "10",
    *,
    instrument_id: instruments.InstrumentId = DEFAULT_INSTRUMENT,
    feed_state: quality.FeedState = quality.FeedState.LIVE,
    is_final: bool = True,
    revision: int = 0,
) -> market.Candle:
    """Builds one candle.

    Args:
        timeframe: Bar resolution.
        start_ms: Aligned bar start.
        open_: Open price.
        high: High price.
        low: Low price.
        close: Close price.
        volume: Base volume.
        instrument_id: Instrument.
        feed_state: Provenance.
        is_final: Whether the bar is closed.
        revision: Correction counter.

    Returns:
        The candle.
    """
    end_ms = sessions.for_instrument(instrument_id).end_of(timeframe, start_ms)
    return market.Candle(
        instrument_id=instrument_id,
        timeframe=timeframe,
        start_ms=start_ms,
        end_ms=end_ms,
        open=decimals.to_decimal(open_),
        high=decimals.to_decimal(high),
        low=decimals.to_decimal(low),
        close=decimals.to_decimal(close),
        base_volume=decimals.to_decimal(volume),
        is_final=is_final,
        source="test",
        feed_state=feed_state,
        received_at_ms=end_ms,
        revision=revision,
    )


def candles_from_rows(
    rows: Iterable[Sequence[Number]],
    timeframe: timeframes.Timeframe = timeframes.Timeframe.M5,
    start_ms: int = DEFAULT_START_MS,
    *,
    instrument_id: instruments.InstrumentId = DEFAULT_INSTRUMENT,
) -> list[market.Candle]:
    """Builds consecutive candles from ``(open, high, low, close[, vol])``.

    Args:
        rows: OHLC(V) rows, oldest first.
        timeframe: Bar resolution.
        start_ms: Start of the first bar.
        instrument_id: Instrument.

    Returns:
        Candles.
    """
    rows = list(rows)
    starts = bar_starts(timeframe, start_ms, len(rows), instrument_id)
    candles = []
    for index, row in enumerate(rows):
        open_, high, low, close = row[:4]
        volume = row[4] if len(row) > 4 else "10"
        candles.append(
            make_candle(
                timeframe,
                starts[index],
                open_,
                high,
                low,
                close,
                volume,
                instrument_id=instrument_id,
            )
        )
    return candles


def random_walk(
    count: int,
    *,
    seed: int,
    timeframe: timeframes.Timeframe = timeframes.Timeframe.M1,
    start_ms: int = DEFAULT_START_MS,
    base: Number = "100",
    instrument_id: instruments.InstrumentId = DEFAULT_INSTRUMENT,
) -> list[market.Candle]:
    """Generates a deterministic random-walk series.

    Prices move in steps of 0.01 with occasional large moves, so pivots,
    gaps and spikes occur naturally.

    Args:
        count: Number of bars.
        seed: Random seed.
        timeframe: Bar resolution.
        start_ms: Start of the first bar.
        base: Starting price.
        instrument_id: Instrument.

    Returns:
        Candles, oldest first.
    """
    rng = random.Random(seed)  # noqa: S311 - deterministic test data.
    tick = decimal.Decimal("0.01")
    price = decimals.to_decimal(base)
    starts = bar_starts(timeframe, start_ms, count, instrument_id)
    candles = []
    for index in range(count):
        open_ = price
        move = rng.choice((1, 2, 3, 5, 8, 13)) * rng.choice((-1, 1))
        if rng.random() < 0.05:
            move *= 6
        close = max(tick, open_ + tick * move)
        high = max(open_, close) + tick * rng.randint(0, 6)
        low = max(tick / 2, min(open_, close) - tick * rng.randint(0, 6))
        volume = decimal.Decimal(rng.randint(1, 50))
        if rng.random() < 0.1:
            volume *= 4
        candles.append(
            make_candle(
                timeframe,
                starts[index],
                open_,
                high,
                low,
                close,
                volume,
                instrument_id=instrument_id,
            )
        )
        price = close
    return candles


def aggregate(
    candles: Sequence[market.Candle], timeframe: timeframes.Timeframe
) -> list[market.Candle]:
    """Aggregates lower-timeframe candles into complete higher bars.

    Only buckets fully covered by the input are emitted, mirroring how a
    venue publishes closed bars.

    Args:
        candles: Contiguous lower-timeframe candles, oldest first.
        timeframe: Target timeframe (must contain the source timeframe).

    Returns:
        Aggregated final candles.

    Raises:
        ValueError: If the timeframes do not nest.
    """
    if not candles:
        return []
    source_tf = candles[0].timeframe
    calendar = sessions.for_instrument(candles[0].instrument_id)
    if not calendar.contains(timeframe, source_tf) and (
        timeframe is not source_tf
    ):
        raise ValueError(f"{source_tf} does not nest in {timeframe}")
    buckets: dict[int, list[market.Candle]] = {}
    for candle in candles:
        buckets.setdefault(
            calendar.floor(timeframe, candle.start_ms), []
        ).append(candle)
    result = []
    for start, members in sorted(buckets.items()):
        end = calendar.end_of(timeframe, start)
        per_bucket = calendar.bars_between(source_tf, start, end)
        if len(members) != per_bucket:
            continue
        result.append(
            market.Candle(
                instrument_id=members[0].instrument_id,
                timeframe=timeframe,
                start_ms=start,
                end_ms=end,
                open=members[0].open,
                high=max(c.high for c in members),
                low=min(c.low for c in members),
                close=members[-1].close,
                base_volume=sum(
                    (c.base_volume for c in members), decimals.ZERO
                ),
                is_final=True,
                source="test.aggregate",
                feed_state=members[-1].feed_state,
                received_at_ms=end,
                taker_buy_base_volume=_sum_taker(members),
                taker_buy_method=members[-1].taker_buy_method,
            )
        )
    return result


def _sum_taker(members: Sequence[market.Candle]) -> decimal.Decimal | None:
    """Sums the members' buy volume when every member carries one.

    Args:
        members: Candles of one bucket.

    Returns:
        Total buy volume, or ``None`` when any member has none.
    """
    if any(c.taker_buy_base_volume is None for c in members):
        return None
    return sum(
        (c.taker_buy_base_volume or decimals.ZERO for c in members),
        decimals.ZERO,
    )


def run_bars(
    plugin: protocol.IndicatorPlugin,
    candles: Sequence[market.Candle],
    settings: Mapping[str, object] | None = None,
    *,
    intrabars: Mapping[
        int, Mapping[timeframes.Timeframe, tuple[market.Candle, ...]]
    ]
    | None = None,
    aux: Mapping[int, Mapping[timeframes.Timeframe, tuple[market.Candle, ...]]]
    | None = None,
) -> list[tuple[structures.IndicatorSnapshot, protocol.PluginOutput]]:
    """Runs one plugin bar by bar and records snapshots and outputs.

    Args:
        plugin: Plugin under test.
        candles: Closed series bars, oldest first.
        settings: Setting overrides.
        intrabars: Optional intrabars per bar index.
        aux: Optional aux bars per bar index.

    Returns:
        ``(snapshot, output)`` after each bar.
    """
    if not candles:
        return []
    state = plugin.initialize(
        candles[0].instrument_id, candles[0].timeframe, settings
    )
    results = []
    for index, candle in enumerate(candles):
        bar = protocol.BarInput(
            candle=candle,
            intrabars=dict((intrabars or {}).get(index, {})),
            aux=dict((aux or {}).get(index, {})),
        )
        state, output = plugin.on_bar_closed(state, bar)
        results.append((plugin.snapshot(state), output))
    return results


def event_names(
    results: Sequence[
        tuple[structures.IndicatorSnapshot, protocol.PluginOutput]
    ],
) -> list[tuple[int, str]]:
    """Flattens ``run_bars`` results into ``(bar_index, event_name)``.

    Args:
        results: Output of ``run_bars``.

    Returns:
        Event occurrences in order.
    """
    names: list[tuple[int, str]] = []
    for index, (_, output) in enumerate(results):
        names.extend((index, event.name) for event in output.events)
    return names


# Test plugins ---------------------------------------------------------------

ECHO_UP = "ECHO_UP"
"""``EchoPlugin`` event: the close rose above the previous close."""

ECHO_DOWN = "ECHO_DOWN"
"""``EchoPlugin`` event: the close fell below the previous close."""

ECHO_ZONE = "ECHO_ZONE"
"""Default AOI kind of ``ZoneEchoPlugin``."""

AGED_OUT = "aged_out"
"""End reason of ``ZoneEchoPlugin`` zones that reached their age limit."""


@dataclasses.dataclass(frozen=True, slots=True)
class EchoState:
    """State of an ``EchoPlugin`` series.

    Attributes:
        header: Series bookkeeping.
        close: Close of the last bar (``None`` before the first).
        aux_close: Close of the latest completed aux bar, if any.
    """

    header: protocol.SeriesHeader
    close: decimal.Decimal | None = None
    aux_close: decimal.Decimal | None = None


class EchoPlugin(base.BasePlugin[EchoState]):
    """Test plugin echoing each bar's close as a value and as events.

    Publishes ``close`` (the bar's close, exactly), ``bars`` (bars
    processed) and ``aux_close`` (close of the latest completed bar of
    ``aux_timeframe``, null without one); emits ``ECHO_UP`` / ``ECHO_DOWN``
    when the close rises above / falls below the previous close. Strategy
    and plugin-package tests use it as a predictable value and event source;
    several instances with different ids may share the event names.

    Attributes:
        PLUGIN_ID: Default id, ``"echo"``.
        DISPLAY_NAME: Human-readable name.
        DESCRIPTION: One-sentence description.
        OUTPUT_EVENTS: ``ECHO_UP`` and ``ECHO_DOWN``.
        OUTPUT_VALUES: ``close``, ``bars`` and ``aux_close``.
        DISPLAY: Hidden layer with a close line and event markers.
        STATE_TYPES: The state dataclass.
        aux_timeframe: Timeframe whose completed bars feed ``aux_close``.
    """

    PLUGIN_ID = "echo"
    DISPLAY_NAME = "Echo"
    DESCRIPTION = "Test plugin publishing each bar's close and its direction."
    OUTPUT_EVENTS = (ECHO_UP, ECHO_DOWN)
    OUTPUT_VALUES = (
        metadata.ValueSpec(
            "close",
            metadata.ValueType.DECIMAL,
            "Close of the bar.",
            metadata.Unit.PRICE,
        ),
        metadata.ValueSpec(
            "bars",
            metadata.ValueType.INTEGER,
            "Bars processed.",
            metadata.Unit.BARS,
        ),
        metadata.ValueSpec(
            "aux_close",
            metadata.ValueType.DECIMAL,
            "Close of the latest completed aux bar.",
            metadata.Unit.PRICE,
        ),
    )
    DISPLAY = metadata.Display(
        plots=(metadata.PlotStyle("close", "Close"),),
        events=(
            metadata.EventStyle(
                ECHO_UP,
                "Up",
                position="below",
                shape="arrow-up",
                tone=metadata.Tone.UP,
            ),
            metadata.EventStyle(
                ECHO_DOWN,
                "Down",
                shape="arrow-down",
                tone=metadata.Tone.DOWN,
            ),
        ),
    )
    STATE_TYPES = (EchoState,)

    def __init__(
        self,
        plugin_id: str = PLUGIN_ID,
        aux_timeframe: timeframes.Timeframe | None = None,
    ) -> None:
        """Creates the plugin.

        Args:
            plugin_id: Registry id (lower snake case).
            aux_timeframe: Timeframe of the aux stream read for
                ``aux_close``; ``None`` reads none.
        """
        self._plugin_id = plugin_id
        self.aux_timeframe = aux_timeframe

    @property
    def plugin_id(self) -> str:
        """The registry id given at construction."""
        return self._plugin_id

    def required_streams(
        self,
        settings: Mapping[str, object] | None,
        *,
        timeframe: timeframes.Timeframe | None = None,
    ) -> frozenset[str]:
        """Chart bars plus the aux stream, when configured.

        Args:
            settings: Overrides.
            timeframe: Series timeframe, when known.

        Returns:
            Stream names.
        """
        del settings, timeframe  # Unused.
        if self.aux_timeframe is None:
            return frozenset({protocol.CHART_STREAM})
        return frozenset(
            {protocol.CHART_STREAM, protocol.aux_stream(self.aux_timeframe)}
        )

    def lookback(
        self, settings: Mapping[str, object] | None, stream: str
    ) -> int:
        """Reads the latest completed aux bar.

        Args:
            settings: Overrides.
            stream: Stream name.

        Returns:
            1 for the aux stream, else 0.
        """
        del settings  # Unused.
        if self.aux_timeframe is None:
            return 0
        return int(stream == protocol.aux_stream(self.aux_timeframe))

    def _initial_state(
        self,
        header: protocol.SeriesHeader,
        settings: Mapping[str, settings_lib.SettingValue],
    ) -> EchoState:
        """Builds the empty state.

        Args:
            header: Fresh header.
            settings: Resolved settings (none are declared).

        Returns:
            State before any bar.
        """
        del settings  # Unused.
        return EchoState(header=header)

    def _step(
        self,
        state: EchoState,
        header: protocol.SeriesHeader,
        bar: protocol.BarInput,
    ) -> base.StepResult[EchoState]:
        """Records the close and emits the direction event.

        Args:
            state: Previous state.
            header: Header advanced to this bar.
            bar: Bar input.

        Returns:
            The new state and at most one event.
        """
        close = bar.candle.close
        events: tuple[structures.StructureEvent, ...] = ()
        if state.close is not None and close != state.close:
            name = ECHO_UP if close > state.close else ECHO_DOWN
            events = (self.event(header, name, price=close),)
        aux_close = state.aux_close
        if self.aux_timeframe is not None:
            completed = bar.aux.get(self.aux_timeframe, ())
            if completed:
                aux_close = completed[-1].close
        return base.StepResult(
            state=EchoState(header=header, close=close, aux_close=aux_close),
            events=events,
        )

    def _publish(
        self, state: EchoState
    ) -> tuple[Sequence[structures.Aoi], Mapping[str, structures.ValueType]]:
        """Publishes the close, the bar count and the aux close.

        Args:
            state: Current state.

        Returns:
            No AOIs; the values.
        """
        return (), {
            "close": state.close,
            "bars": state.header.bars,
            "aux_close": state.aux_close,
        }


@dataclasses.dataclass(frozen=True, slots=True)
class ZoneEchoState:
    """State of a ``ZoneEchoPlugin`` series.

    Attributes:
        header: Series bookkeeping.
        live: Live zones, oldest first.
        born: Bar count at which each live zone was created.
    """

    header: protocol.SeriesHeader
    live: tuple[structures.Aoi, ...] = ()
    born: tuple[int, ...] = ()


class ZoneEchoPlugin(base.BasePlugin[ZoneEchoState]):
    """Test plugin publishing one zone per bar over the bar's body.

    Each bar creates an AOI of ``kind`` spanning ``[min(open, close),
    max(open, close)]``: bullish when the close is above the open, bearish
    below it, neutral otherwise, with ``group_id`` ``"even"`` or ``"odd"``
    by the bar's index in the series. A later bar whose range overlaps a
    live zone marks it ``tested``; a zone ends as ``expired`` (end reason
    ``aged_out``, reserved event ``AOI_EXPIRED``) ``expire_after_bars`` bars
    after the bar that created it. ``active_count`` counts the live zones.

    Attributes:
        PLUGIN_ID: Default id, ``"echo_zones"``.
        DISPLAY_NAME: Human-readable name.
        DESCRIPTION: One-sentence description.
        STATE_TYPES: The state dataclass.
        kind: AOI kind the instance publishes (unique per registry).
        expire_after_bars: Bars a zone stays live.
    """

    PLUGIN_ID = "echo_zones"
    DISPLAY_NAME = "Echo zones"
    DESCRIPTION = "Test plugin publishing one body-range zone per bar."
    STATE_TYPES = (ZoneEchoState,)

    def __init__(
        self,
        plugin_id: str = PLUGIN_ID,
        *,
        kind: str = ECHO_ZONE,
        expire_after_bars: int = 3,
    ) -> None:
        """Creates the plugin.

        Args:
            plugin_id: Registry id (lower snake case).
            kind: AOI kind to publish (upper snake case).
            expire_after_bars: Bars a zone stays live, at least 1.

        Raises:
            ValueError: If ``expire_after_bars`` is below 1.
        """
        if expire_after_bars < 1:
            raise ValueError("expire_after_bars must be at least 1")
        self._plugin_id = plugin_id
        self.kind = kind
        self.expire_after_bars = expire_after_bars

    @property
    def plugin_id(self) -> str:
        """The registry id given at construction."""
        return self._plugin_id

    @property
    def output_kinds(self) -> tuple[str, ...]:
        """The one AOI kind given at construction."""
        return (self.kind,)

    @property
    def output_events(self) -> tuple[str, ...]:
        """The reserved ``AOI_EXPIRED`` event."""
        return (structures.AOI_EXPIRED,)

    @property
    def output_values(self) -> tuple[metadata.ValueSpec, ...]:
        """The ``active_count`` value."""
        return (
            metadata.ValueSpec(
                "active_count",
                metadata.ValueType.INTEGER,
                "Live zones.",
            ),
        )

    @property
    def display(self) -> metadata.Display:
        """A hidden layer drawing the zones as boxes."""
        return metadata.Display(
            kinds=(metadata.KindStyle(self.kind, "Echo zone", "box"),),
            end_reasons=(metadata.EndReasonStyle(AGED_OUT, "aged out"),),
        )

    def _initial_state(
        self,
        header: protocol.SeriesHeader,
        settings: Mapping[str, settings_lib.SettingValue],
    ) -> ZoneEchoState:
        """Builds the empty state.

        Args:
            header: Fresh header.
            settings: Resolved settings (none are declared).

        Returns:
            State before any bar.
        """
        del settings  # Unused.
        return ZoneEchoState(header=header)

    def _step(
        self,
        state: ZoneEchoState,
        header: protocol.SeriesHeader,
        bar: protocol.BarInput,
    ) -> base.StepResult[ZoneEchoState]:
        """Ages, tests and creates zones.

        Args:
            state: Previous state.
            header: Header advanced to this bar.
            bar: Bar input.

        Returns:
            The new state with the created, tested and expired zones.
        """
        candle = bar.candle
        live: list[structures.Aoi] = []
        born: list[int] = []
        updated: list[structures.Aoi] = []
        ended: list[structures.Aoi] = []
        events: list[structures.StructureEvent] = []
        for previous, created in zip(state.live, state.born, strict=True):
            if header.bars - created >= self.expire_after_bars:
                ended.append(
                    base.end_aoi(
                        previous, header, structures.AoiState.EXPIRED, AGED_OUT
                    )
                )
                events.append(
                    self.event(
                        header, structures.AOI_EXPIRED, aoi_id=previous.aoi_id
                    )
                )
                continue
            aoi = previous
            touched = candle.low <= aoi.upper and candle.high >= aoi.lower
            if touched and aoi.state is structures.AoiState.ACTIVE:
                aoi = base.touch_aoi(aoi, header, None)
                updated.append(aoi)
            live.append(aoi)
            born.append(created)
        side = structures.ZoneSide.NEUTRAL
        if candle.close != candle.open:
            side = (
                structures.ZoneSide.BULLISH
                if candle.close > candle.open
                else structures.ZoneSide.BEARISH
            )
        zone = self.aoi(
            header,
            aoi_id=ids.stable_id(
                self.plugin_id,
                header.instrument_id,
                header.timeframe.value,
                candle.start_ms,
            ),
            kind=self.kind,
            side=side,
            lower=min(candle.open, candle.close),
            upper=max(candle.open, candle.close),
            created_at_ms=candle.start_ms,
            group_id="even" if (header.bars - 1) % 2 == 0 else "odd",
        )
        live.append(zone)
        born.append(header.bars)
        updated.append(zone)
        return base.StepResult(
            state=ZoneEchoState(
                header=header, live=tuple(live), born=tuple(born)
            ),
            events=tuple(events),
            updated=tuple(updated),
            ended=tuple(ended),
        )

    def _publish(
        self, state: ZoneEchoState
    ) -> tuple[Sequence[structures.Aoi], Mapping[str, structures.ValueType]]:
        """Publishes the live zones and their count.

        Args:
            state: Current state.

        Returns:
            ``(live zones, {"active_count": n})``.
        """
        return state.live, {"active_count": len(state.live)}


def echo_plugins() -> tuple[protocol.IndicatorPlugin, ...]:
    """Plugin factory of the test plugins, shaped like an entry point's.

    A plugin package exposes such a zero-argument factory in the
    ``joseph.indicator_plugins`` entry-point group; discovery tests point an
    entry point at this one.

    Returns:
        ``EchoPlugin()`` and ``ZoneEchoPlugin()``, in that order.
    """
    return (EchoPlugin(), ZoneEchoPlugin())
