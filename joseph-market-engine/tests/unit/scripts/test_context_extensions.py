# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Intrabar, period and data-window extensions of the script context."""

from __future__ import annotations

from typing import Any
from typing import ClassVar
from typing import cast

import pydantic
import pytest

import joseph_script
from joseph.domain import errors
from joseph.domain import timeframes
from joseph.scripts import catalog
from joseph.scripts import models
from joseph.scripts import service
from joseph.scripts import worker

MINUTE = 60_000
"""One minute in milliseconds."""


def _bar(start: int, length: int, close: float = 1.0) -> dict[str, Any]:
    """Builds one closed bar.

    Args:
        start: Start in UTC milliseconds.
        length: Duration in milliseconds.
        close: Every price of the bar.

    Returns:
        Script bar.
    """
    return {
        "time_ms": start,
        "end_ms": start + length,
        "is_final": True,
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": 1.0,
    }


class Counter(joseph_script.Indicator):
    """Plots intrabar counts, the last closed hour and the day start."""

    ID = "counter"
    PERIODS: ClassVar[tuple[str, ...]] = ("1d",)

    def on_bar(self, ctx: worker.Context) -> None:
        """Publishes the extension readings.

        Args:
            ctx: Script context.
        """
        ctx.plot("Intrabars", len(ctx.intrabars("minutes")))
        ctx.plot("Again", len(ctx.intrabars("minutes")), display="data_window")
        closed = ctx.closed_bar("hours")
        ctx.plot("Hour", None if closed is None else closed["time_ms"])
        ctx.plot("Day", ctx.period("1d")[0])


def test_intrabars_closed_bars_and_periods() -> None:
    """The cursors read each bar's own intrabars and the latest closed bar."""
    bars = [_bar(index * 5 * MINUTE, 5 * MINUTE) for index in range(24)]
    minutes = [_bar(index * MINUTE, MINUTE) for index in range(10, 120)]
    hours = [_bar(index * 60 * MINUTE, 60 * MINUTE) for index in range(2)]
    result = worker.run_class(
        Counter,
        {
            "kind": "indicator",
            "bars": bars,
            "extra_series": {"minutes": minutes, "hours": hours},
            "periods": service.period_bounds(
                "binance:perp:BTCUSDT", bars, ("1d",)
            ),
        },
    )
    plots = {
        plot["name"]: [point["value"] for point in plot["points"]]
        for plot in result["plots"]
    }
    assert plots["Intrabars"] == [0, 0, 5, *[5] * 21]
    assert plots["Again"] == plots["Intrabars"]
    assert plots["Hour"][10] is None
    assert plots["Hour"][11] == 0
    assert plots["Hour"][23] == 60 * MINUTE
    assert set(plots["Day"]) == {0}
    assert result["plot_styles"]["Again"]["display"] == "data_window"
    assert "display" not in result["plot_styles"]["Intrabars"]


def test_undeclared_period_and_display_are_rejected() -> None:
    """Periods must be declared and display modes are bounded."""
    context = worker.Context({"kind": "indicator", "bars": []})
    context.advance(_bar(0, MINUTE))
    with pytest.raises(ValueError, match="PERIODS"):
        context.period("1w")
    with pytest.raises(ValueError, match="display"):
        context.plot("x", 1.0, display="hidden")
    with pytest.raises(ValueError, match="alias"):
        context.intrabars("missing")


def test_auto_timeframes() -> None:
    """Auto intrabars follow the chart resolution."""
    tf = timeframes.Timeframe
    assert service.auto_intrabar_tf(tf.M1) is None
    assert service.auto_intrabar_tf(tf.S5) is tf.S1
    assert service.auto_intrabar_tf(tf.M5) is tf.M1
    assert service.auto_intrabar_tf(tf.D1) is tf.M5
    assert service.auto_intrabar_tf(tf.W1) is tf.H1
    declared = models.SeriesRequest.model_validate(
        {"tf": "auto", "intrabars": True, "tf_input": "intrabar_tf"},
        strict=False,
    )
    assert service.request_tf(declared, tf.M5, {"intrabar_tf": "15s"}) is (
        tf.S15
    )
    assert service.request_tf(declared, tf.M5, {"intrabar_tf": "15m"}) is None
    assert service.request_tf(declared, tf.H1, {}) is tf.M1
    with pytest.raises(errors.ValidationFailedError):
        service.request_tf(declared, tf.H1, {"intrabar_tf": "bogus"})


def test_only_intrabar_requests_may_be_large() -> None:
    """Ordinary requests keep their five-hundred-bar bound."""
    with pytest.raises(pydantic.ValidationError):
        models.SeriesRequest.model_validate(
            {"tf": "1d", "limit": 600}, strict=False
        )
    with pytest.raises(pydantic.ValidationError):
        models.SeriesRequest.model_validate({"tf": "auto"}, strict=False)


class _Weekly(joseph_script.Indicator):
    """Declares an unsupported period."""

    ID = "weekly"
    PERIODS: ClassVar[tuple[str, ...]] = ("4h",)

    def on_bar(self, ctx: worker.Context) -> None:
        """Publishes nothing.

        Args:
            ctx: Script context.
        """
        del ctx


class _Huge(joseph_script.Indicator):
    """Declares an oversized ordinary request."""

    ID = "huge"
    REQUESTS: ClassVar[dict[str, joseph_script.Request]] = {
        "day": joseph_script.Request("1d", limit=900)
    }

    def on_bar(self, ctx: worker.Context) -> None:
        """Publishes nothing.

        Args:
            ctx: Script context.
        """
        del ctx


def test_catalog_validates_periods_and_requests() -> None:
    """Invalid PERIODS or REQUESTS declarations are rejected."""
    for cls in (_Weekly, _Huge):
        with pytest.raises(errors.ValidationFailedError):
            catalog.make_entry(cls, origin="plugin", entry_point="x")
    entry = catalog.make_entry(Counter, origin="plugin", entry_point="x")
    assert entry.periods == ("1d",)


class _Batch:
    """Candle batch stand-in without candles.

    Attributes:
        candles: No candles.
    """

    candles: ClassVar[list[Any]] = []


class _Market:
    """Records candle requests.

    Attributes:
        calls: Keyword arguments of every request.
    """

    def __init__(self) -> None:
        """Starts without requests."""
        self.calls: list[dict[str, Any]] = []

    async def get_batch(self, *args: Any, **kwargs: Any) -> _Batch:
        """Records one request.

        Args:
            *args: Instrument and resolution.
            **kwargs: Range and limit.

        Returns:
            An empty batch.
        """
        self.calls.append({"args": args, **kwargs})
        return _Batch()


async def test_intrabars_are_fetched_only_where_needed() -> None:
    """Intrabars cover the window's bars that lack a buy volume."""
    market = _Market()
    scripts = service.ScriptService(
        cast("Any", None), cast("Any", None), cast("Any", market)
    )
    declared = models.SeriesRequest.model_validate(
        {
            "tf": "auto",
            "intrabars": True,
            "unless": "taker_buy_volume",
            "unless_input": "use",
            "limit": 3000,
        },
        strict=False,
    )
    bars = [_bar(index * 5 * MINUTE, 5 * MINUTE) for index in range(10)]
    for bar in bars[:6]:
        bar["taker_buy_volume"] = 0.5
    run = models.ScriptRun(
        instrument_id="binance:perp:BTCUSDT",
        tf=timeframes.Timeframe.M5,
        window_from_ms=8 * 5 * MINUTE,
    )
    await scripts._request_bars(declared, run, bars, {"use": True})
    assert market.calls[-1]["from_ms"] == 8 * 5 * MINUTE
    assert market.calls[-1]["limit"] == 3000
    unbounded = run.model_copy(update={"window_from_ms": None})
    await scripts._request_bars(declared, unbounded, bars, {"use": True})
    assert market.calls[-1]["from_ms"] == 6 * 5 * MINUTE
    await scripts._request_bars(declared, unbounded, bars, {"use": False})
    assert market.calls[-1]["from_ms"] == 0
    for bar in bars:
        bar["taker_buy_volume"] = 0.5
    count = len(market.calls)
    assert await scripts._request_bars(declared, run, bars, {"use": True}) == []
    assert len(market.calls) == count
