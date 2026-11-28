# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Golden scenarios of the built-in indicator scripts.

Each ``fixtures/indicators/<id>/<scenario>.json`` file holds hand-computed
bars and the values the study must publish on the last bar::

    {"plugin_id": "...", "timeframe": "5m", "settings": {...},
     "instrument_id": "...", "start_ms": ...,          # optional
     "bars": [[open, high, low, close, volume, buy?, method?], ...],
     "intrabars": {"<bar index>": {"1m": [[o, h, l, c, v], ...]}},
     "aux": {"<bar index>": {"1d": [[start_ms, o, h, l, c, v], ...]}},
     "expect": {"final_values": {...}, "final_aois": [...]}}

The runner builds the script bundle the host would (bars on the
instrument's session calendar, intrabar and period series, calendar
period bounds), runs ``builtin:<id>`` in process and reads the published
plots, drawings and alerts back into the scenario's value names. Values
are compared at the precision they were published with before (prices to
four places below the tick, ratios to four places, percentages to two).
"""

from __future__ import annotations

import decimal
import json
import pathlib
from collections.abc import Callable
from typing import Any
from typing import cast

import pytest

import joseph_script
from joseph.domain import instruments
from joseph.domain import sessions
from joseph.domain import timeframes
from joseph.scripts import builtin
from joseph.scripts import catalog
from joseph.scripts import service
from joseph.scripts import worker

FIXTURES = (
    pathlib.Path(__file__).resolve().parents[3] / "fixtures" / "indicators"
)
SCENARIOS = sorted(FIXTURES.glob("*/*.json"))
DEFAULT_INSTRUMENT = "binance:perp:BTCUSDT"
DEFAULT_START_MS = 1_794_787_200_000
PLACES = {
    "bandwidth": 4,
    "percent_b": 4,
    "rsi": 4,
    "pearson_r": 4,
    "delta_pct": 2,
    "coverage_pct": 2,
    "delta": 8,
    "buy_volume": 8,
    "sell_volume": 8,
}
"""Published decimal places per value; prices use six (tick 0.01)."""

METHODS = {
    "Taker buy volume": "taker_volume",
    "Classified trades": "trade_classification",
    "Intrabar estimate": "candle_direction",
    "Insufficient intrabar coverage": "candle_direction",
}
"""Volume delta table labels by old method value."""


def _calendar(instrument_id: str) -> sessions.BarCalendar:
    """Returns the session calendar of an instrument.

    Args:
        instrument_id: Canonical instrument id.

    Returns:
        Its calendar.
    """
    return sessions.for_instrument(
        instruments.InstrumentId.parse(instrument_id)
    )


def _bars(
    rows: list[list[Any]],
    tf: timeframes.Timeframe,
    start_ms: int,
    instrument_id: str,
) -> list[dict[str, Any]]:
    """Builds consecutive script bars from fixture rows.

    Args:
        rows: ``[open, high, low, close, volume, buy?, method?]`` rows.
        tf: Bar resolution.
        start_ms: First bar start.
        instrument_id: Instrument whose calendar spaces the bars.

    Returns:
        Bars in the host's script shape.
    """
    calendar = _calendar(instrument_id)
    current = calendar.shift(tf, start_ms, 0)
    bars = []
    for row in rows:
        bars.append(
            {
                "time_ms": current,
                "end_ms": calendar.end_of(tf, current),
                "is_final": True,
                "open": float(row[0]),
                "high": float(row[1]),
                "low": float(row[2]),
                "close": float(row[3]),
                "volume": float(row[4]) if len(row) > 4 else 10.0,
                "quote_turnover": None,
                "trade_count": None,
                "taker_buy_volume": float(row[5]) if len(row) > 5 else None,
                "taker_buy_method": (
                    (str(row[6]) if len(row) > 6 else "taker")
                    if len(row) > 5
                    else None
                ),
            }
        )
        current = calendar.shift(tf, current, 1)
    return bars


def _inputs(
    cls: type[joseph_script.Indicator], settings: dict[str, Any]
) -> dict[str, Any]:
    """Converts old plugin settings to the script's typed inputs.

    Args:
        cls: Built-in script class.
        settings: Fixture settings (decimals as strings, ``tick_size``).

    Returns:
        Defaults overridden by the converted settings.
    """
    declared = cls.INPUTS
    inputs = {name: spec.default for name, spec in declared.items()}
    for name, value in settings.items():
        if name not in declared:
            continue
        default = declared[name].default
        if isinstance(default, bool):
            inputs[name] = bool(value)
        elif isinstance(default, float):
            inputs[name] = float(value)
        elif isinstance(default, int):
            inputs[name] = int(value)
        else:
            inputs[name] = value
    return inputs


def run_scenario(
    body: dict[str, Any],
) -> tuple[type[joseph_script.Indicator], dict[str, Any]]:
    """Runs one scenario's built-in script on its bars.

    Args:
        body: Parsed fixture.

    Returns:
        The script class and its raw result.
    """
    cls = cast(
        "type[joseph_script.Indicator]",
        builtin.script_class(f"indicators.{body['plugin_id']}"),
    )
    instrument_id = body.get("instrument_id", DEFAULT_INSTRUMENT)
    tf = timeframes.parse(body["timeframe"])
    start = int(body.get("start_ms", DEFAULT_START_MS))
    bars = _bars(body["bars"], tf, start, instrument_id)
    extra: dict[str, list[dict[str, Any]]] = {
        alias: [] for alias in cls.REQUESTS
    }
    for index, by_tf in sorted(
        body.get("intrabars", {}).items(), key=lambda item: int(item[0])
    ):
        for tf_text, rows in by_tf.items():
            extra["intrabars"].extend(
                _bars(
                    rows,
                    timeframes.parse(tf_text),
                    bars[int(index)]["time_ms"],
                    instrument_id,
                )
            )
    seen: dict[int, dict[str, Any]] = {}
    for by_tf in body.get("aux", {}).values():
        for tf_text, rows in by_tf.items():
            for row in rows:
                aux = _bars(
                    [row[1:6]],
                    timeframes.parse(tf_text),
                    int(row[0]),
                    instrument_id,
                )[0]
                seen[aux["time_ms"]] = aux
    if seen:
        extra["period"] = [seen[key] for key in sorted(seen)]
    bundle = {
        "kind": "indicator",
        "bars": bars,
        "inputs": _inputs(cls, body.get("settings", {})),
        "instrument_id": instrument_id,
        "timeframe": tf.value,
        "extra_series": extra,
        "periods": service.period_bounds(instrument_id, bars, cls.PERIODS),
    }
    return cls, worker.run_class(cls, bundle)


def _last(result: dict[str, Any], name: str) -> float | None:
    """Reads a plot's last value.

    Args:
        result: Raw script result.
        name: Plot name.

    Returns:
        The value on the last bar it was published on, None when absent.
    """
    for plot in result["plots"]:
        if plot["name"] == name and plot["points"]:
            value = plot["points"][-1]["value"]
            return None if value is None else float(value)
    return None


def _visual(result: dict[str, Any], visual_id: str) -> dict[str, Any] | None:
    """Finds a drawing by id.

    Args:
        result: Raw script result.
        visual_id: Drawing id.

    Returns:
        Its data, None when absent.
    """
    for visual in result["visuals"]:
        if visual["id"] == visual_id:
            return dict(visual["data"])
    return None


def _table_rows(result: dict[str, Any]) -> list[list[str]]:
    """Reads the rows of the result's tables.

    Args:
        result: Raw script result.

    Returns:
        Every table row, in drawing order.
    """
    rows: list[list[str]] = []
    for visual in result["visuals"]:
        if visual["kind"] == "table":
            rows.extend(visual["data"]["rows"])
    return rows


def _plots(names: dict[str, str]) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Builds a reader of last plot values.

    Args:
        names: Old value name to plot name.

    Returns:
        Reader of the values.
    """
    return lambda result: {
        key: _last(result, plot) for key, plot in names.items()
    }


def _volume_delta(result: dict[str, Any]) -> dict[str, Any]:
    """Reads volume delta values.

    Args:
        result: Raw script result.

    Returns:
        Old value names to values.
    """
    values = _plots(
        {
            "delta_pct": "Delta %",
            "delta": "Delta",
            "buy_volume": "Buy volume",
            "sell_volume": "Sell volume",
            "coverage_pct": "Coverage %",
            "intrabar_count": "Intrabars",
        }
    )(result)
    values["method"] = METHODS[_table_rows(result)[0][1]]
    return values


def _first_bar(result: dict[str, Any]) -> int | None:
    """Finds the anchor bar of an anchored VWAP.

    Args:
        result: Raw script result.

    Returns:
        Start of the bar whose ``Bars`` count is one, None without one.
    """
    for plot in result["plots"]:
        if plot["name"] == "Bars":
            for point in plot["points"]:
                if point["value"] == 1:
                    return int(point["time_ms"])
    return None


def _vwap(result: dict[str, Any]) -> dict[str, Any]:
    """Reads anchored VWAP values.

    Args:
        result: Raw script result.

    Returns:
        Old value names to values.
    """
    names = {"vwap": "VWAP", "stdev": "Stdev"}
    for index in (1, 2, 3):
        names[f"upper_{index}"] = f"Upper {index}"
        names[f"lower_{index}"] = f"Lower {index}"
    values = _plots(names)(result)
    values["anchor_ms"] = _first_bar(result)
    values["bars"] = int(_last(result, "Bars") or 0)
    values["anchor_in_window"] = not _table_rows(result)
    return values


def _regression(result: dict[str, Any]) -> dict[str, Any]:
    """Reads regression channel values.

    Args:
        result: Raw script result.

    Returns:
        Old value names to values.
    """
    values = _plots(
        {
            "slope": "Slope",
            "stdev": "Stdev",
            "pearson_r": "Pearson's R",
            "max_up": "Max up",
            "max_down": "Max down",
        }
    )(result)
    values["bars"] = int(_last(result, "Bars") or 0)
    values["anchor_in_window"] = not _table_rows(result)
    for name, line in (
        ("", "centre"),
        ("upper_", "upper"),
        ("lower_", "lower"),
    ):
        data = _visual(result, line) or {}
        values[f"{name}start"] = data.get("from_price")
        values[f"{name}end"] = data.get("to_price")
        if line == "centre":
            values["start_ms"] = data.get("from_ms")
    return values


def _pivot_period(result: dict[str, Any]) -> int:
    """Finds the start of the period the current pivot levels apply to.

    Args:
        result: Raw script result.

    Returns:
        Latest level line start.
    """
    return max(
        int(visual["data"]["from_ms"])
        for visual in result["visuals"]
        if visual["kind"] == "line"
    )


def _pivots(result: dict[str, Any]) -> dict[str, Any]:
    """Reads the current pivot levels.

    Args:
        result: Raw script result.

    Returns:
        Old value names to values.
    """
    applies = _pivot_period(result)
    values: dict[str, Any] = {
        key: (_visual(result, f"pivot:{applies}:{key}") or {}).get("from_price")
        for key in ("p", "r1", "r2", "r3", "s1", "s2", "s3")
    }
    values["period_start_ms"] = applies
    return values


READERS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "sma": _plots({"sma": "SMA"}),
    "ema": _plots({"ema": "EMA"}),
    "bollinger_bands": _plots(
        {
            "basis": "Basis",
            "upper": "Upper",
            "lower": "Lower",
            "bandwidth": "Bandwidth",
            "percent_b": "%B",
        }
    ),
    "rsi": _plots({"rsi": "RSI"}),
    "atr": _plots({"atr": "ATR"}),
    "volume_delta": _volume_delta,
    "anchored_vwap": _vwap,
    "regression_channel": _regression,
    "pivot_points": _pivots,
}
"""Reads each built-in's result back into its scenario value names."""


def _matches(key: str, actual: Any, expected: Any) -> bool:
    """Compares one value at its published precision.

    Args:
        key: Value name.
        actual: Script value.
        expected: Fixture value (decimal string, number, bool or None).

    Returns:
        Whether they agree.
    """
    if expected is None or isinstance(expected, bool):
        return bool(actual == expected)
    if isinstance(expected, str) and not _numeric(expected):
        return bool(actual == expected)
    if actual is None:
        return False
    places = PLACES.get(key, 6)
    if key.endswith("_ms") or key in {"bars", "intrabar_count"}:
        places = 0
    tolerance = decimal.Decimal(5).scaleb(-(places + 1)) + decimal.Decimal(
        "1e-9"
    )
    gap = abs(decimal.Decimal(str(actual)) - decimal.Decimal(str(expected)))
    return gap <= tolerance


def _numeric(text: str) -> bool:
    """Tells whether a fixture string is a decimal number.

    Args:
        text: Fixture value.

    Returns:
        True for decimal text.
    """
    try:
        decimal.Decimal(text)
    except decimal.InvalidOperation:
        return False
    return True


def _pivot_aoi(
    result: dict[str, Any], expected: dict[str, Any]
) -> dict[str, Any] | None:
    """Finds the drawn level an old pivot AOI expectation describes.

    Args:
        result: Raw script result.
        expected: Old AOI subset (kind, price, state, times).

    Returns:
        The level's kind, side, state, price and times, None when absent.
    """
    key = expected["kind"].removeprefix("PIVOT_").lower()
    current = _pivot_period(result)
    for visual in result["visuals"]:
        data = visual["data"]
        if visual["kind"] != "line" or not visual["id"].endswith(f":{key}"):
            continue
        if not _matches("lower", data["from_price"], expected["lower"]):
            continue
        applies = int(data["from_ms"])
        tested = any(
            alert["name"] == f"pivot_{key}" and alert["time_ms"] >= applies
            for alert in result["alerts"]
        )
        state = "tested" if tested else "active"
        if applies < current:
            state = "expired"
        side = {"p": "neutral", "r": "bearish", "s": "bullish"}[key[0]]
        return {
            "kind": expected["kind"],
            "side": side,
            "state": state,
            "lower": data["from_price"],
            "upper": data["to_price"],
            "created_at_ms": applies,
            "invalidated_at_ms": int(data["to_ms"]),
        }
    return None


def test_every_builtin_indicator_has_scenarios() -> None:
    """Each built-in indicator ships at least two golden scenarios."""
    counts: dict[str, int] = {}
    for path in SCENARIOS:
        plugin_id = json.loads(path.read_text(encoding="utf-8"))["plugin_id"]
        counts[plugin_id] = counts.get(plugin_id, 0) + 1
    built = catalog.build(catalog.CatalogConfig(packages="none"))
    ids = {
        entry.script_id.removeprefix("builtin:")
        for entry in built.entries("indicator")
    }
    assert set(counts) == ids
    assert all(count >= 2 for count in counts.values()), counts


@pytest.mark.parametrize(
    "path", SCENARIOS, ids=lambda p: f"{p.parent.name}/{p.stem}"
)
def test_scenario(path: pathlib.Path) -> None:
    """The script reproduces the scenario's final values and levels.

    Args:
        path: Scenario fixture.
    """
    body = json.loads(path.read_text(encoding="utf-8"))
    _, result = run_scenario(body)
    values = READERS[body["plugin_id"]](result)
    for key, expected in body["expect"].get("final_values", {}).items():
        assert _matches(key, values.get(key), expected), (
            key,
            values.get(key),
            expected,
        )
    for expected in body["expect"].get("final_aois", []):
        found = _pivot_aoi(result, expected)
        assert found is not None, expected
        for key, value in expected.items():
            if key == "confirmed_at_ms":
                continue
            assert _matches(key, found.get(key), value), (key, found, value)
