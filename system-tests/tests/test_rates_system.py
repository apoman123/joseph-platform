# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""System tests: Treasury rate series are never tradable.

Rejection is static validation, so it works offline although the Treasury
adapter (which needs the network) is not enabled in the DEMO market
engine: the market engine refuses strategy activations and backtests on a
rate series, and the execution engine refuses orders.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeAlias

import httpx

UST10Y = "treasury:rate:UST10Y"

_Validate: TypeAlias = Callable[[str, str, object], None]


def test_rate_series_cannot_be_activated(
    market: httpx.Client, validate: _Validate
) -> None:
    """A strategy activation on UST10Y is refused in every mode."""
    for mode in ("ALERT_ONLY", "PAPER", "SHADOW"):
        response = market.post(
            "/v1/strategy-activations",
            json={
                "script_id": "builtin:ema-trend-long",
                "instrument_ids": [UST10Y],
                "tf": "1d",
                "mode": mode,
            },
        )
        assert response.status_code == 422, response.text
        body = response.json()
        validate("common.schema.json", "ErrorResponse", body)
        assert body["code"] == "VALIDATION_FAILED"
        assert "bond ETF" in body["message"]
    listing = market.get("/v1/strategy-activations").json()
    assert all(
        UST10Y not in a["instrument_ids"] for a in listing["activations"]
    )


def test_rate_series_cannot_be_backtested(
    market: httpx.Client, validate: _Validate
) -> None:
    """A built-in strategy refuses a backtest on UST10Y."""
    response = market.post(
        "/v1/scripts/builtin:ema-trend-long/run",
        json={"instrument_id": UST10Y, "tf": "1d", "limit": 100},
    )
    assert 400 <= response.status_code < 500, response.text
    validate("common.schema.json", "ErrorResponse", response.json())


def test_execution_engine_refuses_rate_orders(
    execution: httpx.Client, validate: _Validate
) -> None:
    """A paper order preview on UST10Y fails routing as non-tradable."""
    preview = execution.post(
        "/v1/orders/preview",
        json={
            "account_id": "acct-paper-tenant-a",
            "instrument_id": UST10Y,
            "side": "buy",
            "position_effect": "open",
            "order_type": "limit",
            "quantity_base": "1",
            "limit_price": "4.25",
            "time_in_force": "GTC",
            "reduce_only": False,
            "leverage": "1",
            "mode": "PAPER",
            "source": "MANUAL_TICKET",
        },
    )
    assert preview.status_code == 200, preview.text
    body = preview.json()
    validate("execution.schema.json", "OrderPreview", body)
    assert body["allowed"] is False
    routing = next(
        c for c in body["risk"]["checks"] if c["check"] == "routing.venue"
    )
    assert routing["outcome"] == "FAIL"
    assert routing["code"] == "UNSUPPORTED"
    assert "not a tradable instrument" in routing["message"]
