"""Deterministic builders for candles, AOIs, events, snapshots and frames."""

from __future__ import annotations

import dataclasses
import decimal
from collections.abc import Iterable
from collections.abc import Mapping

from joseph.domain import ids
from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import quality
from joseph.domain import structures
from joseph.domain import timeframes

BASE_MS = 1_795_132_800_000
"""2026-11-20 00:00 UTC; aligned to every timeframe up to 1d."""

D = decimal.Decimal
_TEN = D(10)


def dec(value: str | int | decimal.Decimal) -> decimal.Decimal:
    """Converts a literal to ``Decimal``.

    Args:
        value: Text, int or decimal.

    Returns:
        The decimal.
    """
    return value if isinstance(value, decimal.Decimal) else D(str(value))


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
    """

    def __init__(
        self,
        instrument_id: str = "binance:perp:BTCUSDT",
        timeframe: timeframes.Timeframe = timeframes.Timeframe.M5,
        base_ms: int = BASE_MS,
    ) -> None:
        """Initializes the factory.

        Args:
            instrument_id: Canonical instrument id.
            timeframe: Signal timeframe.
            base_ms: Aligned start of bar 0.
        """
        self.instrument_id = instruments.InstrumentId.parse(instrument_id)
        self.timeframe = timeframe
        self.base_ms = base_ms

    def start(self, index: int) -> int:
        """Returns the start of signal bar ``index``.

        Args:
            index: Bar index.

        Returns:
            Start time (UTC ms).
        """
        return self.timeframe.shift(self.base_ms, index)

    def close(self, index: int) -> int:
        """Returns the close (exclusive end) of signal bar ``index``.

        Args:
            index: Bar index.

        Returns:
            Close time (UTC ms).
        """
        return self.start(index + 1)

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
        kind: structures.AoiKind | str,
        side: str,
        lower: str | int,
        upper: str | int,
        *,
        confirmed_index: int,
        tf: timeframes.Timeframe | None = None,
        aoi_id: str | None = None,
        state: structures.AoiState = structures.AoiState.ACTIVE,
        quality_score: int | None = 70,
        range_id: str | None = None,
        source: str | None = None,
        invalidated_at_ms: int | None = None,
    ) -> structures.Aoi:
        """Builds an AOI confirmed at the close of a signal bar.

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
            range_id: Dealing range id.
            source: Plugin id (derived from kind otherwise).
            invalidated_at_ms: Invalidation time.

        Returns:
            The AOI.
        """
        kind_enum = structures.AoiKind(kind)
        plugin = source or _PLUGIN_BY_KIND[kind_enum]
        confirmed = self.close(confirmed_index)
        created = max(self.start(confirmed_index - 2), self.base_ms)
        return structures.Aoi(
            aoi_id=aoi_id
            or ids.stable_id(kind_enum.value.lower(), lower, upper, confirmed),
            instrument_id=self.instrument_id,
            timeframe=tf or self.timeframe,
            source=plugin,
            kind=kind_enum,
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
            range_id=range_id,
        )

    def event(
        self,
        name: structures.StructureEventName | str,
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
            source: Plugin id (derived from the name otherwise).

        Returns:
            The event.
        """
        name_enum = structures.StructureEventName(name)
        event_tf = tf or self.timeframe
        confirmed = self.close(index)
        return structures.StructureEvent(
            event_id=ids.stable_id(
                "evt", name_enum.value, event_tf.value, confirmed, aoi_id
            ),
            instrument_id=self.instrument_id,
            timeframe=event_tf,
            source=source or _PLUGIN_BY_EVENT.get(name_enum, "test"),
            name=name_enum,
            bar_start_ms=event_tf.shift(event_tf.floor(confirmed), -1),
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
            bar_start_ms=tf.shift(tf.floor(as_of), -1),
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


_PLUGIN_BY_KIND: Mapping[structures.AoiKind, str] = {
    structures.AoiKind.FVG: "fvg_ifvg",
    structures.AoiKind.IFVG: "fvg_ifvg",
    structures.AoiKind.OB: "order_breaker_blocks",
    structures.AoiKind.BREAKER: "order_breaker_blocks",
    structures.AoiKind.BSL: "liquidity_sweeps",
    structures.AoiKind.SSL: "liquidity_sweeps",
    structures.AoiKind.EQH: "liquidity_sweeps",
    structures.AoiKind.EQL: "liquidity_sweeps",
    structures.AoiKind.PREMIUM: "premium_discount_ote",
    structures.AoiKind.DISCOUNT: "premium_discount_ote",
    structures.AoiKind.EQUILIBRIUM: "premium_discount_ote",
    structures.AoiKind.OTE: "premium_discount_ote",
    structures.AoiKind.VAH: "auction_value_areas",
    structures.AoiKind.POC: "auction_value_areas",
    structures.AoiKind.VAL: "auction_value_areas",
    structures.AoiKind.PDH: "daily_bias",
    structures.AoiKind.PDL: "daily_bias",
    structures.AoiKind.SESSION_HIGH: "daily_bias",
    structures.AoiKind.SESSION_LOW: "daily_bias",
}

_E = structures.StructureEventName
_PLUGIN_BY_EVENT: Mapping[structures.StructureEventName, str] = {
    _E.SSL_SWEEP: "liquidity_sweeps",
    _E.BSL_SWEEP: "liquidity_sweeps",
    _E.SSL_BREAK: "liquidity_sweeps",
    _E.BSL_BREAK: "liquidity_sweeps",
    _E.SELLER_FAILURE: "order_flow_pressure",
    _E.BUYER_FAILURE: "order_flow_pressure",
    _E.SELLER_BREAKOUT: "order_flow_pressure",
    _E.BUYER_BREAKOUT: "order_flow_pressure",
    _E.BOS_BULL: "daily_bias",
    _E.BOS_BEAR: "daily_bias",
    _E.CHOCH_BULL: "daily_bias",
    _E.CHOCH_BEAR: "daily_bias",
    _E.FVG_FORMED: "fvg_ifvg",
    _E.OB_FORMED: "order_breaker_blocks",
}
