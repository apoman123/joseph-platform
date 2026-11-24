# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Unit tests for the shared domain kernel."""

from __future__ import annotations

import decimal
from collections.abc import Callable

import hypothesis
import pytest
from hypothesis import strategies

from joseph.domain import decimals
from joseph.domain import ids
from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import quality
from joseph.domain import timeframes

_BTC = instruments.InstrumentId.parse("binance:perp:BTCUSDT")


def _candle(**overrides: object) -> market.Candle:
    """Builds a valid 5m candle with optional field overrides.

    Args:
        **overrides: Field values replacing the defaults.

    Returns:
        A final, live Binance BTCUSDT perpetual candle.
    """
    fields: dict[str, object] = {
        "instrument_id": _BTC,
        "timeframe": timeframes.Timeframe.M5,
        "start_ms": 1_795_252_800_000,
        "end_ms": 1_795_253_100_000,
        "open": decimal.Decimal("100"),
        "high": decimal.Decimal("110"),
        "low": decimal.Decimal("95"),
        "close": decimal.Decimal("105"),
        "base_volume": decimal.Decimal("12.5"),
        "is_final": True,
        "source": "binance.rest",
        "feed_state": quality.FeedState.LIVE,
        "received_at_ms": 1_795_253_100_050,
    }
    fields.update(overrides)
    return market.Candle(**fields)  # type: ignore[arg-type]  # overrides are typed object; tests pass each field's real type


def test_instrument_id_round_trips() -> None:
    """Canonical ids parse and format losslessly."""
    parsed = instruments.InstrumentId.parse("hyperliquid:perp:BTC")
    assert str(parsed) == "hyperliquid:perp:BTC"
    assert parsed.venue is instruments.Venue.HYPERLIQUID


def test_same_symbol_on_two_venues_is_two_instruments() -> None:
    """BTC on Binance and BTC on Hyperliquid never compare equal."""
    binance = instruments.InstrumentId.parse("binance:perp:BTC")
    hyper = instruments.InstrumentId.parse("hyperliquid:perp:BTC")
    assert binance != hyper


@pytest.mark.parametrize("text", ["BTCUSDT", "binance:BTCUSDT", "x:perp:BTC"])
def test_instrument_id_rejects_ambiguous_symbols(text: str) -> None:
    """Bare or malformed symbols are rejected."""
    with pytest.raises(ValueError):
        instruments.InstrumentId.parse(text)


def test_candle_rejects_misaligned_bounds() -> None:
    """Bounds not aligned to the timeframe are rejected."""
    with pytest.raises(ValueError):
        _candle(start_ms=1_795_252_800_001, end_ms=1_795_253_100_001)


def test_candle_rejects_close_outside_range() -> None:
    """A close above the high is rejected."""
    with pytest.raises(ValueError):
        _candle(close=decimal.Decimal("120"))


def test_candle_contract(
    contract_validate: Callable[[str, str, object], None],
) -> None:
    """Candle serialization conforms to the JSON Schema."""
    contract_validate("market.schema.json", "Candle", _candle().to_contract())


def test_last_closed_start_prevents_htf_lookahead() -> None:
    """A 1h bar becomes visible only after it closes."""
    h1 = timeframes.Timeframe.H1
    ten = 1_795_255_200_000  # aligned hour boundary
    assert ten % h1.duration_ms == 0
    # At 10:55 the 10:00 bar is still open, so 09:00 is the last closed.
    assert timeframes.last_closed_start(h1, ten + 55 * 60_000) == (
        ten - h1.duration_ms
    )
    # At exactly 11:00 the 10:00 bar has closed.
    assert timeframes.last_closed_start(h1, ten + h1.duration_ms) == ten


def test_weekly_bars_start_on_monday() -> None:
    """Weekly alignment anchors on Monday 00:00 UTC."""
    monday = 1_794_787_200_000  # 2026-11-16 00:00 UTC (Monday)
    assert timeframes.Timeframe.W1.floor(monday + 3 * 86_400_000) == monday


@hypothesis.given(
    strategies.decimals(
        min_value=decimal.Decimal("-1e12"),
        max_value=decimal.Decimal("1e12"),
        allow_nan=False,
        allow_infinity=False,
        places=8,
    )
)
def test_format_decimal_round_trips(value: decimal.Decimal) -> None:
    """Formatting never uses exponents and parses back exactly."""
    text = decimals.format_decimal(value)
    assert "E" not in text and "e" not in text
    assert decimal.Decimal(text) == value


def test_to_decimal_avoids_binary_float_expansion() -> None:
    """Floats convert through repr, not their binary value."""
    assert decimals.to_decimal(0.1) == decimal.Decimal("0.1")


def test_floor_to_step_never_rounds_up() -> None:
    """Lot rounding floors the quantity."""
    assert decimals.floor_to_step(
        decimal.Decimal("0.01999"), decimal.Decimal("0.001")
    ) == decimal.Decimal("0.019")


def test_uuid7_is_time_ordered() -> None:
    """UUIDv7 values sort in generation order and carry version 7."""
    generated = [ids.uuid7() for _ in range(200)]
    assert generated == sorted(generated)
    assert all(value[14] == "7" for value in generated)


def test_stable_id_is_deterministic() -> None:
    """Identical inputs yield identical ids; different inputs do not."""
    assert ids.stable_id("zone", 1, "a") == ids.stable_id("zone", 1, "a")
    assert ids.stable_id("zone", 1, "a") != ids.stable_id("zone", 2, "a")
