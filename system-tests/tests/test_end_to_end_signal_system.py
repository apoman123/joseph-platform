# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Full chain: strategy activation → signal → execution forwarder → order.

The probe plugin strategy (``conftest.PROBE_ID``, loaded through the
``joseph.scripts`` entry-point group) is activated in PAPER mode on the
DEMO venue and bound to a PAPER deployment. It queues exactly one long
entry, on the first 1m bar starting at or after the next minute, so the
market engine's activation loop must signal it when that bar closes (never
on history), forward the entry to the execution engine, and the execution
engine must create exactly one sized paper order. A built-in strategy
activated on the same venue must evaluate without errors.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any
from typing import TypeAlias

import httpx

from tests import conftest

MOCK_BTC = "mock:perp:BTCUSD"
PAPER = "acct-paper-tenant-a"
_Validate: TypeAlias = Callable[[str, str, object], None]


def _deploy(execution: httpx.Client, version_id: str) -> None:
    """Binds a PAPER deployment with a fixed quantity to a strategy version.

    Args:
        execution: Execution-engine client of tenant A.
        version_id: The activation's ``strategy_version_id``.
    """
    deployment = execution.post(
        "/v1/deployments",
        json={
            "strategy_version_id": version_id,
            "account_id": PAPER,
            "mode": "PAPER",
            "limits": {
                "max_notional": "50000",
                "max_open_positions": 5,
                "max_daily_loss": "5000",
                "allowed_instruments": [MOCK_BTC],
                "max_leverage": "5",
            },
            "sizing": {"model": "FIXED_QTY", "fixed_qty": "0.002"},
        },
    )
    assert deployment.status_code in (200, 201), deployment.text


def test_probe_catalog_entry_is_a_plugin_strategy(
    market: httpx.Client,
) -> None:
    """The entry-point plugin is listed next to the built-in strategies."""
    listing = market.get(
        "/v1/scripts/catalog", params={"kind": "strategy"}
    ).json()
    rows = {row["script_id"]: row for row in listing["scripts"]}
    assert rows[conftest.PROBE_ID]["origin"] == "plugin"
    assert rows[conftest.PROBE_ID]["source_available"] is False
    assert {
        "builtin:ema-trend-long",
        "builtin:rsi-reversion-short",
        "builtin:pivot-bounce-long",
    } <= set(rows)


def test_activation_signal_becomes_one_paper_order(
    market: httpx.Client, execution: httpx.Client, validate: _Validate
) -> None:
    """A live entry on a newly closed bar becomes exactly one paper order."""
    after_ms = (int(time.time() * 1000) // 60_000 + 1) * 60_000
    created = market.post(
        "/v1/strategy-activations",
        json={
            "script_id": conftest.PROBE_ID,
            "instrument_ids": [MOCK_BTC],
            "tf": "1m",
            "mode": "PAPER",
            "inputs": {"after_ms": after_ms},
            "history_bars": 50,
        },
    )
    assert created.status_code == 201, created.text
    activation = created.json()
    validate("python-scripts.schema.json", "StrategyActivation", activation)
    version_id = activation["strategy_version_id"]
    assert version_id.startswith(f"{conftest.PROBE_ID}:v")
    _deploy(execution, version_id)

    deadline = time.monotonic() + 200
    signals: list[dict[str, Any]] = []
    orders: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        signals = market.get(
            "/v1/signals",
            params={"strategy_id": conftest.PROBE_ID, "limit": 50},
        ).json()["signals"]
        orders = [
            o
            for o in execution.get(
                "/v1/orders", params={"account_id": PAPER}
            ).json()["orders"]
            if o.get("strategy_version_id") == version_id
            and o["role"] == "PRIMARY"
        ]
        if signals and orders:
            break
        time.sleep(2)
    market.post(f"/v1/strategy-activations/{activation['activation_id']}/pause")
    assert signals, "activation produced no signal for a newly closed bar"
    assert len(signals) == 1, signals
    signal = signals[0]
    validate("signal.schema.json", "Signal", signal)
    assert signal["kind"] == "ENTRY_CONFIRMED"
    assert signal["direction"] == "LONG"
    assert signal["mode"] == "PAPER"
    assert signal["bar_start_ms"] >= after_ms
    assert signal["strategy_version_id"] == version_id
    assert signal["stop_price"] is not None
    assert len(orders) == 1, orders
    assert orders[0]["signal_id"] == signal["signal_id"]
    assert orders[0]["quantity_base"] in ("0.002", "0.0020")


def test_builtin_strategy_activation_evaluates_cleanly(
    market: httpx.Client, validate: _Validate
) -> None:
    """A built-in PAPER activation passes an evaluation without errors."""
    created = market.post(
        "/v1/strategy-activations",
        json={
            "script_id": "builtin:ema-trend-long",
            "instrument_ids": [MOCK_BTC],
            "tf": "1m",
            "mode": "PAPER",
            "history_bars": 200,
        },
    )
    assert created.status_code == 201, created.text
    activation_id = created.json()["activation_id"]
    deadline = time.monotonic() + 60
    body: dict[str, Any] = created.json()
    while time.monotonic() < deadline and body["last_checked_ms"] is None:
        time.sleep(1)
        body = market.get(f"/v1/strategy-activations/{activation_id}").json()
    assert body["last_checked_ms"] is not None, body
    assert body["last_error"] is None, body
    assert body["instruments"][MOCK_BTC]["last_error"] is None, body
    listing = market.get("/v1/strategy-activations").json()
    validate("python-scripts.schema.json", "StrategyActivationList", listing)
    deleted = market.delete(f"/v1/strategy-activations/{activation_id}")
    assert deleted.status_code == 204
