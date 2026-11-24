# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Unit tests for trade de-duplication, truncation and coverage helpers."""

from __future__ import annotations

import decimal
from collections.abc import Callable
from typing import TypeAlias

import pytest

from joseph.domain import instruments
from joseph.domain import market
from joseph.market_data.domain import trades as trades_lib

_ID = instruments.InstrumentId.parse("lighter:perp:BTC")
_D = decimal.Decimal
Validate: TypeAlias = Callable[[str, str, object], None]
"""``contract_validate(schema_file, definition, instance)`` from conftest."""


def _trade(trade_id: int, ts: int) -> market.Trade:
    """Builds a Lighter-shaped buy trade received 5 ms after it happened.

    Args:
        trade_id: Venue trade id, converted with ``str``.
        ts: Venue event time, epoch milliseconds.

    Returns:
        The trade.
    """
    return market.Trade(
        instrument_id=_ID,
        trade_id=str(trade_id),
        venue_event_ts_ms=ts,
        received_at_ms=ts + 5,
        price=_D("61065.7"),
        base_quantity=_D("0.00020"),
        aggressor_side=market.AggressorSide.BUY,
        is_aggregated=False,
        quote_quantity=_D("12.213140"),
    )


def test_recent_ids_forget_oldest_and_report_membership() -> None:
    """The bounded id set keeps the newest ids and filters repeated trades."""
    seen = trades_lib.RecentIds(2)
    assert seen.add("a") and seen.add("b")
    assert not seen.add("a")
    assert seen.add("c")
    assert "a" not in seen and "c" in seen
    assert len(seen) == 2
    assert [t.trade_id for t in seen.fresh([_trade(1, 1), _trade(1, 1)])] == [
        "1"
    ]
    with pytest.raises(ValueError, match="positive"):
        trades_lib.RecentIds(0)


def test_keep_latest_cuts_at_whole_milliseconds() -> None:
    """Truncation never leaves a partial millisecond at the start."""
    rows = [_trade(1, 10), _trade(2, 20), _trade(3, 20), _trade(4, 30)]
    assert trades_lib.keep_latest(rows, None) == rows
    assert trades_lib.keep_latest(rows, 9) == rows
    assert [t.trade_id for t in trades_lib.keep_latest(rows, 2)] == ["4"]
    assert [t.trade_id for t in trades_lib.keep_latest(rows, 3)] == [
        "2",
        "3",
        "4",
    ]
    assert trades_lib.keep_latest(rows, 0) == []
    same = [_trade(1, 5), _trade(2, 5)]
    assert len(trades_lib.keep_latest(same, 1)) == 1


def test_coverage_rejects_inverted_or_outside_trades() -> None:
    """Coverage rejects inverted spans and trades outside its span."""
    with pytest.raises(ValueError, match="ends before"):
        trades_lib.TradeCoverage((), 10, 5)
    with pytest.raises(ValueError, match="outside"):
        trades_lib.TradeCoverage((_trade(1, 10),), 0, 10)
    assert trades_lib.TradeCoverage((), 5, 5).is_empty


def test_suffix_and_prefix_coverage_drop_cut_milliseconds() -> None:
    """Incomplete paging keeps only whole milliseconds of the range."""
    rows = [_trade(1, 10), _trade(2, 10), _trade(3, 20), _trade(4, 30)]
    suffix = trades_lib.coverage_from_suffix(rows, 0, 40, complete=False)
    assert (suffix.from_ms, suffix.to_ms) == (11, 40)
    assert [t.trade_id for t in suffix.trades] == ["3", "4"]
    full = trades_lib.coverage_from_suffix(rows, 0, 40, complete=True)
    assert (full.from_ms, len(full.trades)) == (0, 4)
    assert trades_lib.coverage_from_suffix([], 0, 40, False).from_ms == 40
    prefix = trades_lib.coverage_from_prefix(rows, 0, 40, complete=False)
    assert (prefix.from_ms, prefix.to_ms) == (0, 30)
    assert [t.trade_id for t in prefix.trades] == ["1", "2", "3"]
    assert trades_lib.coverage_from_prefix([], 0, 40, False).to_ms == 0
    assert len(trades_lib.coverage_from_prefix(rows, 0, 25, True).trades) == 3


def test_trade_contract_is_schema_valid(contract_validate: Validate) -> None:
    """Trades serialize to a valid contract ``Trade`` with plain decimals."""
    payload = trades_lib.to_contract(_trade(42, 1_795_348_718_291))
    contract_validate("market.schema.json", "Trade", payload)
    assert payload["base_quantity"] == "0.0002"
    assert payload["aggressor_side"] == "buy"
