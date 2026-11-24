# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""System tests: Treasury rate series are never tradable.

Rejection is static validation, so it works offline although the Treasury
adapter (which needs the network) is not enabled in the DEMO market
engine: the strategy compiler refuses the venue and the instrument, the
strategy service refuses activation and backtests, and the execution
engine refuses orders.
"""

from __future__ import annotations

import json
import pathlib
from collections.abc import Callable

import httpx

from tests import conftest

UST10Y = "treasury:rate:UST10Y"
_FIXTURES = (
    conftest.ROOT / "joseph-terminal" / "packages" / "contracts" / "fixtures"
)

Validate = Callable[[str, str, object], None]


def _definition(
    strategy_id: str, universe: dict[str, object]
) -> dict[str, object]:
    """Builds the Long A fixture with another id and universe.

    Args:
        strategy_id: Slug.
        universe: Contract ``Universe``.

    Returns:
        Strategy definition.
    """
    path = pathlib.Path(_FIXTURES / "strategy.fixture.json")
    body = json.loads(path.read_text(encoding="utf-8"))
    definition = dict(body["valid"][0])
    definition.update(
        strategy_id=strategy_id,
        universe=universe,
        execution_mode="ALERT_ONLY",
    )
    return definition


def _rate_strategy(strategy_id: str) -> dict[str, object]:
    """Builds a strategy whose universe is the UST10Y rate series.

    Args:
        strategy_id: Slug.

    Returns:
        Strategy definition.
    """
    return _definition(
        strategy_id,
        {"qualification": "ANY", "venues": ["treasury"], "whitelist": [UST10Y]},
    )


def _codes(result: dict[str, object]) -> dict[str, str]:
    """Maps the error codes of a ``ValidationResult`` to their paths.

    Args:
        result: Contract ``ValidationResult``.

    Returns:
        Code to JSON pointer.
    """
    errors = result["errors"]
    assert isinstance(errors, list)
    return {e["code"]: e["path"] for e in errors}


def test_compiler_rejects_a_rate_universe(
    market: httpx.Client, validate: Validate
) -> None:
    """The treasury venue and UST10Y fail with the documented codes."""
    result = market.post(
        "/v1/strategies/validate", json=_rate_strategy("system-rate-ust10y")
    ).json()
    validate("strategy.schema.json", "ValidationResult", result)
    assert result["valid"] is False
    codes = _codes(result)
    assert codes["RATE_VENUE_NOT_TRADABLE"] == "/universe/venues/0"
    assert codes["RATE_NOT_TRADABLE"] == "/universe/whitelist/0"
    messages = " ".join(e["message"] for e in result["errors"])
    assert "bond ETF" in messages


def test_rate_whitelist_is_rejected_on_a_tradable_venue(
    market: httpx.Client, validate: Validate
) -> None:
    """UST10Y in the whitelist of a DEMO-venue universe is still refused."""
    definition = _definition(
        "system-rate-whitelist",
        {"qualification": "ANY", "venues": ["mock"], "whitelist": [UST10Y]},
    )
    result = market.post("/v1/strategies/validate", json=definition).json()
    validate("strategy.schema.json", "ValidationResult", result)
    assert result["valid"] is False
    assert _codes(result)["RATE_NOT_TRADABLE"] == "/universe/whitelist/0"


def test_stored_rate_strategy_cannot_be_activated(
    market: httpx.Client, validate: Validate
) -> None:
    """A saved rate draft revalidates with the codes and never activates."""
    created = market.post(
        "/v1/strategies", json=_rate_strategy("system-rate-draft")
    )
    assert created.status_code == 201, created.text
    validate("strategy.schema.json", "StrategyVersion", created.json())
    assert created.json()["state"] != "ACTIVE"
    revalidated = market.post(
        "/v1/strategies/system-rate-draft/validate", json={"version": 1}
    ).json()
    validate("strategy.schema.json", "ValidationResult", revalidated)
    assert {"RATE_VENUE_NOT_TRADABLE", "RATE_NOT_TRADABLE"} <= set(
        _codes(revalidated)
    )
    for mode in ("ALERT_ONLY", "PAPER"):
        response = market.post(
            "/v1/strategies/system-rate-draft/activate",
            json={"version": 1, "mode": mode},
        )
        assert response.status_code == 422, response.text
        body = response.json()
        validate("common.schema.json", "ErrorResponse", body)
        assert body["code"] == "VALIDATION_FAILED"
        fields = {f["field"] for f in body["field_errors"]}
        assert fields == {"/universe/venues/0", "/universe/whitelist/0"}


def test_rate_series_cannot_be_backtested(
    market: httpx.Client, validate: Validate
) -> None:
    """A valid DEMO strategy refuses a backtest on UST10Y."""
    definition = _definition(
        "system-rate-backtest", {"qualification": "ANY", "venues": ["mock"]}
    )
    created = market.post("/v1/strategies", json=definition)
    assert created.status_code == 201, created.text
    response = market.post(
        "/v1/strategies/system-rate-backtest/backtests",
        json={
            "version": 1,
            "instrument_id": UST10Y,
            "from_ms": conftest.ny_ms(2026, 11, 2),
            "to_ms": conftest.ny_ms(2026, 11, 3),
        },
    )
    assert response.status_code == 422, response.text
    validate("common.schema.json", "ErrorResponse", response.json())
    assert "published rate series" in response.json()["message"]


def test_execution_engine_refuses_rate_orders(
    execution: httpx.Client, validate: Validate
) -> None:
    """A paper order on UST10Y fails routing as a non-tradable series."""
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
