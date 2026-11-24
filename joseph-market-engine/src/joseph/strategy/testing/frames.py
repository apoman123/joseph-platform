# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Deterministic builders for candles, AOIs, events, snapshots and frames.

Bar ``index`` counts bars of the instrument's session calendar: for an
exchange instrument (e.g. ``mock:stock:ACME``) bar 0 starts at the first
session open at or after ``base_ms``, the last bar of a session ends at the
close, and nights, weekends and holidays are skipped.

AOIs and events name the plugin that published them. When a builder is not
given an explicit ``source``, the factory's vocabulary (by default the
generic test vocabulary of ``testing.vocabulary``) resolves it from the
kind or event name.
"""

from __future__ import annotations

import dataclasses
import decimal
from collections.abc import Iterable
from collections.abc import Mapping

from joseph.domain import ids
from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import quality
from joseph.domain import sessions
from joseph.domain import structures
from joseph.domain import timeframes
from joseph.strategy.domain import catalog
from joseph.strategy.testing import vocabulary as test_vocabulary

BASE_MS = 1_795_132_800_000
"""2026-11-20 00:00 UTC; aligned to every timeframe up to 1d."""

_TEN = decimal.Decimal(10)


def dec(value: str | int | decimal.Decimal) -> decimal.Decimal:
    """Converts a literal to ``Decimal``.

    Args:
        value: Text, int or decimal.

    Returns:
        The decimal.
    """
    if isinstance(value, decimal.Decimal):
        return value
    return decimal.Decimal(str(value))


@dataclasses.dataclass(frozen=True, slots=True)
class BarSpec:
    """OHLC of one test bar.

    Attributes:
        open: Open price.
        high: High price.
        low: Low price.
        close: Close price.
        volume: Base volume.
    """

    open: decimal.Decimal
    high: decimal.Decimal
    low: decimal.Decimal
    close: decimal.Decimal
    volume: decimal.Decimal = _TEN


def bar(
    open_: str | int, high: str | int, low: str | int, close: str | int
) -> BarSpec:
    """Builds a bar specification.

    Args:
        open_: Open price.
        high: High price.
        low: Low price.
        close: Close price.

    Returns:
        The specification.
    """
    return BarSpec(dec(open_), dec(high), dec(low), dec(close))


class FrameFactory:
    """Builds frames for one instrument and signal timeframe.

    Attributes:
        instrument_id: Instrument of every built object.
        timeframe: Signal timeframe.
        base_ms: Start of bar index 0.
        calendar: Session calendar of the instrument.
        vocabulary: Resolves the publishing plugin of kinds and events.
    """

    def __init__(
        self,
        instrument_id: str = "binance:perp:BTCUSDT",
        timeframe: timeframes.Timeframe = timeframes.Timeframe.M5,
        base_ms: int = BASE_MS,
        vocabulary: catalog.Vocabulary | None = None,
    ) -> None:
        """Initializes the factory.

        Args:
            instrument_id: Canonical instrument id.
            timeframe: Signal timeframe.
            base_ms: Aligned start of bar 0.
            vocabulary: Resolves the publishing plugin of kinds and events;
                defaults to the generic test vocabulary.
        """
        self.instrument_id = instruments.InstrumentId.parse(instrument_id)
        self.timeframe = timeframe
        self.calendar = sessions.for_instrument(self.instrument_id)
        self.base_ms = self.calendar.ceil(timeframe, base_ms)
        self._starts: list[int] = [self.base_ms]
        self.vocabulary = (
            test_vocabulary.VOCABULARY if vocabulary is None else vocabulary
        )

    def start(self, index: int) -> int:
        """Returns the start of signal bar ``index``.

        Args:
            index: Bar index.

        Returns:
            Start time (UTC ms).
        """
        if index < 0:
            return self.calendar.shift(self.timeframe, self.base_ms, index)
        while len(self._starts) <= index:
            self._starts.append(
                self.calendar.shift(self.timeframe, self._starts[-1], 1)
            )
        return self._starts[index]

    def close(self, index: int) -> int:
        """Returns the close (exclusive end) of signal bar ``index``.

        Args:
            index: Bar index.

        Returns:
            Close time (UTC ms); a session's last bar closes at the close.
        """
        return self.calendar.end_of(self.timeframe, self.start(index))

    def last_closed_start(self, tf: timeframes.Timeframe, as_of_ms: int) -> int:
        """Returns the start of the newest ``tf`` bar closed at a time.

        Args:
            tf: Timeframe.
            as_of_ms: Visibility time.

        Returns:
            Bar start on the instrument's calendar; the start of the bar
            containing ``as_of_ms`` when none has closed yet.
        """
        start = self.calendar.last_closed_start(tf, as_of_ms)
        return self.calendar.floor(tf, as_of_ms) if start is None else start

    def candle(
        self,
        index: int,
        spec: BarSpec,
        *,
        is_final: bool = True,
        feed_state: quality.FeedState = quality.FeedState.LIVE,
        revision: int = 0,
    ) -> market.Candle:
        """Builds a signal-timeframe candle.

        Args:
            index: Bar index.
            spec: OHLCV.
            is_final: Whether the bar is closed.
            feed_state: Provenance.
            revision: Correction counter.

        Returns:
            The candle.
        """
        return market.Candle(
            instrument_id=self.instrument_id,
            timeframe=self.timeframe,
            start_ms=self.start(index),
            end_ms=self.close(index),
            open=spec.open,
            high=spec.high,
            low=spec.low,
            close=spec.close,
            base_volume=spec.volume,
            is_final=is_final,
            source="test",
            feed_state=feed_state,
            received_at_ms=self.close(index) + 50,
            revision=revision,
        )

    def aoi(
        self,
        kind: structures.AoiKind,
        side: str,
        lower: str | int,
        upper: str | int,
        *,
        confirmed_index: int,
        tf: timeframes.Timeframe | None = None,
        aoi_id: str | None = None,
        state: structures.AoiState = structures.AoiState.ACTIVE,
        quality_score: int | None = 70,
        group_id: str | None = None,
        source: str | None = None,
        invalidated_at_ms: int | None = None,
    ) -> structures.Aoi:
        """Builds an AOI confirmed at the close of a signal bar.

        The AOI is created two bars before the confirming bar (a
        three-candle pattern), never before bar 0.

        Args:
            kind: AOI kind.
            side: ``bullish``, ``bearish`` or ``neutral``.
            lower: Lower bound.
            upper: Upper bound.
            confirmed_index: Signal bar whose close confirmed the AOI.
            tf: Detection timeframe (defaults to the signal timeframe).
            aoi_id: Explicit id (derived from inputs otherwise).
            state: Lifecycle state.
            quality_score: Score.
            group_id: Plugin-defined group the AOI belongs to.
            source: Plugin id (resolved from the kind otherwise).
            invalidated_at_ms: Invalidation time.

        Returns:
            The AOI.

        Raises:
            ValueError: If no source is given and the vocabulary does not
                know the kind.
        """
        plugin = source or self.vocabulary.kind_plugin.get(kind)
        if plugin is None:
            raise ValueError(f"no plugin publishes {kind}; pass source")
        confirmed = self.close(confirmed_index)
        created = max(self.start(confirmed_index - 2), self.base_ms)
        return structures.Aoi(
            aoi_id=aoi_id
            or ids.stable_id(kind.lower(), lower, upper, confirmed),
            instrument_id=self.instrument_id,
            timeframe=tf or self.timeframe,
            source=plugin,
            kind=kind,
            side=structures.ZoneSide(side),
            state=state,
            lower=dec(lower),
            upper=dec(upper),
            created_at_ms=min(created, confirmed),
            confirmed_at_ms=confirmed,
            as_of_ms=confirmed,
            indicator_version="1.0.0",
            invalidated_at_ms=invalidated_at_ms,
            quality_score=quality_score,
            group_id=group_id,
        )

    def event(
        self,
        name: structures.StructureEventName,
        index: int,
        *,
        tf: timeframes.Timeframe | None = None,
        aoi_id: str | None = None,
        price: str | int | None = None,
        source: str | None = None,
    ) -> structures.StructureEvent:
        """Builds an event confirmed at the close of signal bar ``index``.

        Args:
            name: Event name.
            index: Signal bar index.
            tf: Event timeframe (defaults to the signal timeframe).
            aoi_id: Related AOI.
            price: Reference price.
            source: Plugin id (the name's default source otherwise, or
                ``test`` for reserved events).

        Returns:
            The event.
        """
        event_tf = tf or self.timeframe
        confirmed = self.close(index)
        return structures.StructureEvent(
            event_id=ids.stable_id(
                "evt", name, event_tf.value, confirmed, aoi_id, source
            ),
            instrument_id=self.instrument_id,
            timeframe=event_tf,
            source=source or self.vocabulary.event_source(name, None) or "test",
            name=name,
            bar_start_ms=self.last_closed_start(event_tf, confirmed),
            confirmed_at_ms=confirmed,
            price=None if price is None else dec(price),
            aoi_id=aoi_id,
        )

    def snapshot(
        self,
        plugin_id: str,
        tf: timeframes.Timeframe,
        index: int,
        *,
        aois: Iterable[structures.Aoi] = (),
        values: Mapping[str, structures.ValueType] | None = None,
        warm: bool = True,
        as_of_ms: int | None = None,
        feed_state: quality.FeedState = quality.FeedState.LIVE,
    ) -> structures.IndicatorSnapshot:
        """Builds a snapshot visible at the close of signal bar ``index``.

        Args:
            plugin_id: Plugin id.
            tf: Series timeframe.
            index: Signal bar index whose close the snapshot is visible at.
            aois: AOIs in the snapshot.
            values: Published values.
            warm: Whether warm-up completed.
            as_of_ms: Explicit as-of time (defaults to the bar close).
            feed_state: Provenance of the snapshot inputs.

        Returns:
            The snapshot.
        """
        as_of = self.close(index) if as_of_ms is None else as_of_ms
        return structures.IndicatorSnapshot(
            instrument_id=self.instrument_id,
            timeframe=tf,
            plugin_id=plugin_id,
            indicator_version="1.0.0",
            method=structures.IndicatorMethod.PINE_PARITY,
            bar_start_ms=self.last_closed_start(tf, as_of),
            as_of_ms=as_of,
            aois=tuple(aois),
            events=(),
            values=dict(values or {}),
            warmup_complete=warm,
            data_quality=quality.DataQuality(feed_state=feed_state),
        )

    def frame(
        self,
        index: int,
        spec: BarSpec,
        snapshots: Iterable[structures.IndicatorSnapshot] = (),
        events: Iterable[structures.StructureEvent] = (),
        *,
        feed_state: quality.FeedState = quality.FeedState.LIVE,
        is_final: bool = True,
        revision: int = 0,
    ) -> structures.BarFrame:
        """Builds a frame.

        Args:
            index: Signal bar index.
            spec: OHLCV of the signal candle.
            snapshots: Visible snapshots.
            events: Events confirmed within the bar.
            feed_state: Provenance of the signal candle.
            is_final: Whether the candle is closed.
            revision: Candle revision.

        Returns:
            The frame.
        """
        snaps = {(s.plugin_id, s.timeframe): s for s in snapshots}
        return structures.BarFrame(
            instrument_id=self.instrument_id,
            timeframe=self.timeframe,
            candle=self.candle(
                index,
                spec,
                is_final=is_final,
                feed_state=feed_state,
                revision=revision,
            ),
            snapshots=snaps,
            events=tuple(events),
            feed_state=feed_state,
        )
