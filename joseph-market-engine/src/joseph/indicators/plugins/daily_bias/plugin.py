"""Daily bias: per-timeframe market structure, PDH/PDL and NY sessions.

Clean-room implementation of V5 §6.2-A. See README.md for semantics and
assumptions. The strategy BIAS node combines this plugin's per-timeframe
``trend`` values; this plugin never reads other timeframes itself.
"""

from __future__ import annotations

import dataclasses
import datetime
import decimal
import zoneinfo
from collections.abc import Mapping
from collections.abc import Sequence

from joseph.domain import ids
from joseph.domain import market
from joseph.domain import structures
from joseph.indicators.sdk import base
from joseph.indicators.sdk import protocol
from joseph.indicators.sdk import settings as settings_lib
from joseph.indicators.sdk import ta

_DAY_MS = 86_400_000
_NEW_YORK = zoneinfo.ZoneInfo("America/New_York")
_EVENTS = structures.StructureEventName

# (session, start minute, end minute) in New York local time, half-open.
_SESSIONS: tuple[tuple[str, int, int], ...] = (
    ("ASIA", 20 * 60, 24 * 60),
    ("LONDON", 2 * 60, 5 * 60),
    ("NEW_YORK", 8 * 60 + 30, 11 * 60),
)


def session_for(start_ms: int) -> str:
    """Returns the NY-time session label for a bar start (DST aware).

    Args:
        start_ms: Bar start in UTC epoch milliseconds.

    Returns:
        ``"ASIA"``, ``"LONDON"``, ``"NEW_YORK"`` or ``"NONE"``.
    """
    local = datetime.datetime.fromtimestamp(
        start_ms / 1000, tz=datetime.UTC
    ).astimezone(_NEW_YORK)
    minute = local.hour * 60 + local.minute
    for name, start, end in _SESSIONS:
        if start <= minute < end:
            return name
    return "NONE"


@dataclasses.dataclass(frozen=True, slots=True)
class Swing:
    """Latest confirmed pivot and whether a close already broke it.

    Attributes:
        price: Pivot price.
        start_ms: Pivot bar start.
        broken: True once a close crossed it (prevents re-triggering).
    """

    price: decimal.Decimal
    start_ms: int
    broken: bool = False


@dataclasses.dataclass(frozen=True, slots=True)
class DailyBiasState:
    """Immutable state of the daily-bias plugin.

    Attributes:
        header: Series bookkeeping.
        pivots: Pivot tracker.
        swing_high: Latest confirmed pivot high.
        swing_low: Latest confirmed pivot low.
        trend: ``BULLISH``, ``BEARISH`` or ``NEUTRAL``.
        last_event: ``BOS``, ``CHOCH``, ``INITIAL`` or ``NONE``.
        last_event_direction: Direction of the last event.
        day_index: Current UTC day number.
        day_high: Current day high so far.
        day_low: Current day low so far.
        day_start_ms: Current day start.
        pdh: Prior UTC day high.
        pdl: Prior UTC day low.
        session: Session label of the last bar.
        day_aois: Current PDH/PDL AOIs.
    """

    header: protocol.SeriesHeader
    pivots: ta.PivotTracker
    swing_high: Swing | None = None
    swing_low: Swing | None = None
    trend: str = "NEUTRAL"
    last_event: str = "NONE"
    last_event_direction: str | None = None
    day_index: int | None = None
    day_high: decimal.Decimal | None = None
    day_low: decimal.Decimal | None = None
    day_start_ms: int | None = None
    pdh: decimal.Decimal | None = None
    pdl: decimal.Decimal | None = None
    session: str = "NONE"
    day_aois: tuple[structures.Aoi, ...] = ()


class DailyBiasPlugin(base.BasePlugin[DailyBiasState]):
    """Confirmed BOS/CHoCH structure, PDH/PDL and session labels."""

    PLUGIN_ID = "daily_bias"
    DISPLAY_NAME = "Daily bias dashboard"
    DESCRIPTION = (
        "Confirmed pivot structure (BOS/CHoCH) per timeframe, prior-day "
        "high/low and New York session windows."
    )
    OUTPUT_KINDS = (structures.AoiKind.PDH, structures.AoiKind.PDL)
    OUTPUT_EVENTS = (
        _EVENTS.BOS_BULL,
        _EVENTS.BOS_BEAR,
        _EVENTS.CHOCH_BULL,
        _EVENTS.CHOCH_BEAR,
        _EVENTS.INITIAL_BREAK_BULL,
        _EVENTS.INITIAL_BREAK_BEAR,
    )
    SETTINGS = (
        settings_lib.SettingSpec(
            "pivot_left",
            settings_lib.SettingKind.INT,
            3,
            "Bars left of a pivot.",
            minimum=1,
            maximum=50,
        ),
        settings_lib.SettingSpec(
            "pivot_right",
            settings_lib.SettingKind.INT,
            3,
            "Bars right of a pivot (confirmation delay).",
            minimum=1,
            maximum=50,
        ),
    )
    STATE_TYPES = (DailyBiasState, Swing)

    def warmup_for(
        self, settings: Mapping[str, settings_lib.SettingValue]
    ) -> int:
        """Returns bars needed before the first pivot can confirm.

        Args:
            settings: Resolved settings.

        Returns:
            ``left + right + 1``.
        """
        return (
            settings_lib.get_int(settings, "pivot_left")
            + settings_lib.get_int(settings, "pivot_right")
            + 1
        )

    def _initial_state(
        self,
        header: protocol.SeriesHeader,
        settings: Mapping[str, settings_lib.SettingValue],
    ) -> DailyBiasState:
        """Builds the initial state.

        Args:
            header: Fresh header.
            settings: Resolved settings.

        Returns:
            Initial state.
        """
        return DailyBiasState(
            header=header,
            pivots=ta.PivotTracker(
                left=settings_lib.get_int(settings, "pivot_left"),
                right=settings_lib.get_int(settings, "pivot_right"),
            ),
        )

    def _step(
        self,
        state: DailyBiasState,
        header: protocol.SeriesHeader,
        bar: protocol.BarInput,
    ) -> base.StepResult[DailyBiasState]:
        """Applies one closed bar.

        Args:
            state: Previous state.
            header: Advanced header.
            bar: Bar input.

        Returns:
            Step result.
        """
        candle = bar.candle
        point = ta.BarPoint.of(candle)
        pivots, pivot_high, pivot_low = state.pivots.update(point)
        swing_high = state.swing_high
        swing_low = state.swing_low
        if pivot_high is not None:
            swing_high = Swing(pivot_high.price, pivot_high.start_ms)
        if pivot_low is not None:
            swing_low = Swing(pivot_low.price, pivot_low.start_ms)

        trend = state.trend
        last_event = state.last_event
        direction = state.last_event_direction
        events: list[structures.StructureEvent] = []
        if (
            swing_high is not None
            and not swing_high.broken
            and candle.close > swing_high.price
        ):
            name, last_event = self._classify(trend, bullish=True)
            trend, direction = "BULLISH", "BULLISH"
            events.append(
                self.event(
                    header,
                    name,
                    price=swing_high.price,
                    meta={
                        "pivot_start_ms": swing_high.start_ms,
                        "direction": "BULLISH",
                    },
                )
            )
            swing_high = dataclasses.replace(swing_high, broken=True)
        if (
            swing_low is not None
            and not swing_low.broken
            and candle.close < swing_low.price
        ):
            name, last_event = self._classify(trend, bullish=False)
            trend, direction = "BEARISH", "BEARISH"
            events.append(
                self.event(
                    header,
                    name,
                    price=swing_low.price,
                    meta={
                        "pivot_start_ms": swing_low.start_ms,
                        "direction": "BEARISH",
                    },
                )
            )
            swing_low = dataclasses.replace(swing_low, broken=True)

        day_state, updated, ended = self._roll_day(state, header, candle)
        new_state = dataclasses.replace(
            day_state,
            pivots=pivots,
            swing_high=swing_high,
            swing_low=swing_low,
            trend=trend,
            last_event=last_event,
            last_event_direction=direction,
            session=session_for(candle.start_ms),
        )
        return base.StepResult(
            state=new_state,
            events=tuple(events),
            updated=updated,
            ended=ended,
        )

    @staticmethod
    def _classify(
        trend: str, *, bullish: bool
    ) -> tuple[structures.StructureEventName, str]:
        """Classifies a pivot break given the prevailing trend.

        Args:
            trend: Trend before the break.
            bullish: True for a break above a pivot high.

        Returns:
            ``(event_name, last_event_label)``.
        """
        if trend == "NEUTRAL":
            return (
                _EVENTS.INITIAL_BREAK_BULL
                if bullish
                else _EVENTS.INITIAL_BREAK_BEAR,
                "INITIAL",
            )
        with_trend = (trend == "BULLISH") == bullish
        if with_trend:
            return (_EVENTS.BOS_BULL if bullish else _EVENTS.BOS_BEAR), "BOS"
        return (
            (_EVENTS.CHOCH_BULL if bullish else _EVENTS.CHOCH_BEAR),
            "CHOCH",
        )

    def _roll_day(
        self,
        state: DailyBiasState,
        header: protocol.SeriesHeader,
        candle: market.Candle,
    ) -> tuple[
        DailyBiasState, tuple[structures.Aoi, ...], tuple[structures.Aoi, ...]
    ]:
        """Tracks the UTC day and publishes PDH/PDL on day changes.

        Args:
            state: Previous state.
            header: Advanced header.
            candle: Current candle.

        Returns:
            ``(state, updated_aois, ended_aois)``.
        """
        bar = ta.BarPoint.of(candle)
        day = bar.start_ms // _DAY_MS
        if state.day_index is None or day == state.day_index:
            high = (
                bar.high
                if state.day_high is None
                else max(state.day_high, bar.high)
            )
            low = (
                bar.low
                if state.day_low is None
                else min(state.day_low, bar.low)
            )
            return (
                dataclasses.replace(
                    state,
                    day_index=day,
                    day_high=high,
                    day_low=low,
                    day_start_ms=(
                        state.day_start_ms
                        if state.day_index is not None
                        else day * _DAY_MS
                    ),
                ),
                (),
                (),
            )
        consecutive = day == state.day_index + 1
        pdh = state.day_high if consecutive else None
        pdl = state.day_low if consecutive else None
        ended = tuple(
            self.end_aoi(aoi, header, structures.AoiState.EXPIRED)
            for aoi in state.day_aois
        )
        aois: list[structures.Aoi] = []
        prev_start = state.day_start_ms or 0
        for kind, level in (
            (structures.AoiKind.PDH, pdh),
            (structures.AoiKind.PDL, pdl),
        ):
            if level is None:
                continue
            aois.append(
                self.aoi(
                    header,
                    aoi_id=ids.stable_id(
                        kind.value.lower(),
                        header.instrument_id,
                        header.timeframe.value,
                        prev_start,
                    ),
                    kind=kind,
                    side=structures.ZoneSide.NEUTRAL,
                    lower=level,
                    upper=level,
                    created_at_ms=prev_start,
                    meta={"day_start_ms": prev_start},
                )
            )
        new_state = dataclasses.replace(
            state,
            day_index=day,
            day_high=bar.high,
            day_low=bar.low,
            day_start_ms=day * _DAY_MS,
            pdh=pdh,
            pdl=pdl,
            day_aois=tuple(aois),
        )
        return new_state, tuple(aois), ended

    def _publish(
        self, state: DailyBiasState
    ) -> tuple[Sequence[structures.Aoi], Mapping[str, structures.ValueType]]:
        """Publishes live AOIs and values.

        Args:
            state: Current state.

        Returns:
            ``(aois, values)``.
        """
        values: dict[str, structures.ValueType] = {
            "trend": state.trend,
            "last_event": state.last_event,
            "last_event_direction": state.last_event_direction,
            "last_pivot_high": (
                None if state.swing_high is None else state.swing_high.price
            ),
            "last_pivot_low": (
                None if state.swing_low is None else state.swing_low.price
            ),
            "pdh": state.pdh,
            "pdl": state.pdl,
            "session": state.session,
        }
        return state.day_aois, values
