# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Container-only Python script SDK and deterministic simulated broker.

The application never imports user source through this module. Its image entry
point receives a bounded JSON bundle, runs it without host mounts or network,
and returns plots and simulated fills. Indicators cannot submit orders.
"""

from __future__ import annotations

import base64
import builtins
import contextlib
import gzip
import inspect
import io
import json
import math
import os
import sys
import types
from collections.abc import Callable
from collections.abc import Mapping
from typing import Any

import joseph_script

OPTIONAL_FIELDS = frozenset(
    {"quote_turnover", "trade_count", "taker_buy_volume"}
)
"""Optional numeric bar fields; absent values stay None, never zero."""


def _sign(value: float) -> int:
    """Classifies a signed position.

    Args:
        value: Signed quantity.

    Returns:
        1 for long, -1 for short and 0 for flat.
    """
    return (value > 0) - (value < 0)


class Context:
    """Exposes closed-bar history, plots, state.

    Exposes closed-bar history, plots, state and a simulated strategy account.

    Attributes:
        kind: Indicator or strategy role; order methods require strategy.
        inputs: JSON input values fixed for this run.
        state: Mutable user dictionary retained across bars of this run.
        history: Closed OHLCV dictionaries visible through the current bar.
        plots: Named scalar series with UTC millisecond timestamps.
        fills: Simulated next-bar-open fills; never forwarded to execution.
        equity_curve: Simulated marked equity at each closed bar.
        position_size: Signed simulated base-asset quantity.
        cash: Simulated quote-asset cash balance.
        pending: Pending side/quantity intents filled on the next bar's open.
        orders: Every queued strategy order in bar order, with its protective
            stop/target, comment and outcome (pending, filled, rejected or
            unchanged); the host turns orders queued on the latest closed
            bar of an activation into live signals.
        position_stop: Protective stop of the open simulated position, if
            its entry declared one.
        position_target: Profit target of the open simulated position, if
            its entry declared one.
        commission_bps: Per-fill commission in basis points.
        slippage_bps: Adverse per-fill price adjustment in basis points.
        overlay: Whether plots belong on the price pane.
        instrument_id: Canonical instrument currently being evaluated.
        timeframe: Current source resolution.
        alerts: Matched plain-text condition events, bounded to source bars.
        visuals: Declarative drawing objects keyed by stable script-local ids.
        plot_styles: Per-series visual settings.
        rejected_orders: Number of simulated orders rejected for insufficient
            equity.
        snapshot: Actual available market metrics for a filter candidate.
        instrument: Source instrument metadata; absent fields remain
            unavailable.
        extra_series: Declared additional symbol/resolution histories.
        bar_visuals: Separately bounded per-bar symbols, colors and OHLC plots.
        total_bars: Number of source bars in the current isolated run.
        periods: Host-computed [start, next start] calendar period bounds
            per source bar, by period resolution.
    """

    def __init__(self, bundle: dict[str, Any]) -> None:
        """Initializes a fresh isolated run.

        Initializes a fresh isolated run without side effects outside the
        process.

        Args:
            bundle: Host-validated source, role, inputs and simulation settings.
        """
        self.kind = str(bundle["kind"])
        self.total_bars = len(bundle.get("bars", []))
        self.inputs: dict[str, Any] = dict(bundle.get("inputs", {}))
        self.state: dict[str, Any] = {}
        self.history: list[dict[str, Any]] = []
        self.plots: dict[str, list[dict[str, Any]]] = {}
        self.fills: list[dict[str, Any]] = []
        self.equity_curve: list[dict[str, Any]] = []
        self.position_size = 0.0
        self.cash = float(bundle.get("initial_equity", 10000))
        self.pending: list[tuple[str, float]] = []
        self.orders: list[dict[str, Any]] = []
        self.position_stop: float | None = None
        self.position_target: float | None = None
        self.commission_bps = float(bundle.get("commission_bps", 0))
        self.slippage_bps = float(bundle.get("slippage_bps", 0))
        self.overlay = bool(bundle.get("overlay", True))
        self.instrument_id = str(bundle.get("instrument_id", ""))
        self.timeframe = str(bundle.get("timeframe", ""))
        self.alerts: list[dict[str, Any]] = []
        self.visuals: dict[str, dict[str, Any]] = {}
        self.plot_styles: dict[str, dict[str, Any]] = {}
        self.rejected_orders = 0
        self.snapshot: dict[str, Any] = dict(bundle.get("snapshot", {}))
        self.instrument: dict[str, Any] = dict(bundle.get("instrument", {}))
        self.extra_series: dict[str, list[dict[str, Any]]] = dict(
            bundle.get("extra_series", {})
        )
        self.bar_visuals: dict[str, dict[str, Any]] = {}
        self.periods: dict[str, list[list[int]]] = dict(
            bundle.get("periods", {})
        )
        self._cursors: dict[str, int] = {}
        self._closed: dict[str, int] = {}

    @property
    def bar(self) -> dict[str, Any]:
        """The current visible OHLCV bar, without future-bar access.

        Returns:
            Current bar with time_ms/open/high/low/close/volume fields.
        """
        return self.history[-1]

    @property
    def equity(self) -> float:
        """Marks the simulated account at the current bar's close.

        Returns:
            Quote-asset cash plus marked signed position value.
        """
        return self.cash + self.position_size * float(self.bar["close"])

    @property
    def bar_index(self) -> int:
        """The zero-based index of the currently visible source bar.

        Returns:
            Visible history length minus one; minus one before any bar exists.
        """
        return len(self.history) - 1

    @property
    def is_first(self) -> bool:
        """Whether this is the first visible source bar.

        Returns:
            True only at zero-based bar index zero.
        """
        return self.bar_index == 0

    @property
    def is_last(self) -> bool:
        """Whether this is the final currently available source bar.

        Returns:
            True on the last confirmed or explicitly requested provisional bar.
        """
        return len(self.history) == self.total_bars

    @property
    def is_confirmed(self) -> bool:
        """Whether the current source bar is closed and confirmed.

        Returns:
            Actual source finality, never inferred from an unfinished price bar.
        """
        return bool(self.bar.get("is_final", True))

    def request(
        self, alias: str, field: str = "close", *, intrabars: bool = False
    ) -> list[float]:
        """Reads a declared other timeframe/symbol without future-bar leakage.

        Args:
            alias: Source alias declared in the immutable script version.
            field: OHLCV numeric field, default close.
            intrabars: Restrict to closed lower-timeframe bars inside this bar.

        Returns:
            Values of source bars already closed at the current bar's close.

        Raises:
            ValueError: If the alias or requested field was not declared.
        """
        if alias not in self.extra_series or field not in {
            "open",
            "high",
            "low",
            "close",
            "volume",
        }:
            raise ValueError(
                "declare the requested series alias and use an OHLCV field"
            )
        end = self.bar.get("end_ms", self.bar["time_ms"])
        start = self.bar["time_ms"]
        return [
            float(bar[field])
            for bar in self.extra_series[alias]
            if bar["end_ms"] <= end
            and (not intrabars or bar["time_ms"] >= start)
        ]

    def intrabars(self, alias: str) -> list[dict[str, Any]]:
        """Reads the declared lower-timeframe bars inside the current bar.

        Successive bars advance a cursor, so a whole run reads each intrabar
        once.

        Args:
            alias: Intrabar request alias declared by the script.

        Returns:
            Closed intrabars that start at or after the current bar's start
            and end by its close, oldest first; empty where the host fetched
            none.

        Raises:
            ValueError: If the alias was not declared.
        """
        if alias not in self.extra_series:
            raise ValueError("declare the requested series alias")
        series = self.extra_series[alias]
        start = self.bar["time_ms"]
        end = self.bar.get("end_ms", start)
        index = self._cursors.get(alias, 0)
        while index < len(series) and series[index]["time_ms"] < start:
            index += 1
        self._cursors[alias] = index
        found = []
        while index < len(series) and series[index]["end_ms"] <= end:
            found.append(series[index])
            index += 1
        return found

    def closed_bar(self, alias: str) -> dict[str, Any] | None:
        """Reads the latest declared-series bar closed by the current close.

        Args:
            alias: Request alias declared by the script.

        Returns:
            That bar's OHLCV dictionary, or None before the first one.

        Raises:
            ValueError: If the alias was not declared.
        """
        if alias not in self.extra_series:
            raise ValueError("declare the requested series alias")
        series = self.extra_series[alias]
        end = self.bar.get("end_ms", self.bar["time_ms"])
        index = self._closed.get(alias, -1)
        while index + 1 < len(series) and series[index + 1]["end_ms"] <= end:
            index += 1
        self._closed[alias] = index
        return series[index] if index >= 0 else None

    def period(self, tf: str) -> tuple[int, int]:
        """Reads the calendar period containing the current bar.

        Args:
            tf: A period the script declares in ``PERIODS``.

        Returns:
            UTC millisecond start of the period and of the next one, on the
            instrument's session calendar (UTC days, ISO weeks and months on
            24x7 markets; sessions, their weeks and months on exchanges).

        Raises:
            ValueError: If the host computed no bounds for that period.
        """
        bounds = self.periods.get(tf)
        if bounds is None or len(bounds) < len(self.history):
            raise ValueError("declare the period in PERIODS")
        start, following = bounds[len(self.history) - 1]
        return int(start), int(following)

    def input(
        self,
        name: str,
        default: Any = None,
        *,
        minimum: float | None = None,
        maximum: float | None = None,
        options: list[Any] | None = None,
    ) -> Any:
        """Reads and bounds a user-configurable scalar or structured input.

        Args:
            name: Versioned input name shown in chart settings.
            default: Optional fallback when neither run nor version provides it.
            minimum: Optional inclusive numeric lower bound.
            maximum: Optional inclusive numeric upper bound.
            options: Optional list of accepted JSON values.

        Returns:
            Selected JSON value after range and option checks.

        Raises:
            ValueError: If an input falls outside its declared constraints.
        """
        value = self.inputs.get(name, default)
        if minimum is not None and (
            not isinstance(value, (int, float)) or value < minimum
        ):
            raise ValueError(f"{name} is below its minimum")
        if maximum is not None and (
            not isinstance(value, (int, float)) or value > maximum
        ):
            raise ValueError(f"{name} is above its maximum")
        if options is not None and value not in options:
            raise ValueError(f"{name} is not an accepted option")
        return value

    def series(self, field: str = "close") -> list[float]:
        """Reads one OHLCV field through the current closed bar.

        Args:
            field: OHLCV numeric field, default close.

        Returns:
            Visible values in chronological order.

        Raises:
            ValueError: If the field is not an OHLCV series.
        """
        if field not in {"open", "high", "low", "close", "volume"}:
            raise ValueError("unknown OHLCV field")
        return [float(bar[field]) for bar in self.history]

    def optional_series(self, field: str) -> list[float | None]:
        """Reads an optional provider field through the current closed bar.

        Args:
            field: quote_turnover, trade_count or taker_buy_volume.

        Returns:
            Visible values in chronological order; None where the source
            published no value for that bar.

        Raises:
            ValueError: If the field is not an optional bar field.
        """
        if field not in OPTIONAL_FIELDS:
            raise ValueError("unknown optional bar field")
        return [
            None if bar.get(field) is None else float(bar[field])
            for bar in self.history
        ]

    def sma(self, field: str = "close", length: int = 20) -> float | None:
        """Computes a simple moving average using visible closed bars only.

        Args:
            field: OHLCV numeric field, default close.
            length: Positive integer lookback, maximum two thousand bars.

        Returns:
            Mean, or None while fewer than length bars are available.

        Raises:
            ValueError: If the length is outside the supported bounds.
        """
        if not isinstance(length, int) or not 1 <= length <= 2000:
            raise ValueError("length must be an integer between 1 and 2000")
        values = self.series(field)
        return sum(values[-length:]) / length if len(values) >= length else None

    def plot(
        self,
        name: str,
        value: float | None,
        *,
        color: str = "#5cb8e6",
        style: str = "line",
        width: int = 2,
        offset: int = 0,
        display: str = "all",
    ) -> None:
        """Appends or replaces a named plot value on the current bar.

        Args:
            name: Nonempty display/series name, at most sixty-four characters.
            value: Finite numeric value or None during warm-up.
            color: CSS hex color, default the chart's blue information color.
            style: line, step, area, histogram, columns, circles or crosses.
            width: Pixel width from one through five, default two.
            offset: Display offset in bars, from minus five hundred through five
                hundred.
            display: "all" draws the series; "data_window" only lists its
                values in the legend and data window.

        Returns:
            None after updating the named output series.

        Raises:
            ValueError: If the name, output count or numeric value is invalid.
        """
        if (
            not isinstance(name, str)
            or not 1 <= len(name) <= 64
            or (name not in self.plots and len(self.plots) >= 64)
        ):
            raise ValueError(
                "plots need a short name and at most sixty-four series"
            )
        if value is not None:
            value = float(value)
            if not math.isfinite(value) or abs(value) > 1e15:
                raise ValueError("plot values must be finite and bounded")
        points = self.plots.setdefault(name, [])
        if (
            style
            not in {
                "line",
                "step",
                "area",
                "histogram",
                "columns",
                "circles",
                "crosses",
            }
            or not 1 <= width <= 5
            or not -500 <= offset <= 500
            or display not in {"all", "data_window"}
        ):
            raise ValueError("invalid plot style, width, offset or display")
        styling: dict[str, Any] = {
            "color": color,
            "style": style,
            "width": width,
            "offset": offset,
        }
        if display != "all":
            styling["display"] = display
        self.plot_styles[name] = styling
        point = {"time_ms": self.bar["time_ms"], "value": value, "color": color}
        if points and points[-1]["time_ms"] == point["time_ms"]:
            points[-1] = point
        else:
            points.append(point)

    def alert(
        self, message: str, *, name: str = "alert", severity: str = "INFO"
    ) -> None:
        """Records a preview condition.

        Records a preview condition for durable host-controlled notification
        routing.

        Args:
            message: Plain text, one through five hundred characters.
            name: Stable condition id, one through sixty-four characters.
            severity: INFO, WARNING, ENTRY or CRITICAL notification priority.

        Returns:
            None after recording at most one named event for this closed bar.

        Raises:
            ValueError: For unsupported roles, invalid text/priority or event
                limits.
        """
        if (
            self.kind not in {"indicator", "strategy", "alert"}
            or not 1 <= len(message) <= 500
            or not 1 <= len(name) <= 64
            or severity not in {"INFO", "WARNING", "ENTRY", "CRITICAL"}
        ):
            raise ValueError("invalid alert role, name, message or severity")
        if not self.bar.get("is_final", True):
            return
        if len(self.alerts) >= 2000:
            raise ValueError("alert event limit exceeded")
        event = {
            "name": name,
            "message": message,
            "time_ms": self.bar["time_ms"],
            "severity": severity,
        }
        if (
            self.alerts
            and self.alerts[-1]["name"] == name
            and self.alerts[-1]["time_ms"] == event["time_ms"]
        ):
            self.alerts[-1] = event
        else:
            self.alerts.append(event)

    def visual(self, kind: str, *, id: str | None = None, **data: Any) -> str:  # noqa: A002 - Public drawing and OHLC parameter names match their SDK fields.
        """Creates or replaces a bounded declarative visual object.

        Args:
            kind: Supported line, polyline, box, label, table, fill, hline,
                barcolor, bgcolor, shape, candle or bar category.
            id: Optional stable object id; an omitted id allocates one.
            **data: JSON coordinates, color, text and style for this category.

        Returns:
            Stable object identifier, reusable for later updates or deletion.

        Raises:
            ValueError: For unknown categories or oversized object/output
                limits.
        """
        if kind not in {
            "line",
            "polyline",
            "box",
            "label",
            "table",
            "fill",
            "hline",
            "linefill",
            "barcolor",
            "bgcolor",
            "shape",
            "candle",
            "bar",
        }:
            raise ValueError("unknown visual category")
        visual_id = id or f"{kind}-{len(self.visuals) + len(self.bar_visuals)}"
        if (
            len(visual_id) > 100
            or len(json.dumps(data, allow_nan=False)) > 8192
        ):
            raise ValueError("visual id or object size limit exceeded")
        target = (
            self.bar_visuals
            if kind in {"bgcolor", "barcolor", "shape", "candle", "bar"}
            else self.visuals
        )
        maximum = 16000 if target is self.bar_visuals else 500
        if visual_id not in target and len(target) >= maximum:
            raise ValueError("visual object limit exceeded")
        target[visual_id] = {"id": visual_id, "kind": kind, "data": data}
        return visual_id

    def delete(self, id: str) -> None:  # noqa: A002 - Public drawing and OHLC parameter names match their SDK fields.
        """Removes a script-owned visual.

        Removes a script-owned visual without modifying manual chart drawings.

        Args:
            id: Identifier previously returned by a visual method.

        Returns:
            None after removing the object when present.
        """
        self.visuals.pop(id, None)
        self.bar_visuals.pop(id, None)

    def line(
        self,
        from_ms: int,
        from_price: float,
        to_ms: int,
        to_price: float,
        *,
        id: str | None = None,  # noqa: A002 - Public drawing and OHLC parameter names match their SDK fields.
        color: str = "#5cb8e6",
        width: int = 1,
        style: str = "solid",
        extend: str = "none",
    ) -> str:
        """Draws or updates a script-owned segment, ray or extended line.

        Args:
            from_ms: First UTC millisecond anchor.
            from_price: First numeric price anchor.
            to_ms: Second UTC millisecond anchor.
            to_price: Second numeric price anchor.
            id: Optional stable id to replace an existing line.
            color: Hex stroke color.
            width: Stroke width in CSS pixels.
            style: solid, dashed or dotted stroke.
            extend: none, left, right or both endpoint extension.

        Returns:
            Script-owned visual id for updates or deletion.
        """
        return self.visual(
            "line",
            id=id,
            from_ms=from_ms,
            from_price=from_price,
            to_ms=to_ms,
            to_price=to_price,
            color=color,
            width=width,
            style=style,
            extend=extend,
        )

    def polyline(
        self,
        points: list[list[float]],
        *,
        id: str | None = None,  # noqa: A002 - Public drawing and OHLC parameter names match their SDK fields.
        color: str = "#5cb8e6",
        closed: bool = False,
        fill: str | None = None,
    ) -> str:
        """Draws connected time/price anchors, optionally closing.

        Draws connected time/price anchors, optionally closing and filling them.

        Args:
            points: Two through sixty-four [UTC milliseconds, price] pairs.
            id: Optional stable id for replacing the object.
            color: Hex stroke color.
            closed: Whether to connect the final anchor back to the first.
            fill: Optional hex polygon fill color.

        Returns:
            Script-owned polyline id.

        Raises:
            ValueError: If the anchor count is outside its supported bounds.
        """
        if not 2 <= len(points) <= 64:
            raise ValueError("polylines need two through sixty-four anchors")
        return self.visual(
            "polyline",
            id=id,
            points=points,
            color=color,
            closed=closed,
            fill=fill,
        )

    def box(
        self,
        from_ms: int,
        to_ms: int,
        lower: float,
        upper: float,
        *,
        id: str | None = None,  # noqa: A002 - Public drawing and OHLC parameter names match their SDK fields.
        color: str = "#5cb8e6",
        opacity: float = 0.15,
        text: str = "",
    ) -> str:
        """Draws or updates a rectangular price/time zone.

        Args:
            from_ms: Left UTC millisecond anchor.
            to_ms: Right UTC millisecond anchor.
            lower: Lower numeric price.
            upper: Upper numeric price.
            id: Optional stable object id.
            color: Hex border/fill color.
            opacity: Fill alpha from zero through one.
            text: Optional plain-text label.

        Returns:
            Script-owned box id.
        """
        return self.visual(
            "box",
            id=id,
            from_ms=from_ms,
            to_ms=to_ms,
            lower=lower,
            upper=upper,
            color=color,
            opacity=opacity,
            text=text[:300],
        )

    def label(
        self,
        text: str,
        *,
        time_ms: int | None = None,
        price: float | None = None,
        id: str | None = None,  # noqa: A002 - Public drawing and OHLC parameter names match their SDK fields.
        color: str = "#e4edf1",
        position: str = "at",
    ) -> str:
        """Places an editable script-owned text annotation on a source bar.

        Args:
            text: Plain text, displayed up to three hundred characters.
            time_ms: Optional UTC millisecond anchor, default current bar.
            price: Optional numeric price, default current close.
            id: Optional stable id for updating an existing label.
            color: Hex text color.
            position: at, above or below the selected price anchor.

        Returns:
            Script-owned label id.
        """
        return self.visual(
            "label",
            id=id,
            text=text[:300],
            time_ms=self.bar["time_ms"] if time_ms is None else time_ms,
            price=float(self.bar["close"]) if price is None else price,
            color=color,
            position=position,
        )

    def table(
        self,
        name: str,
        rows: list[list[Any]],
        *,
        position: str = "top_right",
        color: str = "#e4edf1",
        background: str = "#122431",
    ) -> str:
        """Displays a fixed-position table independent of chart pan and zoom.

        Args:
            name: Stable table id, reused to update its contents.
            rows: At most twenty rows of ten plain-text cells each.
            position: One of nine top/middle/bottom and left/center/right
                anchors.
            color: Hex text color.
            background: Hex cell background color.

        Returns:
            Script-owned table id.

        Raises:
            ValueError: If the table dimensions exceed supported bounds.
        """
        if len(rows) > 20 or any(len(row) > 10 for row in rows):
            raise ValueError("tables support twenty rows and ten columns")
        cells = [[str(cell)[:120] for cell in row] for row in rows]
        return self.visual(
            "table",
            id=name,
            rows=cells,
            position=position,
            color=color,
            background=background,
        )

    def hline(
        self,
        name: str,
        price: float,
        *,
        color: str = "#8fa5b3",
        style: str = "dashed",
    ) -> str:
        """Draws or updates a named horizontal level.

        Args:
            name: Stable level id and display label.
            price: Finite numeric level.
            color: Hex stroke color.
            style: solid, dashed or dotted line style.

        Returns:
            Script-owned horizontal-level id.
        """
        return self.visual(
            "hline", id=name, price=price, color=color, style=style, text=name
        )

    def fill(
        self,
        first: str,
        second: str,
        *,
        color: str = "#5cb8e6",
        opacity: float = 0.15,
        id: str | None = None,  # noqa: A002 - Public drawing and OHLC parameter names match their SDK fields.
    ) -> str:
        """Shades the area between two named plots or horizontal levels.

        Args:
            first: First plot/level name.
            second: Second plot/level name.
            color: Hex fill color.
            opacity: Fill alpha from zero through one.
            id: Optional stable fill id, default derived from both names.

        Returns:
            Script-owned fill id.
        """
        return self.visual(
            "fill",
            id=id or f"fill:{first}:{second}",
            first=first,
            second=second,
            color=color,
            opacity=opacity,
        )

    def linefill(
        self,
        first: str,
        second: str,
        *,
        color: str = "#5cb8e6",
        opacity: float = 0.15,
    ) -> str:
        """Fills the overlapping interval between two script-owned lines.

        Args:
            first: Existing first line object identifier.
            second: Existing second line object identifier.
            color: Literal hex fill color.
            opacity: Fill alpha from zero through one.

        Returns:
            Stable fill identifier, updated when either source line changes.

        Raises:
            ValueError: If either reference is absent or not a line object.
        """
        if any(
            self.visuals.get(name, {}).get("kind") != "line"
            for name in (first, second)
        ):
            raise ValueError("linefill requires two existing line objects")
        return self.visual(
            "linefill",
            id=f"linefill:{first}:{second}",
            first=first,
            second=second,
            color=color,
            opacity=opacity,
        )

    def bgcolor(self, color: str, *, opacity: float = 0.15) -> str:
        """Colors the current bar's chart background.

        Colors the current bar's chart background without changing source
        prices.

        Args:
            color: Hex background color.
            opacity: Fill alpha from zero through one.

        Returns:
            Current bar's background visual id.
        """
        return self.visual(
            "bgcolor",
            id=f"bg:{self.bar['time_ms']}",
            time_ms=self.bar["time_ms"],
            color=color,
            opacity=opacity,
        )

    def barcolor(self, color: str) -> str:
        """Overrides the current source candle's display color.

        Args:
            color: Hex candle body/wick color.

        Returns:
            Current bar's candle-color visual id.
        """
        return self.visual(
            "barcolor",
            id=f"bar:{self.bar['time_ms']}",
            time_ms=self.bar["time_ms"],
            color=color,
            open=self.bar["open"],
            high=self.bar["high"],
            low=self.bar["low"],
            close=self.bar["close"],
        )

    def plotshape(
        self,
        shape: str = "circle",
        *,
        text: str = "",
        position: str = "below",
        color: str = "#5cb8e6",
        id: str | None = None,  # noqa: A002 - Public drawing and OHLC parameter names match their SDK fields.
    ) -> str:
        """Marks the current candle.

        Marks the current candle with a shape, character or directional arrow.

        Args:
            shape: circle, square, diamond, cross, arrow_up or arrow_down.
            text: Optional plain-text character or caption.
            position: above, below or at the source close.
            color: Hex marker/text color.
            id: Optional stable marker id.

        Returns:
            Script-owned marker id anchored to the actual current candle.
        """
        return self.visual(
            "shape",
            id=id,
            shape=shape,
            text=text[:80],
            position=position,
            color=color,
            time_ms=self.bar["time_ms"],
            price=self.bar["close"],
            high=self.bar["high"],
            low=self.bar["low"],
        )

    def plotchar(
        self, character: str, *, color: str = "#5cb8e6", position: str = "below"
    ) -> str:
        """Places a plain-text character on the current bar.

        Args:
            character: Text truncated to eighty characters.
            color: Hex text color.
            position: above, below or at the source close.

        Returns:
            Script-owned marker identifier.
        """
        return self.plotshape(
            "character", text=character, color=color, position=position
        )

    def plotarrow(self, value: float, *, color: str = "#5cb8e6") -> str | None:
        """Marks positive values upward and negative values downward.

        Args:
            value: Signed numeric direction; zero draws no marker.
            color: Hex arrow color.

        Returns:
            Marker identifier, or None for a zero value.
        """
        if not value:
            return None
        return self.plotshape(
            "arrow_up" if value > 0 else "arrow_down",
            color=color,
            position="below" if value > 0 else "above",
        )

    def plotcandle(
        self,
        name: str,
        open: float,  # noqa: A002 - Public drawing and OHLC parameter names match their SDK fields.
        high: float,
        low: float,
        close: float,
        *,
        color: str = "#5cb8e6",
        bars: bool = False,
    ) -> str:
        """Plots synthetic candles or OHLC bars.

        Plots synthetic candles or OHLC bars while retaining source provenance.

        Args:
            name: Stable custom series name, at most sixty-four characters.
            open: Finite opening value.
            high: Finite high, at least all other values.
            low: Finite low, at most all other values.
            close: Finite closing value.
            color: Hex candle or bar color.
            bars: True for OHLC bars, False for candle bodies and wicks.

        Returns:
            Current bar's custom OHLC visual identifier.

        Raises:
            ValueError: For an invalid name or inconsistent/nonfinite OHLC
                values.
        """
        values = [open, high, low, close]
        if (
            not 1 <= len(name) <= 64
            or any(
                not math.isfinite(value) or abs(value) > 1e15
                for value in values
            )
            or high < max(values)
            or low > min(values)
        ):
            raise ValueError("custom OHLC values must be finite and consistent")
        return self.visual(
            "bar" if bars else "candle",
            id=f"{name}:{self.bar['time_ms']}",
            time_ms=self.bar["time_ms"],
            name=name,
            open=open,
            high=high,
            low=low,
            close=close,
            color=color,
        )

    def plotbar(
        self,
        name: str,
        open_value: float,
        high: float,
        low: float,
        close: float,
        *,
        color: str = "#5cb8e6",
    ) -> str:
        """Plots a named OHLC bar instead of a filled candle body.

        Args:
            name: Stable custom series name, at most sixty-four characters.
            open_value: Finite opening value within the declared high/low.
            high: Finite highest value.
            low: Finite lowest value.
            close: Finite closing value within the declared high/low.
            color: Literal hex body and wick color.

        Returns:
            Current source bar's custom OHLC visual identifier.

        Raises:
            ValueError: If the name or OHLC bounds are inconsistent.
        """
        return self.plotcandle(
            name, open_value, high, low, close, color=color, bars=True
        )

    def alertcondition(
        self,
        condition: bool,
        title: str,
        message: str,
        *,
        severity: str = "INFO",
    ) -> None:
        """Publishes a confirmed-bar condition when its predicate matches.

        Args:
            condition: Explicit boolean result of the user's Python predicate.
            title: Stable condition name, one through sixty-four characters.
            message: Plain-text notification, at most five hundred characters.
            severity: INFO, WARNING, ENTRY or CRITICAL priority.

        Returns:
            None after recording a matching confirmed-bar event for host
            routing.

        Raises:
            ValueError: If the predicate is not boolean or alert fields are
                invalid.
        """
        if not isinstance(condition, bool):
            raise ValueError("alertcondition requires a boolean predicate")
        if condition:
            self.alert(message, name=title, severity=severity)

    def entry(
        self,
        side: str,
        quantity: float = 1.0,
        *,
        stop: float | None = None,
        target: float | None = None,
        comment: str = "",
    ) -> None:
        """Queues a simulated order for the following bar's opening price.

        The stop and target are protective levels: live activations forward
        them with entry signals (the execution engine places them as
        venue-held stop-loss and take-profit orders), while the simulation
        leaves exits to the strategy, which reads them back through
        ``position_stop`` and ``position_target``.

        Args:
            side: long or short; any other value is rejected.
            quantity: Positive finite base-asset quantity, maximum one billion.
            stop: Optional protective stop, below the current close for a
                long entry and above it for a short entry.
            target: Optional profit target, above the current close for a
                long entry and below it for a short entry.
            comment: Optional plain-text reason, at most 120 characters.

        Returns:
            None after replacing this bar's pending intent.

        Raises:
            ValueError: If used by an indicator or with an invalid side,
                quantity, stop, target or comment.
        """
        if self.kind != "strategy" or side not in {"long", "short"}:
            raise ValueError("only strategy scripts can enter long or short")
        quantity = float(quantity)
        if not math.isfinite(quantity) or not 0 < quantity <= 1e9:
            raise ValueError("quantity must be finite and positive")
        close = float(self.bar["close"])
        sign = 1 if side == "long" else -1
        levels: dict[str, float | None] = {}
        for name, given, direction in (
            ("stop", stop, -sign),
            ("target", target, sign),
        ):
            if given is None:
                levels[name] = None
                continue
            value = float(given)
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be a finite positive price")
            if (value - close) * direction <= 0:
                raise ValueError(
                    f"{name} is on the wrong side of the close for a "
                    f"{side} entry"
                )
            levels[name] = value
        self._queue(
            side, quantity, levels["stop"], levels["target"], str(comment)
        )

    def close(self, comment: str = "") -> None:
        """Queues closing the simulated position on the next bar's open.

        Args:
            comment: Optional plain-text reason, at most 120 characters.

        Returns:
            None after replacing the pending intent with a simulated close.

        Raises:
            ValueError: If called from an indicator script or with an
                overlong comment.
        """
        if self.kind != "strategy":
            raise ValueError("only strategy scripts can close positions")
        self._queue("flat", 0.0, None, None, str(comment))

    def _queue(
        self,
        side: str,
        quantity: float,
        stop: float | None,
        target: float | None,
        comment: str,
    ) -> None:
        """Replaces this bar's pending intent and records the order.

        Args:
            side: long, short or flat.
            quantity: Target absolute quantity; zero when flat.
            stop: Protective stop, if any.
            target: Profit target, if any.
            comment: Plain-text reason.

        Returns:
            None after queuing the intent.

        Raises:
            ValueError: If the comment exceeds 120 characters.
        """
        if len(comment) > 120:
            raise ValueError("order comments are limited to 120 characters")
        order = {
            "time_ms": self.bar["time_ms"],
            "action": "close" if side == "flat" else side,
            "reference": float(self.bar["close"]),
            "quantity": quantity,
            "stop": stop,
            "target": target,
            "comment": comment,
            "status": "pending",
            "fill_time_ms": None,
        }
        if self.orders and self.orders[-1]["time_ms"] == order["time_ms"]:
            self.orders[-1] = order
        else:
            self.orders.append(order)
        self.pending = [(side, quantity)]

    def advance(self, bar: dict[str, Any]) -> None:
        """Fills prior-bar intents.

        Fills prior-bar intents and exposes one more closed bar to the script.

        Args:
            bar: Next chronological closed OHLCV dictionary.

        Returns:
            None after accounting for adverse slippage and per-fill fees.
        """
        order = self.orders[-1] if self.pending and self.orders else None
        for side, quantity in self.pending:
            desired = quantity if side == "long" else -quantity
            if side == "flat":
                desired = 0.0
            change = desired - self.position_size
            if order is not None:
                order["status"] = "unchanged"
            if not change:
                continue
            price = float(bar["open"]) * (
                1 + math.copysign(self.slippage_bps / 10000, change)
            )
            fee = abs(change) * price * self.commission_bps / 10000
            previous = self.position_size
            marked = self.cash + previous * float(bar["open"])
            if desired and abs(desired) * price + fee > max(0, marked):
                self.rejected_orders += 1
                if order is not None:
                    order["status"] = "rejected"
                continue
            if order is not None:
                order["status"] = "filled"
                order["fill_time_ms"] = bar["time_ms"]
            if desired == 0:
                self.position_stop = self.position_target = None
            elif order is not None and _sign(self.position_size) != _sign(
                desired
            ):
                self.position_stop = order["stop"]
                self.position_target = order["target"]
            self.cash -= change * price + fee
            self.position_size = desired
            effect = "entry"
            if desired == 0 or (
                previous * desired > 0 and abs(desired) < abs(previous)
            ):
                effect = "exit"
            elif previous and previous * desired < 0:
                effect = "reversal"
            self.fills.append(
                {
                    "time_ms": bar["time_ms"],
                    "side": "buy" if change > 0 else "sell",
                    "quantity": abs(change),
                    "price": price,
                    "fee": fee,
                    "effect": effect,
                }
            )
        self.pending = []
        self.history.append(dict(bar))


def _load(
    bundle: dict[str, Any], cache: dict[str, types.ModuleType]
) -> types.ModuleType:
    """Loads one source module.

    Loads one source module and its pinned library graph inside the container.

    Args:
        bundle: Source module with recursively resolved dependency bundles.
        cache: Modules indexed by immutable content hash, local to this run.

    Returns:
        Loaded module; library objects expose their declared __all__ names.

    Raises:
        ValueError: If a library export is absent or an alias is undeclared.
        Exception: If user source fails while loading inside the container.
    """
    key = str(bundle["source_hash"])
    if key in cache:
        return cache[key]
    namespace = types.ModuleType(f"joseph_user_{key}")
    sys.modules[namespace.__name__] = namespace
    libraries = bundle.get("libraries", {})

    def library_import(
        name: str,
        globals_: dict[str, Any] | None = None,
        locals_: dict[str, Any] | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> Any:
        """Resolves only this module's pinned library aliases.

        Resolves only this module's pinned library aliases before stdlib
        imports.

        Args:
            name: Python module name supplied by the import statement.
            globals_: Importing module globals, optional.
            locals_: Importing scope locals, optional.
            fromlist: Requested public imported names, optional.
            level: Relative-import level, default absolute.

        Returns:
            Dependency namespace or standard Python import result.

        Raises:
            ValueError: If an undeclared library alias is requested.
            ImportError: If a standard Python module is unavailable.
        """
        if name == "libraries":
            module = types.ModuleType("libraries")
            for alias in fromlist:
                if alias not in libraries:
                    raise ValueError("undeclared library alias")
                setattr(module, alias, _load(libraries[alias], cache))
            return module
        if name.startswith("libraries."):
            alias = name.split(".")[1]
            if alias not in libraries:
                raise ValueError("undeclared library alias")
            return _load(libraries[alias], cache)
        return builtins.__import__(name, globals_, locals_, fromlist, level)  # noqa: GPY037 - Container-only hook resolves immutable pinned imports.

    allowed_builtins = dict(vars(builtins))
    allowed_builtins["__import__"] = library_import
    namespace.__dict__["__builtins__"] = allowed_builtins
    exec(  # noqa: S102 - User source runs only inside the restricted runner container.
        compile(bundle["source"], f"{bundle['script_id']}.py", "exec"),
        namespace.__dict__,
    )
    base = getattr(joseph_script, str(bundle["kind"]).title())
    classes = [
        value
        for value in namespace.__dict__.values()
        if isinstance(value, type)
        and value.__module__ == namespace.__name__
        and issubclass(value, base)
        and value is not base
    ]
    if len(classes) != 1 or inspect.isabstract(classes[0]):
        raise ValueError(
            "implement exactly one concrete subclass of the selected"
            " script base"
        )
    instance = classes[0]()
    namespace.__dict__["__script_instance__"] = instance
    if bundle["kind"] == "library":
        exports = instance.exports()
        if (
            not isinstance(exports, Mapping)
            or not exports
            or any(
                not isinstance(name, str)
                or not name.isidentifier()
                or name.startswith("_")
                for name in exports
            )
        ):
            raise ValueError(
                "library exports must map public "
                "identifiers to reusable objects"
            )
        exported = types.ModuleType(namespace.__name__)
        for name, value in exports.items():
            setattr(exported, name, value)
        namespace = exported
    cache[key] = namespace
    return namespace


def evaluate(bundle: dict[str, Any]) -> dict[str, Any]:
    """Runs closed bars in order and returns display data.

    Runs closed bars in order and returns display data and simulated results.

    Args:
        bundle: Validated source/dependency bundle, closed bars and inputs.

    Returns:
        Plot series, simulated fills/equity and reproducibility statistics.

    Raises:
        Exception: If source code or its declared entry point fails.
    """
    if "symbols" in bundle:
        results = []
        for symbol in bundle["symbols"]:
            try:
                result = evaluate(
                    {
                        key: value
                        for key, value in bundle.items()
                        if key != "symbols"
                    }
                    | symbol
                )
                results.append(
                    {
                        "instrument_id": symbol["instrument_id"],
                        "matched": result["matched"],
                        "error": None,
                    }
                )
            except joseph_script.Unavailable as exc:
                results.append(
                    {
                        "instrument_id": symbol["instrument_id"],
                        "matched": False,
                        "error": str(exc)[:300] or "required data unavailable",
                        "unavailable": True,
                    }
                )
            except Exception as exc:  # noqa: BLE001 - One user filter failure must not discard other symbols.
                results.append(
                    {
                        "instrument_id": symbol["instrument_id"],
                        "matched": False,
                        "error": f"{type(exc).__name__}: {str(exc)[:300]}",
                    }
                )
        return {"results": results}
    module = _load(bundle, {})
    return run_instance(module.__dict__["__script_instance__"], bundle)


def run_class(cls: type, bundle: dict[str, Any]) -> dict[str, Any]:
    """Runs a trusted, already imported script class on one bundle.

    The host uses this for built-in and operator-installed plugin scripts,
    which are platform code rather than tenant source; it never executes
    source text.

    Args:
        cls: Concrete subclass of the bundle's joseph_script role.
        bundle: Kind, closed bars, inputs and simulation settings.

    Returns:
        The same result mapping as an isolated container run.

    Raises:
        ValueError: If the class does not implement the bundle's role.
    """
    base = getattr(joseph_script, str(bundle["kind"]).title())
    if (
        not isinstance(cls, type)
        or not issubclass(cls, base)
        or inspect.isabstract(cls)
    ):
        raise ValueError("script class does not implement the selected role")
    if "symbols" in bundle:
        results = []
        for symbol in bundle["symbols"]:
            try:
                result = run_class(
                    cls,
                    {
                        key: value
                        for key, value in bundle.items()
                        if key != "symbols"
                    }
                    | symbol,
                )
                results.append(
                    {
                        "instrument_id": symbol["instrument_id"],
                        "matched": result["matched"],
                        "error": None,
                    }
                )
            except joseph_script.Unavailable as exc:
                results.append(
                    {
                        "instrument_id": symbol["instrument_id"],
                        "matched": False,
                        "error": str(exc)[:300] or "required data unavailable",
                        "unavailable": True,
                    }
                )
            except Exception as exc:  # noqa: BLE001 - One filter failure must not discard other symbols.
                results.append(
                    {
                        "instrument_id": symbol["instrument_id"],
                        "matched": False,
                        "error": f"{type(exc).__name__}: {str(exc)[:300]}",
                    }
                )
        return {"results": results}
    return run_instance(cls(), bundle)


def run_instance(instance: Any, bundle: dict[str, Any]) -> dict[str, Any]:
    """Evaluates one script instance over the bundle's bars in order.

    Args:
        instance: Fresh script instance for this run.
        bundle: Kind, closed bars, inputs and simulation settings.

    Returns:
        Plot series, simulated fills/equity and reproducibility statistics.

    Raises:
        ValueError: If a filter returns a non-boolean or equity is not finite.
    """
    context = Context(bundle)
    run: Callable[[Context], None] = getattr(instance, "on_bar", lambda _: None)
    peak = context.cash
    drawdown = 0.0
    for bar in bundle["bars"]:
        context.advance(bar)
        run(context)
        value = context.equity
        if not math.isfinite(value):
            raise ValueError("simulated account value is not finite")
        peak = max(peak, value)
        drawdown = max(drawdown, (peak - value) / peak * 100 if peak > 0 else 0)
        context.equity_curve.append({"time_ms": bar["time_ms"], "value": value})
    final = context.equity if context.history else context.cash
    initial = float(bundle.get("initial_equity", 10000))
    matched = None
    if context.kind == "filter":
        matched = instance.matches(context)
        if not isinstance(matched, bool):
            raise ValueError("Filter.matches must return a boolean")
    return {
        "plots": [
            {"name": name, "points": points}
            for name, points in context.plots.items()
        ],
        "fills": context.fills,
        "orders": context.orders if context.kind == "strategy" else [],
        "position_size": context.position_size,
        "matched": matched,
        "alerts": context.alerts,
        "visuals": list(context.visuals.values()),
        "bar_visuals": list(context.bar_visuals.values()),
        "plot_styles": context.plot_styles,
        "equity_curve": context.equity_curve
        if context.kind == "strategy"
        else [],
        "metrics": {
            "initial_equity": initial,
            "final_equity": final,
            "net_profit": final - initial,
            "return_pct": (final / initial - 1) * 100,
            "max_drawdown_pct": drawdown,
            "fill_count": len(context.fills),
            "fees": sum(fill["fee"] for fill in context.fills),
            "unfilled_intents": len(context.pending),
            "rejected_orders": context.rejected_orders,
        },
    }


def main() -> int:  # noqa: GPY013 - Container validation failures become a bounded JSON error reply.
    """Reads a bounded environment bundle and prints one container result.

    Returns:
        Zero with JSON output, or one with a bounded user-source error. No
        host credentials, files or execution-service connections are available.
    """
    try:
        pieces = [
            os.environ.pop(f"JOSEPH_SCRIPT_INPUT_{index}")
            for index in range(int(os.environ.pop("JOSEPH_SCRIPT_INPUT_COUNT")))
        ]
        packed = base64.b64decode("".join(pieces))
        data = gzip.decompress(packed)
        if len(data) > 2_000_000:
            raise ValueError("script input is too large")
        bundle = json.loads(data)
        # Keep user stdout out of the JSON protocol and bound host logs.
        with contextlib.redirect_stdout(io.StringIO()):
            result = evaluate(bundle)
        output = json.dumps({"ok": True, "result": result}, allow_nan=False)
        if len(output.encode()) > 16_000_000:
            raise ValueError("script output exceeds sixteen megabytes")
        sys.stdout.write(output)
        return 0
    except Exception as exc:  # noqa: BLE001 - Container boundary returns user errors, never a host traceback.
        sys.stdout.write(
            json.dumps(
                {
                    "ok": False,
                    "error": f"{type(exc).__name__}: {str(exc)[:400]}",
                }
            )
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
