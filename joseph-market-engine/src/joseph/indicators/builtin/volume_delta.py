# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Volume delta: buy minus sell volume of each bar, as a percentage.

A clean-room implementation of TradingView's published Volume Delta method.

* True delta (``delta_source = "auto"`` and the bar carries a buy volume):
  ``buy`` is the venue's taker buy volume, ``sell = volume - buy`` and the
  coverage is 100 %. ``method`` is ``taker_volume`` (venue aggressor flag,
  snapshot method ``trade``) or ``trade_classification`` (trades classified
  against the quote where the market publishes no aggressor side, snapshot
  method ``trade_classification``).
* Estimate (``method`` ``candle_direction``, otherwise and always with
  ``delta_source = "estimate"``): each intrabar is up or down by
  ``sign(close - open)``; a flat intrabar takes ``sign(close - previous
  intrabar close)``, and when that is flat too it keeps the previous
  direction. The previous close and direction carry across chart bars.
  ``buy`` sums the volume of up intrabars, ``sell`` that of down intrabars
  and ``covered`` the volume of all intrabars. The estimate is published
  only when there are at least ``min_intrabars`` intrabars, they cover at
  least ``min_coverage`` of a positive chart volume; otherwise the delta
  values are null while ``coverage_pct`` and ``intrabar_count`` say why.

``delta_pct = (buy - sell) / covered * 100`` (``covered`` is the bar volume
on the true path) and ``coverage_pct = covered / volume * 100`` are computed
in doubles and published with two decimals; volumes with eight.
"""

from __future__ import annotations

import dataclasses
import decimal
from collections.abc import Mapping
from collections.abc import Sequence

from joseph.domain import decimals
from joseph.domain import market
from joseph.domain import structures
from joseph.domain import timeframes as tfs
from joseph.indicators.builtin import _series
from joseph.indicators.sdk import base
from joseph.indicators.sdk import metadata
from joseph.indicators.sdk import protocol
from joseph.indicators.sdk import settings as settings_lib

TAKER_VOLUME = "taker_volume"
"""``method`` of true delta from the venue's aggressor flag."""

TRADE_CLASSIFICATION = "trade_classification"
"""``method`` of buy volume classified from trades against the quote."""

CANDLE_DIRECTION = "candle_direction"
"""``method`` of the intrabar candle-direction estimate."""

INTRABAR_AUTO = "auto"
"""``intrabar_tf`` choice resolved per chart timeframe."""

_NO_AUTO_INTRABARS = frozenset({tfs.Timeframe.S1, tfs.Timeframe.M1})
"""Charts on which ``intrabar_tf = auto`` reads no intrabars."""

_DAILY_AUTO_INTRABARS: Mapping[tfs.Timeframe, tfs.Timeframe] = {
    tfs.Timeframe.D1: tfs.Timeframe.M5,
}
"""Daily-and-above ``auto`` intrabars other than the 1h default."""

_VALUE_KEYS = (
    "delta_pct",
    "delta",
    "buy_volume",
    "sell_volume",
    "method",
    "coverage_pct",
    "intrabar_count",
)
"""Published value keys, in publication order."""


@dataclasses.dataclass(frozen=True, slots=True)
class VolumeDeltaState:
    """State of the volume delta study.

    Attributes:
        header: Series bookkeeping.
        last_close: Close of the last intrabar seen (``None`` before one).
        last_direction: Direction of the last intrabar: 1 up, -1 down, 0
            unknown.
        values: Values published for the last bar, as ``(key, value)``
            pairs.
    """

    header: protocol.SeriesHeader
    last_close: float | None
    last_direction: int
    values: tuple[tuple[str, structures.ValueType], ...]


@dataclasses.dataclass(frozen=True, slots=True)
class _Estimate:
    """Intrabar estimate of one chart bar.

    Attributes:
        buy: Volume of up intrabars.
        sell: Volume of down intrabars.
        covered: Volume of all intrabars.
        count: Number of intrabars.
        last_close: Close of the last intrabar (carried to the next bar).
        last_direction: Direction of the last intrabar (carried too).
    """

    buy: float
    sell: float
    covered: float
    count: int
    last_close: float | None
    last_direction: int


def _sign(value: float) -> int:
    """Returns the sign of a double.

    Args:
        value: Number.

    Returns:
        1, -1 or 0.
    """
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0


def estimate(
    intrabars: Sequence[market.Candle],
    last_close: float | None,
    last_direction: int,
) -> _Estimate:
    """Splits intrabar volume into buys and sells by candle direction.

    Args:
        intrabars: Intrabars of the chart bar, oldest first.
        last_close: Close of the intrabar before the first one, if any.
        last_direction: Direction of the intrabar before the first one.

    Returns:
        Buy, sell and covered volume plus the carried close and direction.
    """
    buy = 0.0
    sell = 0.0
    covered = 0.0
    previous_close = last_close
    direction = last_direction
    for intrabar in intrabars:
        close = float(intrabar.close)
        volume = float(intrabar.base_volume)
        current = _sign(close - float(intrabar.open))
        if current == 0 and previous_close is not None:
            current = _sign(close - previous_close)
        if current != 0:
            direction = current
        if direction > 0:
            buy += volume
        elif direction < 0:
            sell += volume
        covered += volume
        previous_close = close
    return _Estimate(
        buy=buy,
        sell=sell,
        covered=covered,
        count=len(intrabars),
        last_close=previous_close,
        last_direction=direction,
    )


def _percent(numerator: float, denominator: float) -> float | None:
    """Returns ``numerator / denominator * 100``.

    Args:
        numerator: Dividend.
        denominator: Divisor.

    Returns:
        The percentage, or ``None`` when the divisor is not positive.
    """
    if denominator <= 0:
        return None
    return numerator / denominator * 100


class VolumeDeltaPlugin(base.BasePlugin[VolumeDeltaState]):
    """Volume delta of each bar, true where possible, else estimated.

    Attributes:
        PLUGIN_ID: ``"volume_delta"``.
        DISPLAY_NAME: Human-readable name.
        DESCRIPTION: One-sentence description.
        OUTPUT_VALUES: Delta share, delta, buy and sell volume, method,
            coverage and intrabar count.
        DISPLAY: A ``Delta %`` histogram pane, visible by default.
        SETTINGS: Delta source, intrabar timeframe and the minimum intrabar
            count and coverage of the estimate.
        STATE_TYPES: The state dataclass.
    """

    PLUGIN_ID = "volume_delta"
    DISPLAY_NAME = "Volume delta"
    DESCRIPTION = (
        "Buy minus sell volume of each bar as a percentage of its volume: "
        "venue taker volume or classified trades when available, otherwise "
        "an estimate from intrabar candle direction."
    )
    OUTPUT_VALUES = (
        metadata.ValueSpec(
            "delta_pct",
            metadata.ValueType.DECIMAL,
            "Delta as a percentage of the covered volume.",
            metadata.Unit.PERCENT,
        ),
        metadata.ValueSpec(
            "delta",
            metadata.ValueType.DECIMAL,
            "Buy volume minus sell volume.",
            metadata.Unit.VOLUME,
        ),
        metadata.ValueSpec(
            "buy_volume",
            metadata.ValueType.DECIMAL,
            "Buy volume.",
            metadata.Unit.VOLUME,
        ),
        metadata.ValueSpec(
            "sell_volume",
            metadata.ValueType.DECIMAL,
            "Sell volume.",
            metadata.Unit.VOLUME,
        ),
        metadata.ValueSpec(
            "method",
            metadata.ValueType.STRING,
            "How buy and sell volume were obtained.",
            metadata.Unit.NONE,
            choices=(TAKER_VOLUME, TRADE_CLASSIFICATION, CANDLE_DIRECTION),
        ),
        metadata.ValueSpec(
            "coverage_pct",
            metadata.ValueType.DECIMAL,
            "Share of the bar volume the method covered.",
            metadata.Unit.PERCENT,
        ),
        metadata.ValueSpec(
            "intrabar_count",
            metadata.ValueType.INTEGER,
            "Intrabars used by the estimate.",
            metadata.Unit.NONE,
        ),
    )
    DISPLAY = metadata.Display(
        default_visible=True,
        z_index=70,
        plots=(
            metadata.PlotStyle(
                "delta_pct",
                "Delta %",
                pane="delta",
                render="histogram",
                tone=metadata.Tone.SIGN,
            ),
        ),
        panes=(
            metadata.PaneStyle(
                "delta", "Delta %", levels=(metadata.PaneLevel(0, "0"),)
            ),
        ),
    )
    SETTINGS = (
        settings_lib.SettingSpec(
            "delta_source",
            settings_lib.SettingKind.ENUM,
            "auto",
            "auto: true delta from venue taker volume when available, else "
            "the estimate; estimate: always the intrabar estimate.",
            choices=("auto", "estimate"),
        ),
        settings_lib.SettingSpec(
            "intrabar_tf",
            settings_lib.SettingKind.ENUM,
            INTRABAR_AUTO,
            "Lower timeframe used for the estimate.",
            choices=(INTRABAR_AUTO, "1s", "5s", "15s", "1m", "3m", "5m", "15m"),
        ),
        settings_lib.SettingSpec(
            "min_intrabars",
            settings_lib.SettingKind.INT,
            5,
            "Intrabars required.",
            minimum=1,
            maximum=100,
        ),
        settings_lib.SettingSpec(
            "min_coverage",
            settings_lib.SettingKind.DECIMAL,
            decimal.Decimal("0.80"),
            "Share of chart volume the intrabars must cover.",
            minimum=decimal.Decimal("0.50"),
            maximum=decimal.Decimal("1"),
        ),
    )
    STATE_TYPES = (VolumeDeltaState,)

    def intrabar_timeframe(
        self,
        settings: Mapping[str, settings_lib.SettingValue],
        timeframe: tfs.Timeframe | None,
    ) -> tfs.Timeframe | None:
        """Resolves the intrabar timeframe for a chart timeframe.

        ``auto`` follows TradingView's volume-delta rule: 1s on seconds
        charts, 1m intraday, 5m on daily and 1h on weekly and monthly
        charts. 1s and 1m charts have no practical lower timeframe, so
        ``auto`` reads no intrabars there and only true delta is published.

        Args:
            settings: Resolved settings.
            timeframe: Chart timeframe, if known.

        Returns:
            The intrabar timeframe, or ``None`` when there is none.
        """
        choice = settings_lib.get_str(settings, "intrabar_tf")
        if choice != INTRABAR_AUTO:
            return tfs.parse(choice)
        if timeframe is None:
            return tfs.Timeframe.M1
        if timeframe in _NO_AUTO_INTRABARS:
            return None
        if timeframe.is_seconds:
            return tfs.Timeframe.S1
        if timeframe.duration_ms < tfs.Timeframe.D1.duration_ms:
            return tfs.Timeframe.M1
        return _DAILY_AUTO_INTRABARS.get(timeframe, tfs.Timeframe.H1)

    def required_streams(
        self,
        settings: Mapping[str, object] | None,
        *,
        timeframe: tfs.Timeframe | None = None,
    ) -> frozenset[str]:
        """Chart bars plus intrabars of the resolved lower timeframe.

        Args:
            settings: Overrides.
            timeframe: Chart timeframe, when known.

        Returns:
            Stream names.
        """
        resolved = self.resolve_settings(settings)
        intrabar = self.intrabar_timeframe(resolved, timeframe)
        if intrabar is None:
            return frozenset({protocol.CHART_STREAM})
        return frozenset(
            {protocol.CHART_STREAM, protocol.intrabar_stream(intrabar)}
        )

    def snapshot_method(
        self, values: Mapping[str, structures.ValueType]
    ) -> structures.IndicatorMethod:
        """Labels trade-derived bars, never as TradingView parity.

        Args:
            values: Published values.

        Returns:
            ``trade`` for taker volume, ``trade_classification`` for
            classified buy volume, else ``METHOD``.
        """
        method = values.get("method")
        if method == TAKER_VOLUME:
            return structures.IndicatorMethod.TRADE
        if method == TRADE_CLASSIFICATION:
            return structures.IndicatorMethod.TRADE_CLASSIFICATION
        return self.METHOD

    def _initial_state(
        self,
        header: protocol.SeriesHeader,
        settings: Mapping[str, settings_lib.SettingValue],
    ) -> VolumeDeltaState:
        """Builds the empty state.

        Args:
            header: Fresh header.
            settings: Resolved settings.

        Returns:
            State before any bar.
        """
        del settings  # Unused.
        return VolumeDeltaState(
            header=header, last_close=None, last_direction=0, values=()
        )

    def _step(
        self,
        state: VolumeDeltaState,
        header: protocol.SeriesHeader,
        bar: protocol.BarInput,
    ) -> base.StepResult[VolumeDeltaState]:
        """Computes the bar's delta.

        Args:
            state: Previous state.
            header: Header advanced to this bar.
            bar: Bar input.

        Returns:
            The new state and the bar's coverage for data quality.
        """
        settings = header.settings_map()
        candle = bar.candle
        intrabar_tf = self.intrabar_timeframe(settings, candle.timeframe)
        intrabars = (
            bar.intrabars.get(intrabar_tf, ())
            if intrabar_tf is not None
            else ()
        )
        split = estimate(intrabars, state.last_close, state.last_direction)
        volume = float(candle.base_volume)
        taker = candle.taker_buy_base_volume
        auto = settings_lib.get_str(settings, "delta_source") == "auto"
        if auto and taker is not None:
            values = self._true_values(candle, float(taker), volume)
        else:
            values = self._estimate_values(split, volume, settings)
        coverage = values["coverage_pct"]
        return base.StepResult(
            state=dataclasses.replace(
                state,
                last_close=split.last_close,
                last_direction=split.last_direction,
                values=tuple(values.items()),
            ),
            coverage_pct=(
                decimals.format_decimal(coverage)
                if isinstance(coverage, decimal.Decimal)
                else None
            ),
        )

    def _true_values(
        self, candle: market.Candle, buy: float, volume: float
    ) -> dict[str, structures.ValueType]:
        """Publishes true delta from the bar's buy volume.

        Args:
            candle: Chart bar.
            buy: Its buy volume.
            volume: Its total volume.

        Returns:
            Published values.
        """
        sell = volume - buy
        method = TAKER_VOLUME
        if candle.buy_volume_method is market.BuyVolumeMethod.CLASSIFIED:
            method = TRADE_CLASSIFICATION
        return {
            "delta_pct": _series.publish_ratio(_percent(buy - sell, volume), 2),
            "delta": _series.publish_quantity(buy - sell),
            "buy_volume": _series.publish_quantity(buy),
            "sell_volume": _series.publish_quantity(sell),
            "method": method,
            "coverage_pct": _series.publish_ratio(100.0, 2),
            "intrabar_count": 0,
        }

    def _estimate_values(
        self,
        split: _Estimate,
        volume: float,
        settings: Mapping[str, settings_lib.SettingValue],
    ) -> dict[str, structures.ValueType]:
        """Publishes the intrabar estimate when it is sufficient.

        Args:
            split: Intrabar split of the bar.
            volume: Chart bar volume.
            settings: Resolved settings.

        Returns:
            Published values; the delta values are null when the intrabars
            are too few or cover too little of the bar volume.
        """
        coverage = split.covered / volume if volume > 0 else None
        minimum = float(settings_lib.get_decimal(settings, "min_coverage"))
        sufficient = (
            coverage is not None
            and split.count >= settings_lib.get_int(settings, "min_intrabars")
            and coverage >= minimum
        )
        values: dict[str, structures.ValueType] = dict.fromkeys(_VALUE_KEYS)
        if sufficient:
            values["delta_pct"] = _series.publish_ratio(
                _percent(split.buy - split.sell, split.covered), 2
            )
            values["delta"] = _series.publish_quantity(split.buy - split.sell)
            values["buy_volume"] = _series.publish_quantity(split.buy)
            values["sell_volume"] = _series.publish_quantity(split.sell)
        values["method"] = CANDLE_DIRECTION
        values["coverage_pct"] = _series.publish_ratio(
            None if coverage is None else coverage * 100, 2
        )
        values["intrabar_count"] = split.count
        return values

    def _publish(
        self, state: VolumeDeltaState
    ) -> tuple[Sequence[structures.Aoi], Mapping[str, structures.ValueType]]:
        """Publishes the last bar's values.

        Args:
            state: Current state.

        Returns:
            No AOIs; the delta values (all null before the first bar).
        """
        return (), dict.fromkeys(_VALUE_KEYS) | dict(state.values)
