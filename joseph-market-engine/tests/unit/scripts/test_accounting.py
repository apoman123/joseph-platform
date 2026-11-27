# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Trusted reconciliation rejects fabricated financial simulation outputs."""

from __future__ import annotations

import pytest

from joseph.domain import errors
from joseph.domain import timeframes
from joseph.scripts import accounting
from joseph.scripts import models

BARS = [
    {"time_ms": 0.0, "open": 100.0, "close": 100.0},
    {"time_ms": 1000.0, "open": 110.0, "close": 120.0},
    {"time_ms": 2000.0, "open": 115.0, "close": 115.0},
]
"""Authoritative source prices, independent of the user-code output."""


def _result() -> models.ScriptResult:
    """Constructs a valid fill ledger with intentionally fabricated balances.

    Returns:
        Two source-bar fills and untrusted performance claiming excess profit.
    """
    return models.ScriptResult(
        script_id="audit",
        version=1,
        kind="strategy",
        source_hash="test",
        instrument_id="mock:perp:BTCUSD",
        tf="1s",
        bars_count=3,
        feed_state="DEMO",
        data_source="mock",
        complete=True,
        overlay=True,
        plots=[],
        fills=[
            models.SimulatedFill(
                time_ms=1000,
                side="buy",
                quantity=1,
                price=110,
                fee=0,
                effect="entry",
            ),
            models.SimulatedFill(
                time_ms=2000,
                side="sell",
                quantity=1,
                price=115,
                fee=0,
                effect="exit",
            ),
        ],
        equity_curve=[],
        metrics={"final_equity": 1e9},
    )


def test_reconciles_performance_from_source_prices() -> None:
    """Recomputes profit and equity from authoritative fill data."""
    request = models.ScriptRun(
        instrument_id="mock:perp:BTCUSD", tf=timeframes.Timeframe.S1
    )
    result = accounting.reconcile(_result(), BARS, request)
    assert result.metrics["final_equity"] == 10005
    assert result.metrics["net_profit"] == 5
    assert [point.value for point in result.equity_curve] == [
        10000,
        10010,
        10005,
    ]


@pytest.mark.parametrize(
    "change", ["price", "fee", "first_bar", "effect", "insolvent"]
)
def test_rejects_impossible_fill_ledgers(change: str) -> None:
    """Rejects invented fills before presenting a financial performance report.

    Args:
        change: Fabricated price, cost, execution bar, effect or buying power.
    """
    result = _result()
    request = models.ScriptRun(
        instrument_id="mock:perp:BTCUSD", tf=timeframes.Timeframe.S1
    )
    if change == "price":
        result.fills[0].price = 1
    elif change == "fee":
        result.fills[0].fee = 999
    elif change == "first_bar":
        result.fills[0].time_ms = 0
    elif change == "effect":
        result.fills[0].effect = "exit"
    else:
        request.initial_equity = 1
    with pytest.raises(errors.ValidationFailedError):
        accounting.reconcile(result, BARS, request)
