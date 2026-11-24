# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Integration tests for strategy signal intake (market -> execution)."""

from __future__ import annotations

import collections.abc
import decimal
import json
import pathlib
from typing import Any

import pytest
from fastapi import testclient

from joseph_execution import bootstrap
from joseph_execution.domain import deployments
from joseph_execution.domain import enums
from joseph_execution.testing import harness

D = decimal.Decimal
_FIXTURE = (
    pathlib.Path(__file__).resolve().parents[3]
    / "joseph-terminal"
    / "packages"
    / "contracts"
    / "fixtures"
    / "signal.fixture.json"
)


def _signal(h: harness.Harness, **overrides: object) -> dict[str, object]:
    """Builds a fresh contract Signal for tenant A on the mock BTC perp.

    Args:
        h: Harness whose clock dates the bar close (one second ago).
        **overrides: Signal fields that replace the defaults.

    Returns:
        A paper entry signal body at 95000 with stop 94000 and target 97000.
    """
    base = json.loads(_FIXTURE.read_text())["valid"][0]
    base.update(
        {
            "tenant_id": "tenant-a",
            "instrument_id": harness.BTC,
            "strategy_version_id": "discount-ote-reversal:v3",
            "bar_close_ms": h.clock.now_ms() - 1_000,
            "entry_price": "95000",
            "stop_price": "94000",
            "target_price": "97000",
            "mode": "PAPER",
        }
    )
    base.update(overrides)
    return base


def _deploy(
    h: harness.Harness,
    mode: enums.DeploymentMode = enums.DeploymentMode.PAPER,
    instruments: tuple[str, ...] = (),
) -> deployments.Deployment:
    """Deploys the test strategy on tenant A's paper account.

    Limits: 50000 max notional, 3 open positions, 1000 daily loss, 5x
    leverage; sizing risks 0.5% of equity per signal.

    Args:
        h: Harness.
        mode: Deployment mode.
        instruments: Instrument allow-list; empty allows any instrument
            outside AUTO_LIVE.

    Returns:
        The enabled deployment.
    """
    return h.container.services.deployments.create(
        harness.TENANT_A,
        "discount-ote-reversal:v3",
        harness.PAPER_A,
        mode,
        deployments.DeploymentLimits(D(50000), 3, D(1000), instruments, D(5)),
        deployments.SizingModel("RISK_PCT", risk_pct=D("0.5")),
    )


@pytest.fixture
def ctx() -> collections.abc.Iterator[
    tuple[harness.Harness, testclient.TestClient]
]:
    """Provides a harness and a test client for its app.

    Yields:
        ``(harness, client)``; the app's lifespan runs until teardown.
    """
    h = harness.build()
    with testclient.TestClient(bootstrap.build_app(h.container)) as client:
        yield h, client


def _post(
    client: testclient.TestClient,
    body: dict[str, object],
    token: str = harness.SERVICE_TOKEN,
) -> dict[str, Any]:
    """Posts a signal to the internal intake endpoint.

    Args:
        client: Test client.
        body: Signal body.
        token: Value of the ``X-Joseph-Service-Token`` header.

    Returns:
        The response JSON object with the HTTP status code under ``status``.
    """
    response = client.post(
        "/internal/v1/signals",
        json=body,
        headers={"X-Joseph-Service-Token": token},
    )
    return {"status": response.status_code, **response.json()}


def test_signal_creates_sized_paper_order_once(
    ctx: tuple[harness.Harness, testclient.TestClient],
) -> None:
    """An entry signal is risk-sized with stop and target; replays add none."""
    h, client = ctx
    _deploy(h)
    first = _post(client, _signal(h))
    assert first["status"] == 202
    result = first["results"][0]
    assert result["decision"] == "ALLOW"
    orders = h.container.services.oms.list_orders(harness.TENANT_A)
    primary = next(o for o in orders if o.role is enums.OrderRole.PRIMARY)
    # 0.5% of 100000 equity risked over a 1000 stop distance = 0.5 BTC; the
    # 50000 max notional (0.526 BTC at 95000) does not bind.
    assert primary.quantity_base == D("0.5")
    assert primary.strategy_version_id == "discount-ote-reversal:v3"
    assert {o.role for o in orders} >= {
        enums.OrderRole.STOP_LOSS,
        enums.OrderRole.TAKE_PROFIT,
    }
    again = _post(client, _signal(h))
    assert again["results"][0]["replayed"] is True
    assert len(h.container.services.oms.list_orders(harness.TENANT_A)) == (
        len(orders)
    )


def test_signal_edge_cases(
    ctx: tuple[harness.Harness, testclient.TestClient],
) -> None:
    """Bad-token, undeployed, non-entry, stale, bad-stop signals are refused."""
    h, client = ctx
    assert _post(client, _signal(h), token="wrong")["status"] == 401
    assert _post(client, _signal(h))["results"] == []
    _deploy(h)
    watching = _post(client, _signal(h, kind="WATCHING_AOI", signal_id="w1"))
    assert watching["results"] == []
    stale = _post(
        client,
        _signal(h, signal_id="old", bar_close_ms=h.clock.now_ms() - 600_000),
    )
    assert stale["results"][0]["decision"] == "BLOCK"
    wrong_side = _post(client, _signal(h, signal_id="bad", stop_price="96000"))
    assert wrong_side["results"][0]["error"]["code"] == ("VALIDATION_FAILED")


def test_auto_live_signal_is_blocked_and_audited(
    ctx: tuple[harness.Harness, testclient.TestClient],
) -> None:
    """An AUTO_LIVE signal on a paper account is blocked, audited, not sent."""
    h, client = ctx
    _deploy(h, enums.DeploymentMode.AUTO_LIVE, (harness.BTC,))
    result = _post(client, _signal(h, signal_id="live-1"))
    assert result["results"][0]["decision"] == "BLOCK"
    assert all(not a.calls for a in h.container.registry.live.values())
    assert h.container.paper.submit_calls == 0
    with h.container.uow_factory() as uow:
        actions = [e.action for e in uow.audit.chain("tenant-a")]
    assert "risk.blocked" in actions


def test_disabled_deployment_and_other_tenant_receive_nothing(
    ctx: tuple[harness.Harness, testclient.TestClient],
) -> None:
    """Signals for a disabled deployment or another tenant yield no results."""
    h, client = ctx
    dep = _deploy(h)
    h.container.services.deployments.disable(
        harness.TENANT_A, dep.deployment_id
    )
    assert _post(client, _signal(h))["results"] == []
    assert (
        _post(client, _signal(h, tenant_id="tenant-b", signal_id="b"))[
            "results"
        ]
        == []
    )
