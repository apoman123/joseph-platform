# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Classic floor pivot points from the previous period's range.

From the high ``H``, low ``L`` and close ``C`` of the last completed
``period`` bar (``1d``, ``1w`` or ``1M``; on exchanges the calendar's
session, week and month bars)::

    P = (H + L + C) / 3
    R1 = 2P - L          S1 = 2P - H
    R2 = P + (H - L)     S2 = P - (H - L)
    R3 = H + 2(P - L)    S3 = L - 2(H - P)

computed in doubles and published as prices (values ``p`` ... ``s3``) and as
level AOIs (``lower == upper``) of kinds ``PIVOT_P`` (neutral),
``PIVOT_R1`` ... ``PIVOT_R3`` (bearish) and ``PIVOT_S1`` ... ``PIVOT_S3``
(bullish).

Timing, without look-ahead: the levels apply to the period after the
completed bar, which starts at ``applies`` (the calendar's next ``period``
bar). They are published on the first chart bar that sees the completed
bar (its ``end_ms`` is at or before the chart bar's close) and that closes
at or after ``applies``. On a 24x7 market that is the chart bar closing
with the period; on an exchange it is the first bar of the next session,
since the levels are known at the session close but apply only from the
next open. The AOIs are created at ``applies`` (``period_start_ms``) and
confirmed at the close of that chart bar.

Lifecycle: a live level becomes ``tested`` on the first chart bar that
starts at or after ``applies`` and whose range includes it; every level
expires on the bar that publishes the next period's levels.
"""

from __future__ import annotations

import dataclasses
import decimal
from collections.abc import Mapping
from collections.abc import Sequence

from joseph.domain import errors
from joseph.domain import ids
from joseph.domain import market
from joseph.domain import sessions
from joseph.domain import structures
from joseph.domain import timeframes as tfs
from joseph.indicators.builtin import _series
from joseph.indicators.sdk import base
from joseph.indicators.sdk import metadata
from joseph.indicators.sdk import protocol
from joseph.indicators.sdk import settings as settings_lib

_SIDE = structures.ZoneSide

_LEVELS: tuple[tuple[str, str, structures.ZoneSide], ...] = (
    ("p", "PIVOT_P", _SIDE.NEUTRAL),
    ("r1", "PIVOT_R1", _SIDE.BEARISH),
    ("r2", "PIVOT_R2", _SIDE.BEARISH),
    ("r3", "PIVOT_R3", _SIDE.BEARISH),
    ("s1", "PIVOT_S1", _SIDE.BULLISH),
    ("s2", "PIVOT_S2", _SIDE.BULLISH),
    ("s3", "PIVOT_S3", _SIDE.BULLISH),
)
"""Value key, AOI kind and side of each level, in publication order."""

_DESCRIPTIONS: Mapping[str, str] = {
    "p": "Pivot (H + L + C) / 3.",
    "r1": "Resistance 1, 2P - L.",
    "r2": "Resistance 2, P + (H - L).",
    "r3": "Resistance 3, H + 2(P - L).",
    "s1": "Support 1, 2P - H.",
    "s2": "Support 2, P - (H - L).",
    "s3": "Support 3, L - 2(H - P).",
}
"""Description of each level value."""

_TONES: Mapping[structures.ZoneSide, metadata.Tone] = {
    _SIDE.NEUTRAL: metadata.Tone.NEUTRAL,
    _SIDE.BEARISH: metadata.Tone.DOWN,
    _SIDE.BULLISH: metadata.Tone.UP,
}
"""Display tone of each level side."""

_PERIODS = ("1d", "1w", "1M")
"""Accepted ``period`` values."""

_TIMEFRAMES: tuple[tfs.Timeframe, ...] = tuple(
    tf
    for tf in tfs.Timeframe
    if not tf.is_seconds and tf is not tfs.Timeframe.MN1
)
"""Chart timeframes: minutes to weeks (below the longest period)."""


def floor_levels(high: float, low: float, close: float) -> dict[str, float]:
    """Computes the classic floor pivot levels.

    Args:
        high: High of the completed period.
        low: Its low.
        close: Its close.

    Returns:
        Level by value key (``p``, ``r1`` ... ``s3``).
    """
    pivot = (high + low + close) / 3
    return {
        "p": pivot,
        "r1": 2 * pivot - low,
        "r2": pivot + (high - low),
        "r3": high + 2 * (pivot - low),
        "s1": 2 * pivot - high,
        "s2": pivot - (high - low),
        "s3": low - 2 * (high - pivot),
    }


@dataclasses.dataclass(frozen=True, slots=True)
class PivotState:
    """State of the pivot points study.

    Attributes:
        header: Series bookkeeping.
        tick: Series tick, fixed at the first bar (``None`` before it).
        source_start_ms: Start of the completed period bar behind the
            current levels (``None`` before the first publication).
        period_start_ms: Start of the period the current levels apply to.
        live: Current level AOIs, in publication order.
        levels: Current level prices as ``(key, price)`` pairs.
    """

    header: protocol.SeriesHeader
    tick: decimal.Decimal | None
    source_start_ms: int | None
    period_start_ms: int | None
    live: tuple[structures.Aoi, ...]
    levels: tuple[tuple[str, decimal.Decimal | None], ...]


def _period(settings: Mapping[str, settings_lib.SettingValue]) -> tfs.Timeframe:
    """Reads the period timeframe.

    Args:
        settings: Resolved settings.

    Returns:
        The ``period`` setting as a timeframe.
    """
    return tfs.parse(settings_lib.get_str(settings, "period"))


def _touches(candle: market.Candle, aoi: structures.Aoi) -> bool:
    """Checks whether a bar tests a live level for the first time.

    Args:
        candle: Chart bar.
        aoi: Level AOI.

    Returns:
        True when the level is still untested, the bar lies in the period
        the level applies to and its range includes the level.
    """
    return (
        aoi.state is structures.AoiState.ACTIVE
        and candle.start_ms >= aoi.created_at_ms
        and candle.low <= aoi.lower <= candle.high
    )


class PivotPointsPlugin(base.BasePlugin[PivotState]):
    """Floor pivot levels of the previous period.

    Attributes:
        PLUGIN_ID: ``"pivot_points"``.
        DISPLAY_NAME: Human-readable name.
        DESCRIPTION: One-sentence description.
        TIMEFRAMES: Minute to weekly charts.
        OUTPUT_KINDS: The seven level kinds.
        OUTPUT_VALUES: The seven levels and ``period_start_ms``.
        DISPLAY: Dashed levels labelled P, R1 to R3 and S1 to S3, visible
            by default.
        SETTINGS: ``period`` and ``tick_size``.
        STATE_TYPES: The state dataclass.
    """

    PLUGIN_ID = "pivot_points"
    DISPLAY_NAME = "Pivot points"
    DESCRIPTION = (
        "Classic floor pivot levels P, R1 to R3 and S1 to S3 from the "
        "previous period's high, low and close."
    )
    TIMEFRAMES = _TIMEFRAMES
    OUTPUT_KINDS = tuple(kind for _, kind, _ in _LEVELS)
    OUTPUT_VALUES = (
        *(
            metadata.ValueSpec(
                key,
                metadata.ValueType.DECIMAL,
                _DESCRIPTIONS[key],
                metadata.Unit.PRICE,
            )
            for key, _, _ in _LEVELS
        ),
        metadata.ValueSpec(
            "period_start_ms",
            metadata.ValueType.TIMESTAMP_MS,
            "Start of the period the levels apply to.",
            metadata.Unit.NONE,
        ),
    )
    DISPLAY = metadata.Display(
        default_visible=True,
        z_index=20,
        kinds=tuple(
            metadata.KindStyle(
                kind,
                key.upper(),
                "level",
                tone=_TONES[side],
                line_style=metadata.LineStyle.DASHED,
            )
            for key, kind, side in _LEVELS
        ),
    )
    SETTINGS = (
        settings_lib.SettingSpec(
            "period",
            settings_lib.SettingKind.ENUM,
            "1d",
            "Period whose high, low and close set the levels.",
            choices=_PERIODS,
        ),
        _series.tick_spec(),
    )
    STATE_TYPES = (PivotState,)

    def required_streams(
        self,
        settings: Mapping[str, object] | None,
        *,
        timeframe: tfs.Timeframe | None = None,
    ) -> frozenset[str]:
        """Chart bars plus completed bars of the period.

        Args:
            settings: Overrides.
            timeframe: Chart timeframe, when known.

        Returns:
            Stream names.
        """
        del timeframe  # Unused.
        period = _period(self.resolve_settings(settings))
        return frozenset({protocol.CHART_STREAM, protocol.aux_stream(period)})

    def lookback(
        self, settings: Mapping[str, object] | None, stream: str
    ) -> int:
        """Reads only the latest completed period bar.

        Args:
            settings: Overrides.
            stream: Stream name.

        Returns:
            1 for the period stream, else 0.
        """
        period = _period(self.resolve_settings(settings))
        return 1 if stream == protocol.aux_stream(period) else 0

    def _initial_state(
        self,
        header: protocol.SeriesHeader,
        settings: Mapping[str, settings_lib.SettingValue],
    ) -> PivotState:
        """Builds the empty state.

        Args:
            header: Fresh header.
            settings: Resolved settings.

        Returns:
            State before any bar.

        Raises:
            ValidationFailedError: If the chart timeframe is not shorter
                than the period.
        """
        period = _period(settings)
        if header.timeframe.duration_ms >= period.duration_ms:
            raise errors.ValidationFailedError(
                f"{self.plugin_id}: the {header.timeframe} chart must be "
                f"shorter than the {period} period"
            )
        return PivotState(
            header=header,
            tick=None,
            source_start_ms=None,
            period_start_ms=None,
            live=(),
            levels=(),
        )

    def _step(
        self,
        state: PivotState,
        header: protocol.SeriesHeader,
        bar: protocol.BarInput,
    ) -> base.StepResult[PivotState]:
        """Publishes new levels when due and tracks tests of live ones.

        Args:
            state: Previous state.
            header: Header advanced to this bar.
            bar: Bar input.

        Returns:
            The new state with new, tested and expired levels.
        """
        settings = header.settings_map()
        tick = state.tick or _series.series_tick(settings, bar.candle)
        state = dataclasses.replace(state, tick=tick)
        before = {aoi.aoi_id: aoi for aoi in state.live}
        ended: tuple[structures.Aoi, ...] = ()
        completed = bar.aux.get(_period(settings), ())
        if completed:
            fresh = self._publication(state, header, completed[-1], tick)
            if fresh is not None:
                ended = tuple(
                    base.end_aoi(aoi, header, structures.AoiState.EXPIRED)
                    for aoi in state.live
                )
                state = fresh
        live = tuple(
            base.touch_aoi(aoi, header, None)
            if _touches(bar.candle, aoi)
            else aoi
            for aoi in state.live
        )
        updated = tuple(
            aoi for aoi in live if before.get(aoi.aoi_id) is not aoi
        )
        return base.StepResult(
            state=dataclasses.replace(state, live=live),
            updated=updated,
            ended=ended,
        )

    def _publication(
        self,
        state: PivotState,
        header: protocol.SeriesHeader,
        completed: market.Candle,
        tick: decimal.Decimal,
    ) -> PivotState | None:
        """Builds the next period's levels when they are due on this bar.

        Args:
            state: State before the bar.
            header: Header advanced to this bar.
            completed: Latest completed period bar visible at the close.
            tick: Series tick.

        Returns:
            The state holding the new levels, or ``None`` when the bar
            publishes nothing.
        """
        if (
            state.source_start_ms is not None
            and completed.start_ms <= state.source_start_ms
        ):
            return None
        calendar = sessions.for_instrument(header.instrument_id)
        applies = calendar.shift(completed.timeframe, completed.start_ms, 1)
        if header.as_of_ms < applies:
            return None
        prices = {
            key: _series.publish_price(value, tick)
            for key, value in floor_levels(
                float(completed.high),
                float(completed.low),
                float(completed.close),
            ).items()
        }
        aois = []
        for key, kind, side in _LEVELS:
            price = prices[key]
            if price is None:
                continue
            aois.append(
                self.aoi(
                    header,
                    aoi_id=ids.stable_id(
                        self.plugin_id,
                        header.instrument_id,
                        header.timeframe.value,
                        applies,
                        kind,
                    ),
                    kind=kind,
                    side=side,
                    lower=price,
                    upper=price,
                    created_at_ms=applies,
                )
            )
        return dataclasses.replace(
            state,
            source_start_ms=completed.start_ms,
            period_start_ms=applies,
            live=tuple(aois),
            levels=tuple(prices.items()),
        )

    def _publish(
        self, state: PivotState
    ) -> tuple[Sequence[structures.Aoi], Mapping[str, structures.ValueType]]:
        """Publishes the current levels.

        Args:
            state: Current state.

        Returns:
            The live level AOIs and the level values (null before the first
            publication) with ``period_start_ms``.
        """
        values: dict[str, structures.ValueType] = dict.fromkeys(
            key for key, _, _ in _LEVELS
        )
        values.update(state.levels)
        values["period_start_ms"] = state.period_start_ms
        return state.live, values
