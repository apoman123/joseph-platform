"""Dealing range with premium, discount, equilibrium and OTE zones.

Clean-room functional reimplementation of V5 §6.2-G written from the
specification text only (the reference Pine script carries a third-party
CC BY-NC-SA 4.0 notice, so no source was consulted). See README.md.
"""

from __future__ import annotations

import dataclasses
import decimal
from collections.abc import Mapping
from collections.abc import Sequence

from joseph.domain import ids
from joseph.domain import structures
from joseph.indicators.sdk import base
from joseph.indicators.sdk import protocol
from joseph.indicators.sdk import settings as settings_lib
from joseph.indicators.sdk import ta

_EVENTS = structures.StructureEventName
_KIND = structures.AoiKind
_SIDE = structures.ZoneSide
_STATE = structures.AoiState
_TWO = decimal.Decimal(2)


@dataclasses.dataclass(frozen=True, slots=True)
class Swing:
    """Latest confirmed swing.

    Attributes:
        price: Swing price.
        start_ms: Swing bar start.
        broken: True once broken (each swing resets the range once).
    """

    price: decimal.Decimal
    start_ms: int
    broken: bool = False


@dataclasses.dataclass(frozen=True, slots=True)
class DealingRange:
    """Current dealing range.

    Attributes:
        range_id: Stable id derived from the anchoring swings.
        direction: ``BULLISH`` (leg up) or ``BEARISH`` (leg down).
        high: Range high (extends with the leg).
        low: Range low (extends with the leg).
        created_at_ms: Start of the earlier anchoring swing bar.
    """

    range_id: str
    direction: str
    high: decimal.Decimal
    low: decimal.Decimal
    created_at_ms: int

    @property
    def eq(self) -> decimal.Decimal:
        """Returns the 50% equilibrium."""
        return ta.quantize_price((self.high + self.low) / _TWO)

    def ote(
        self, low_fib: decimal.Decimal, high_fib: decimal.Decimal
    ) -> tuple[decimal.Decimal, decimal.Decimal]:
        """Returns the OTE band oriented by the leg direction.

        A bullish leg retraces down from the high, so the band sits in the
        discount half; a bearish leg retraces up from the low.

        Args:
            low_fib: Shallow retracement, e.g. ``0.62``.
            high_fib: Deep retracement, e.g. ``0.79``.

        Returns:
            ``(lower, upper)`` prices.
        """
        span = self.high - self.low
        if self.direction == "BULLISH":
            return (
                ta.quantize_price(self.high - span * high_fib),
                ta.quantize_price(self.high - span * low_fib),
            )
        return (
            ta.quantize_price(self.low + span * low_fib),
            ta.quantize_price(self.low + span * high_fib),
        )


@dataclasses.dataclass(frozen=True, slots=True)
class PdState:
    """Immutable plugin state.

    Attributes:
        header: Series bookkeeping.
        pivots: Swing tracker.
        swing_high: Latest swing high.
        swing_low: Latest swing low.
        dealing_range: Current range, if any.
        aois: Published zone AOIs of the current range.
        last_close: Close of the last bar.
    """

    header: protocol.SeriesHeader
    pivots: ta.PivotTracker
    swing_high: Swing | None = None
    swing_low: Swing | None = None
    dealing_range: DealingRange | None = None
    aois: tuple[structures.Aoi, ...] = ()
    last_close: decimal.Decimal | None = None


class PremiumDiscountOtePlugin(base.BasePlugin[PdState]):
    """Premium/discount/OTE zones of the current dealing range."""

    PLUGIN_ID = "premium_discount_ote"
    DISPLAY_NAME = "Premium / discount / OTE"
    DESCRIPTION = (
        "Dealing range from confirmed swings with equilibrium, premium and "
        "discount halves and the 62-79% optimal trade entry band."
    )
    OUTPUT_KINDS = (
        _KIND.PREMIUM,
        _KIND.DISCOUNT,
        _KIND.EQUILIBRIUM,
        _KIND.OTE,
    )
    OUTPUT_EVENTS = (_EVENTS.RANGE_RESET,)
    SETTINGS = (
        settings_lib.SettingSpec(
            "swing_length",
            settings_lib.SettingKind.INT,
            10,
            "Bars on each side of a swing.",
            minimum=1,
            maximum=100,
        ),
        settings_lib.SettingSpec(
            "break_mode",
            settings_lib.SettingKind.ENUM,
            "CLOSE",
            "Whether a close or a wick beyond a swing resets the range.",
            choices=("CLOSE", "WICK"),
        ),
        settings_lib.SettingSpec(
            "ote_low",
            settings_lib.SettingKind.DECIMAL,
            decimal.Decimal("0.62"),
            "Shallow OTE retracement.",
            minimum=decimal.Decimal(0),
            maximum=decimal.Decimal(1),
        ),
        settings_lib.SettingSpec(
            "ote_high",
            settings_lib.SettingKind.DECIMAL,
            decimal.Decimal("0.79"),
            "Deep OTE retracement.",
            minimum=decimal.Decimal(0),
            maximum=decimal.Decimal(1),
        ),
    )
    STATE_TYPES = (PdState, Swing, DealingRange)

    def warmup_for(
        self, settings: Mapping[str, settings_lib.SettingValue]
    ) -> int:
        """Returns bars needed for the first swing to confirm.

        Args:
            settings: Resolved settings.

        Returns:
            ``2 * swing_length + 1``.
        """
        return 2 * settings_lib.get_int(settings, "swing_length") + 1

    def _initial_state(
        self,
        header: protocol.SeriesHeader,
        settings: Mapping[str, settings_lib.SettingValue],
    ) -> PdState:
        """Builds the initial state.

        Args:
            header: Fresh header.
            settings: Resolved settings.

        Returns:
            Initial state.

        Raises:
            ValueError: If ``ote_low > ote_high``.
        """
        if settings_lib.get_decimal(settings, "ote_low") > (
            settings_lib.get_decimal(settings, "ote_high")
        ):
            raise ValueError("ote_low must be <= ote_high")
        length = settings_lib.get_int(settings, "swing_length")
        return PdState(
            header=header, pivots=ta.PivotTracker(left=length, right=length)
        )

    def _step(
        self,
        state: PdState,
        header: protocol.SeriesHeader,
        bar: protocol.BarInput,
    ) -> base.StepResult[PdState]:
        """Applies one closed bar.

        Args:
            state: Previous state.
            header: Advanced header.
            bar: Bar input.

        Returns:
            Step result.
        """
        settings = header.settings_map()
        point = ta.BarPoint.of(bar.candle)
        pivots, pivot_high, pivot_low = state.pivots.update(point)
        swing_high = state.swing_high
        swing_low = state.swing_low
        if pivot_high is not None:
            swing_high = Swing(pivot_high.price, pivot_high.start_ms)
        if pivot_low is not None:
            swing_low = Swing(pivot_low.price, pivot_low.start_ms)

        breaks_up, breaks_down = self._breaks(
            point,
            swing_high,
            swing_low,
            wick=settings_lib.get_str(settings, "break_mode") == "WICK",
        )
        dealing = state.dealing_range
        events: list[structures.StructureEvent] = []
        reset = False
        candidate: DealingRange | None = None
        if breaks_up and swing_high is not None:
            swing_high = dataclasses.replace(swing_high, broken=True)
            if swing_low is not None:
                candidate = self._new_range(
                    header,
                    "BULLISH",
                    swing_low,
                    swing_high,
                    high=max(point.high, swing_high.price),
                    low=swing_low.price,
                )
        elif breaks_down and swing_low is not None:
            swing_low = dataclasses.replace(swing_low, broken=True)
            if swing_high is not None:
                candidate = self._new_range(
                    header,
                    "BEARISH",
                    swing_low,
                    swing_high,
                    high=swing_high.price,
                    low=min(point.low, swing_low.price),
                )
        # ASSUMPTION: stale anchors that do not span a valid range
        # (high <= low) do not reset the dealing range.
        if candidate is not None and candidate.high > candidate.low:
            dealing = candidate
            reset = True

        extended = False
        if not reset and dealing is not None:
            if dealing.direction == "BULLISH" and point.high > dealing.high:
                dealing = dataclasses.replace(dealing, high=point.high)
                extended = True
            elif dealing.direction == "BEARISH" and point.low < dealing.low:
                dealing = dataclasses.replace(dealing, low=point.low)
                extended = True

        aois = state.aois
        updated: tuple[structures.Aoi, ...] = ()
        ended: tuple[structures.Aoi, ...] = ()
        if reset and dealing is not None:
            ended = tuple(
                self.end_aoi(aoi, header, _STATE.INVALIDATED)
                for aoi in state.aois
            )
            previous = state.dealing_range
            events.append(
                self.event(
                    header,
                    _EVENTS.RANGE_RESET,
                    aoi_id=None,
                    price=dealing.eq,
                    meta={
                        "range_id": dealing.range_id,
                        "direction": dealing.direction,
                        "previous_range_id": (
                            None if previous is None else previous.range_id
                        ),
                    },
                )
            )
        if (reset or extended) and dealing is not None:
            aois = self._zone_aois(header, dealing, settings)
            updated = aois
        new_state = dataclasses.replace(
            state,
            pivots=pivots,
            swing_high=swing_high,
            swing_low=swing_low,
            dealing_range=dealing,
            aois=aois,
            last_close=point.close,
        )
        return base.StepResult(
            state=new_state,
            events=tuple(events),
            updated=updated,
            ended=ended,
        )

    @staticmethod
    def _breaks(
        point: ta.BarPoint,
        swing_high: Swing | None,
        swing_low: Swing | None,
        *,
        wick: bool,
    ) -> tuple[bool, bool]:
        """Detects breaks of the latest unbroken swings.

        Args:
            point: Current bar.
            swing_high: Latest swing high.
            swing_low: Latest swing low.
            wick: Use wicks (True) or closes (False).

        Returns:
            ``(breaks_up, breaks_down)``; an outside bar breaking both
            resolves by its body direction (ASSUMPTION).
        """
        up_level = point.high if wick else point.close
        down_level = point.low if wick else point.close
        breaks_up = (
            swing_high is not None
            and not swing_high.broken
            and up_level > swing_high.price
        )
        breaks_down = (
            swing_low is not None
            and not swing_low.broken
            and down_level < swing_low.price
        )
        if breaks_up and breaks_down:
            breaks_up = point.close >= point.open
            breaks_down = not breaks_up
        return breaks_up, breaks_down

    def _new_range(
        self,
        header: protocol.SeriesHeader,
        direction: str,
        swing_low: Swing,
        swing_high: Swing,
        *,
        high: decimal.Decimal,
        low: decimal.Decimal,
    ) -> DealingRange:
        """Creates a dealing range anchored on two swings.

        Args:
            header: Advanced header.
            direction: Leg direction.
            swing_low: Anchoring swing low.
            swing_high: Anchoring swing high.
            high: Range high.
            low: Range low.

        Returns:
            The range with a deterministic id.
        """
        return DealingRange(
            range_id=ids.stable_id(
                "pdr",
                header.instrument_id,
                header.timeframe.value,
                direction,
                swing_low.start_ms,
                swing_high.start_ms,
            ),
            direction=direction,
            high=high,
            low=low,
            created_at_ms=min(swing_low.start_ms, swing_high.start_ms),
        )

    def _zone_aois(
        self,
        header: protocol.SeriesHeader,
        dealing: DealingRange,
        settings: Mapping[str, settings_lib.SettingValue],
    ) -> tuple[structures.Aoi, ...]:
        """Builds the four zone AOIs of a range.

        Args:
            header: Advanced header.
            dealing: Current range.
            settings: Resolved settings.

        Returns:
            PREMIUM, DISCOUNT, EQUILIBRIUM and OTE AOIs.
        """
        eq = dealing.eq
        ote_lower, ote_upper = dealing.ote(
            settings_lib.get_decimal(settings, "ote_low"),
            settings_lib.get_decimal(settings, "ote_high"),
        )
        ote_side = (
            _SIDE.BULLISH if dealing.direction == "BULLISH" else _SIDE.BEARISH
        )
        zones = (
            (_KIND.PREMIUM, _SIDE.BEARISH, eq, dealing.high),
            (_KIND.DISCOUNT, _SIDE.BULLISH, dealing.low, eq),
            (_KIND.EQUILIBRIUM, _SIDE.NEUTRAL, eq, eq),
            (_KIND.OTE, ote_side, ote_lower, ote_upper),
        )
        return tuple(
            self.aoi(
                header,
                aoi_id=ids.stable_id("pd", dealing.range_id, kind.value),
                kind=kind,
                side=side,
                lower=lower,
                upper=upper,
                created_at_ms=dealing.created_at_ms,
                range_id=dealing.range_id,
                meta={
                    "direction": dealing.direction,
                    "range_high": dealing.high,
                    "range_low": dealing.low,
                },
            )
            for kind, side, lower, upper in zones
        )

    def _publish(
        self, state: PdState
    ) -> tuple[Sequence[structures.Aoi], Mapping[str, structures.ValueType]]:
        """Publishes live AOIs and values.

        Args:
            state: Current state.

        Returns:
            ``(aois, values)``.
        """
        dealing = state.dealing_range
        close = state.last_close
        if dealing is None or close is None:
            return state.aois, {
                "range_id": None,
                "direction": None,
                "range_high": None,
                "range_low": None,
                "eq": None,
                "ote_lower": None,
                "ote_upper": None,
                "in_premium": None,
                "in_discount": None,
                "in_ote": None,
            }
        settings = state.header.settings_map()
        eq = dealing.eq
        ote_lower, ote_upper = dealing.ote(
            settings_lib.get_decimal(settings, "ote_low"),
            settings_lib.get_decimal(settings, "ote_high"),
        )
        return state.aois, {
            "range_id": dealing.range_id,
            "direction": dealing.direction,
            "range_high": dealing.high,
            "range_low": dealing.low,
            "eq": eq,
            "ote_lower": ote_lower,
            "ote_upper": ote_upper,
            "in_premium": eq < close <= dealing.high,
            "in_discount": dealing.low <= close < eq,
            "in_ote": ote_lower <= close <= ote_upper,
        }
