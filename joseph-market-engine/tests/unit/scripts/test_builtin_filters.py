# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Built-in Filter scripts on deterministic snapshots and closed bars."""

from __future__ import annotations

from typing import Any

import pytest

import joseph_script
from joseph.scripts import builtin
from joseph.scripts import worker
from joseph.scripts.builtin.filters import rsi as rsi_filter

_NOW = 1_795_278_000_000
_ID = "binance:perp:BTCUSDT"


def _bars(closes: list[float], **fields: list[float]) -> list[dict[str, Any]]:
    """Builds closed hourly bars from closes.

    Args:
        closes: Closing prices, oldest first.
        **fields: Optional open, high, low or volume lists of equal length.

    Returns:
        Bar records; open and close equal, high/low one above/below.
    """
    rows = []
    for index, close in enumerate(closes):
        rows.append(
            {
                "time_ms": index * 3_600_000,
                "end_ms": (index + 1) * 3_600_000,
                "is_final": True,
                "open": fields.get("open", closes)[index],
                "high": fields.get("high", [c + 1 for c in closes])[index],
                "low": fields.get("low", [c - 1 for c in closes])[index],
                "close": close,
                "volume": fields.get("volume", [100.0] * len(closes))[index],
            }
        )
    return rows


def _match(
    module: str,
    *,
    snapshot: dict[str, Any] | None = None,
    bars: list[dict[str, Any]] | None = None,
    instrument_id: str = _ID,
    **inputs: Any,
) -> bool:
    """Runs one built-in filter in process.

    Args:
        module: Module name below ``builtin.filters``.
        snapshot: Snapshot contract fields.
        bars: Closed bars.
        instrument_id: Candidate instrument.
        **inputs: Input overrides.

    Returns:
        The filter decision.
    """
    cls = builtin.script_class(f"filters.{module}")
    assert issubclass(cls, joseph_script.Filter)
    defaults = {name: spec.default for name, spec in cls.INPUTS.items()}
    result = worker.run_class(
        cls,
        {
            "kind": "filter",
            "bars": bars or [],
            "snapshot": {"evaluated_at_ms": _NOW, **(snapshot or {})},
            "instrument": {"instrument_id": instrument_id},
            "instrument_id": instrument_id,
            "inputs": {**defaults, **inputs},
        },
    )
    return bool(result["matched"])


@pytest.mark.parametrize(
    ("module", "field"),
    [
        ("price", "last"),
        ("change", "change_24h_pct"),
        ("volume", "volume_24h_base"),
        ("turnover", "turnover_24h_quote"),
    ],
)
def test_snapshot_filters_compare_with_operators(
    module: str, field: str
) -> None:
    """Above and below are strict; between is inclusive and order-free.

    Args:
        module: Filter module.
        field: Snapshot field it reads.
    """
    snapshot = {field: "50"}
    assert _match(module, snapshot=snapshot, operator="above", value=49)
    assert not _match(module, snapshot=snapshot, operator="above", value=50)
    assert _match(module, snapshot=snapshot, operator="below", value=51)
    assert not _match(module, snapshot=snapshot, operator="below", value=50)
    assert _match(
        module, snapshot=snapshot, operator="between", value=60, value2=50
    )
    assert not _match(
        module, snapshot=snapshot, operator="between", value=10, value2=49
    )


@pytest.mark.parametrize("module", ["price", "change", "volume", "turnover"])
def test_missing_snapshot_value_is_unavailable(module: str) -> None:
    """A missing value is never a match.

    Args:
        module: Filter module.
    """
    with pytest.raises(joseph_script.Unavailable):
        _match(module, snapshot={"last": None})


def test_price_falls_back_to_the_mark() -> None:
    """Without a trade the mark price is compared."""
    assert _match("price", snapshot={"mark": "12"}, value=10)


def test_relative_volume_uses_the_preceding_average() -> None:
    """Latest volume over the mean of the bars before it."""
    bars = _bars([10.0] * 6, volume=[100.0] * 5 + [300.0])
    assert _match("relative_volume", bars=bars, length=5)
    assert not _match("relative_volume", bars=bars, length=5, value=3.0)
    with pytest.raises(joseph_script.Unavailable, match="needs 6"):
        _match("relative_volume", bars=bars[:5], length=5)


def test_gap_measures_open_against_previous_close() -> None:
    """A 4 % gap up passes above 2 % and fails below."""
    bars = _bars([100.0, 104.0], open=[100.0, 104.0])
    assert _match("gap", bars=bars, operator="above", value=3.9)
    assert not _match("gap", bars=bars, operator="below", value=0)
    with pytest.raises(joseph_script.Unavailable):
        _match("gap", bars=bars[:1])


def test_price_vs_moving_average() -> None:
    """Price above the simple and exponential average of rising closes."""
    bars = _bars([float(value) for value in range(1, 21)])
    snapshot = {"last": "25"}
    assert _match("price_vs_ma", bars=bars, snapshot=snapshot, length=10)
    assert _match(
        "price_vs_ma", bars=bars, snapshot=snapshot, length=10, ma_type="EMA"
    )
    assert not _match(
        "price_vs_ma",
        bars=bars,
        snapshot={"last": "5"},
        length=10,
        position="above",
    )
    assert _match(
        "price_vs_ma",
        bars=bars,
        snapshot={"last": "5"},
        length=10,
        position="below",
    )
    with pytest.raises(joseph_script.Unavailable, match="price"):
        _match("price_vs_ma", bars=bars, snapshot={}, length=10)


def test_rsi_of_rising_and_falling_closes() -> None:
    """Only gains give RSI 100, only losses 0."""
    rising = _bars([float(value) for value in range(1, 30)])
    falling = _bars([float(value) for value in range(30, 1, -1)])
    assert _match("rsi", bars=rising, operator="above", value=99)
    assert _match("rsi", bars=falling, operator="below", value=1)
    with pytest.raises(joseph_script.Unavailable):
        _match("rsi", bars=rising[:10])


def test_rsi_matches_wilder_reference() -> None:
    """Exact Wilder RSI of the StockCharts example (rounded there: 70.53)."""
    closes = [
        44.34,
        44.09,
        44.15,
        43.61,
        44.33,
        44.83,
        45.10,
        45.42,
        45.84,
        46.08,
        45.89,
        46.03,
        45.61,
        46.28,
        46.28,
    ]
    assert rsi_filter.rsi(closes, 14) == pytest.approx(70.464, abs=0.001)


def test_new_high_low_includes_the_latest_bar() -> None:
    """The latest high must be the window's highest."""
    bars = _bars([1.0, 2.0, 3.0, 4.0])
    assert _match("new_high_low", bars=bars, length=4)
    assert not _match("new_high_low", bars=bars, length=4, extreme="low")
    assert _match(
        "new_high_low",
        bars=_bars([4.0, 3.0, 2.0, 1.0]),
        length=4,
        extreme="low",
    )
    with pytest.raises(joseph_script.Unavailable):
        _match("new_high_low", bars=bars, length=5)


def test_scan_loop_reports_unavailable_separately() -> None:
    """The worker marks Unavailable decisions instead of failing the batch."""
    cls = builtin.script_class("filters.price")
    assert issubclass(cls, joseph_script.Filter)
    output = worker.run_class(
        cls,
        {
            "kind": "filter",
            "inputs": {"operator": "above", "value": 1, "value2": 2},
            "symbols": [
                {
                    "instrument_id": "mock:perp:BTCUSD",
                    "snapshot": {"last": "5"},
                    "bars": [],
                },
                {
                    "instrument_id": "mock:perp:ETHUSD",
                    "snapshot": {},
                    "bars": [],
                },
            ],
        },
    )
    first, second = output["results"]
    assert first == {
        "instrument_id": "mock:perp:BTCUSD",
        "matched": True,
        "error": None,
    }
    assert second["unavailable"] is True and not second["matched"]
    assert "unavailable" in second["error"]
