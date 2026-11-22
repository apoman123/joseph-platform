"""Full chain: live pipeline → signal → execution forwarder → paper order.

An always-true 1m PAPER strategy is activated on the DEMO venue and bound to
a PAPER deployment. The market engine's live pipeline must fire on the next
closed bar (never on history), forward the entry to the execution engine,
and the execution engine must create exactly one sized paper order.
"""

from __future__ import annotations

import time

import httpx

MOCK_BTC = "mock:perp:BTCUSD"
PAPER = "acct-paper-tenant-a"
STRATEGY = "system-e2e-chain"


def _definition() -> dict[str, object]:
    """Builds an always-true 1m PAPER strategy with a computable R:R.

    Returns:
        Strategy definition.
    """
    return {
        "schema_version": 2,
        "strategy_id": STRATEGY,
        "name": "End-to-end chain probe",
        "direction": "LONG",
        "signal_timeframe": "1m",
        "universe": {"qualification": "ANY", "venues": ["mock"]},
        "when": {
            "op": "COMPARE",
            "left": {"kind": "candle", "field": "close"},
            "cmp": "gt",
            "right": {"kind": "const", "value": "0"},
        },
        "risk": {
            "min_rr": "1.0",
            "stop_model": "ATR",
            "stop_buffer_atr": "1",
            "target_model": "FIXED_R",
            "fixed_r": "2",
        },
        "notification": {
            "cooldown_bars": 1000,
            "once_per_aoi": False,
            "emit": ["ENTRY_CONFIRMED"],
            "channels": ["UI"],
        },
        "execution_mode": "PAPER",
    }


def test_pipeline_signal_becomes_one_paper_order(
    market: httpx.Client, execution: httpx.Client
) -> None:
    """A live entry reaches the paper account exactly once."""
    created = market.post("/v1/strategies", json=_definition())
    assert created.status_code == 201, created.text
    version_id = created.json()["strategy_version_id"]
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
    activated_at = int(time.time() * 1000)
    activated = market.post(
        f"/v1/strategies/{STRATEGY}/activate",
        json={"version": 1, "mode": "PAPER", "instruments": [MOCK_BTC]},
    )
    assert activated.status_code == 200, activated.text

    deadline = time.monotonic() + 150
    signals: list[dict[str, object]] = []
    orders: list[dict[str, object]] = []
    while time.monotonic() < deadline:
        signals = market.get(
            "/v1/signals", params={"strategy_id": STRATEGY, "limit": 50}
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
    market.post(f"/v1/strategies/{STRATEGY}/pause", json={})
    assert signals, "pipeline produced no signal for a newly closed bar"
    assert all(s["bar_close_ms"] > activated_at - 60_000 for s in signals)
    assert all(s["kind"] == "ENTRY_CONFIRMED" for s in signals)
    assert len(orders) == 1, orders
    assert orders[0]["signal_id"] == signals[-1]["signal_id"]
    assert orders[0]["quantity_base"] in ("0.002", "0.0020")
