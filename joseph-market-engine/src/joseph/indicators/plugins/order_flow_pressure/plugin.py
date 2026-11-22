"""Order-flow pressure estimated from lower-timeframe candle direction.

Clean-room implementation of V5 §6.2-E. The delta is an ESTIMATE from
intrabar candle direction (``method = "candle_direction"``); it is never
labeled as venue-measured taker delta. See README.md for assumptions.
"""

from __future__ import annotations

import dataclasses
import decimal
from collections.abc import Mapping
from collections.abc import Sequence

from joseph.domain import decimals
from joseph.domain import market
from joseph.domain import structures
from joseph.domain import timeframes
from joseph.indicators.sdk import base
from joseph.indicators.sdk import protocol
from joseph.indicators.sdk import settings as settings_lib
from joseph.indicators.sdk import ta

_EVENTS = structures.StructureEventName
_PCT = decimal.Decimal("0.01")
_INTRABAR_CHOICES = ("1m", "3m", "5m", "15m")


@dataclasses.dataclass(frozen=True, slots=True)
class FlowState:
    """Immutable plugin state.

    Attributes:
        header: Series bookkeeping.
        history: Previous bars (baseline window), oldest first.
        last_direction: Direction of the last classified intrabar
            (1 up, -1 down, 0 unknown) used for doji carry.
        delta_pct: Last bar's estimated delta percent.
        coverage_pct: Last bar's intrabar volume coverage percent.
        intrabar_count: Last bar's intrabar count.
        rel_volume: Last bar's relative volume.
    """

    header: protocol.SeriesHeader
    history: tuple[ta.BarPoint, ...] = ()
    last_direction: int = 0
    delta_pct: decimal.Decimal | None = None
    coverage_pct: decimal.Decimal | None = None
    intrabar_count: int = 0
    rel_volume: decimal.Decimal | None = None


def classify(
    intrabars: Sequence[market.Candle], last_direction: int
) -> tuple[decimal.Decimal, decimal.Decimal, decimal.Decimal, int]:
    """Splits intrabar volume into up/down by candle direction.

    Doji intrabars carry the previous direction; a doji with no prior
    direction is left unclassified.

    Args:
        intrabars: Lower-timeframe bars, oldest first.
        last_direction: Direction carried in from earlier bars.

    Returns:
        ``(up_volume, down_volume, total_volume, last_direction)``.
    """
    up = decimals.ZERO
    down = decimals.ZERO
    total = decimals.ZERO
    direction = last_direction
    for candle in intrabars:
        if candle.close > candle.open:
            direction = 1
        elif candle.close < candle.open:
            direction = -1
        total += candle.base_volume
        if direction > 0:
            up += candle.base_volume
        elif direction < 0:
            down += candle.base_volume
    return up, down, total, direction


class OrderFlowPressurePlugin(base.BasePlugin[FlowState]):
    """Buyer/seller failures and breakouts from estimated delta."""

    PLUGIN_ID = "order_flow_pressure"
    DISPLAY_NAME = "Order-flow pressure (estimated)"
    DESCRIPTION = (
        "Lower-timeframe candle-direction volume delta with relative "
        "volume, wick rejection and breakout confirmation."
    )
    TIMEFRAMES = tuple(
        tf
        for tf in timeframes.Timeframe
        if tf.duration_ms > timeframes.Timeframe.M1.duration_ms
    )
    OUTPUT_EVENTS = (
        _EVENTS.BUYER_FAILURE,
        _EVENTS.SELLER_FAILURE,
        _EVENTS.BUYER_BREAKOUT,
        _EVENTS.SELLER_BREAKOUT,
    )
    SETTINGS = (
        settings_lib.SettingSpec(
            "intrabar_tf",
            settings_lib.SettingKind.ENUM,
            "1m",
            "Lower timeframe used to estimate delta.",
            choices=_INTRABAR_CHOICES,
        ),
        settings_lib.SettingSpec(
            "min_intrabars",
            settings_lib.SettingKind.INT,
            5,
            "Minimum intrabars per bar.",
            minimum=1,
        ),
        settings_lib.SettingSpec(
            "min_coverage_pct",
            settings_lib.SettingKind.DECIMAL,
            decimal.Decimal(80),
            "Minimum intrabar volume coverage percent.",
            minimum=decimal.Decimal(0),
            maximum=decimal.Decimal(100),
        ),
        settings_lib.SettingSpec(
            "baseline_bars",
            settings_lib.SettingKind.INT,
            20,
            "Previous bars used for volume and range baselines.",
            minimum=1,
            maximum=500,
        ),
        settings_lib.SettingSpec(
            "rel_volume_min",
            settings_lib.SettingKind.DECIMAL,
            decimal.Decimal("1.5"),
            "Minimum relative volume.",
            minimum=decimal.Decimal(0),
        ),
        settings_lib.SettingSpec(
            "delta_min_pct",
            settings_lib.SettingKind.DECIMAL,
            decimal.Decimal(20),
            "Minimum absolute delta percent.",
            minimum=decimal.Decimal(0),
            maximum=decimal.Decimal(100),
        ),
        settings_lib.SettingSpec(
            "wick_reversal_pct",
            settings_lib.SettingKind.DECIMAL,
            decimal.Decimal(35),
            "Rejection wick as percent of range.",
            minimum=decimal.Decimal(0),
            maximum=decimal.Decimal(100),
        ),
        settings_lib.SettingSpec(
            "breakout_body_pct",
            settings_lib.SettingKind.DECIMAL,
            decimal.Decimal(50),
            "Breakout body as percent of range.",
            minimum=decimal.Decimal(0),
            maximum=decimal.Decimal(100),
        ),
        settings_lib.SettingSpec(
            "breakout_close_pct",
            settings_lib.SettingKind.DECIMAL,
            decimal.Decimal(25),
            "Maximum distance of the close from the extreme, percent of range.",
            minimum=decimal.Decimal(0),
            maximum=decimal.Decimal(100),
        ),
    )
    STATE_TYPES = (FlowState,)

    def warmup_for(
        self, settings: Mapping[str, settings_lib.SettingValue]
    ) -> int:
        """Returns bars needed for the baseline.

        Args:
            settings: Resolved settings.

        Returns:
            ``baseline_bars + 1``.
        """
        return settings_lib.get_int(settings, "baseline_bars") + 1

    def required_streams(
        self, settings: Mapping[str, object] | None
    ) -> frozenset[str]:
        """Returns chart bars plus the intrabar stream.

        Args:
            settings: Overrides.

        Returns:
            Stream names.
        """
        resolved = self.resolve_settings(settings)
        intrabar = timeframes.parse(
            settings_lib.get_str(resolved, "intrabar_tf")
        )
        return frozenset(
            {protocol.CHART_STREAM, protocol.intrabar_stream(intrabar)}
        )

    def _initial_state(
        self,
        header: protocol.SeriesHeader,
        settings: Mapping[str, settings_lib.SettingValue],
    ) -> FlowState:
        """Builds the initial state.

        Args:
            header: Fresh header.
            settings: Resolved settings.

        Returns:
            Initial state.

        Raises:
            ValueError: If the intrabar timeframe does not nest inside the
                series timeframe.
        """
        intrabar = timeframes.parse(
            settings_lib.get_str(settings, "intrabar_tf")
        )
        if not header.timeframe.contains(intrabar):
            raise ValueError(
                f"intrabar_tf {intrabar} must nest inside {header.timeframe}"
            )
        return FlowState(header=header)

    def _step(
        self,
        state: FlowState,
        header: protocol.SeriesHeader,
        bar: protocol.BarInput,
    ) -> base.StepResult[FlowState]:
        """Applies one closed bar.

        Args:
            state: Previous state.
            header: Advanced header.
            bar: Bar input with intrabars.

        Returns:
            Step result.
        """
        settings = header.settings_map()
        candle = bar.candle
        point = ta.BarPoint.of(candle)
        intrabar_tf = timeframes.parse(
            settings_lib.get_str(settings, "intrabar_tf")
        )
        intrabars = bar.intrabars.get(intrabar_tf, ())
        up, down, total, direction = classify(intrabars, state.last_direction)
        coverage = None
        if candle.base_volume > decimals.ZERO:
            coverage = min(
                total / candle.base_volume * decimals.HUNDRED, decimals.HUNDRED
            )
        classified = up + down
        delta = None
        enough = (
            len(intrabars) >= settings_lib.get_int(settings, "min_intrabars")
            and coverage is not None
            and coverage
            >= settings_lib.get_decimal(settings, "min_coverage_pct")
            and classified > decimals.ZERO
        )
        if enough:
            delta = ((up - down) / classified * decimals.HUNDRED).quantize(_PCT)
        baseline_n = settings_lib.get_int(settings, "baseline_bars")
        baseline = state.history[-baseline_n:]
        rel_volume = None
        if len(baseline) >= baseline_n:
            average = sum((b.volume for b in baseline), decimals.ZERO) / (
                decimal.Decimal(len(baseline))
            )
            if average > decimals.ZERO:
                rel_volume = (point.volume / average).quantize(_PCT)
        events = self._signals(
            point, baseline, delta, rel_volume, settings=settings, header=header
        )
        new_state = dataclasses.replace(
            state,
            history=(*state.history, point)[-baseline_n:],
            last_direction=direction,
            delta_pct=delta,
            coverage_pct=None if coverage is None else coverage.quantize(_PCT),
            intrabar_count=len(intrabars),
            rel_volume=rel_volume,
        )
        return base.StepResult(
            state=new_state,
            events=tuple(events),
            coverage_pct=(
                None
                if coverage is None
                else decimals.format_decimal(coverage.quantize(_PCT))
            ),
        )

    def _signals(
        self,
        bar: ta.BarPoint,
        baseline: tuple[ta.BarPoint, ...],
        delta: decimal.Decimal | None,
        rel_volume: decimal.Decimal | None,
        *,
        settings: Mapping[str, settings_lib.SettingValue],
        header: protocol.SeriesHeader,
    ) -> list[structures.StructureEvent]:
        """Evaluates the four pressure events on a closed bar.

        Args:
            bar: Current bar.
            baseline: Previous bars.
            delta: Estimated delta percent (``None`` = insufficient data).
            rel_volume: Relative volume (``None`` = insufficient history).
            settings: Resolved settings.
            header: Advanced header.

        Returns:
            Events confirmed on this bar.
        """
        if delta is None or rel_volume is None or bar.range <= decimals.ZERO:
            return []
        if rel_volume < settings_lib.get_decimal(settings, "rel_volume_min"):
            return []
        delta_min = settings_lib.get_decimal(settings, "delta_min_pct")
        wick_min = settings_lib.get_decimal(settings, "wick_reversal_pct")
        body_min = settings_lib.get_decimal(settings, "breakout_body_pct")
        close_max = settings_lib.get_decimal(settings, "breakout_close_pct")
        scale = decimals.HUNDRED / bar.range
        upper_wick = (bar.high - max(bar.open, bar.close)) * scale
        lower_wick = (min(bar.open, bar.close) - bar.low) * scale
        body = bar.body * scale
        prior_high = max((b.high for b in baseline), default=None)
        prior_low = min((b.low for b in baseline), default=None)
        names: list[structures.StructureEventName] = []
        if delta >= delta_min and upper_wick >= wick_min:
            names.append(_EVENTS.BUYER_FAILURE)
        if delta <= -delta_min and lower_wick >= wick_min:
            names.append(_EVENTS.SELLER_FAILURE)
        if (
            delta >= delta_min
            and bar.close > bar.open
            and body >= body_min
            and (bar.high - bar.close) * scale <= close_max
            and prior_high is not None
            and bar.close > prior_high
        ):
            names.append(_EVENTS.BUYER_BREAKOUT)
        if (
            delta <= -delta_min
            and bar.close < bar.open
            and body >= body_min
            and (bar.close - bar.low) * scale <= close_max
            and prior_low is not None
            and bar.close < prior_low
        ):
            names.append(_EVENTS.SELLER_BREAKOUT)
        return [
            self.event(
                header,
                name,
                price=bar.close,
                meta={
                    "delta_pct": delta,
                    "rel_volume": rel_volume,
                    "method": "candle_direction",
                },
            )
            for name in names
        ]

    def _publish(
        self, state: FlowState
    ) -> tuple[Sequence[structures.Aoi], Mapping[str, structures.ValueType]]:
        """Publishes values (this plugin has no AOIs).

        Args:
            state: Current state.

        Returns:
            ``([], values)``.
        """
        return [], {
            "delta_pct": state.delta_pct,
            "coverage_pct": state.coverage_pct,
            "intrabar_count": state.intrabar_count,
            "rel_volume": state.rel_volume,
            "method": "candle_direction",
        }
