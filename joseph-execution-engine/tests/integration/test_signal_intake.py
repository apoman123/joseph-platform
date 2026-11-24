# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Integration tests for strategy signal intake (market -> execution)."""

from __future__ import annotations

import collections.abc
import decimal
import json
import pathlib

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
        h: Harness.
        **overrides: Field overrides.

    Returns:
        Signal body.
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
    """Creates a deployment for tenant A.

    Args:
        h: Harness.
        mode: Mode.
        instruments: Allow-list.

    Returns:
        Deployment.
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
    """Provides a harness and client.

    Yields:
        ``(harness, client)``.
    """
    h = harness.build()
    with testclient.TestClient(bootstrap.build_app(h.container)) as client:
        yield h, client


def _post(
    client: testclient.TestClient,
    body: dict[str, object],
    token: str = harness.SERVICE_TOKEN,
) -> dict[str, object]:
    """Posts a signal to the internal endpoint.

    Args:
        client: Client.
        body: Signal.
        token: Service token.

    Returns:
        Response JSON.
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
    """An entry signal sizes by risk, adds protection and never repeats."""
    h, client = ctx
    _deploy(h)
    first = _post(client, _signal(h))
    assert first["status"] == 202
    result = first["results"][0]  # type: ignore[index]
    assert result["decision"] == "ALLOW"
    orders = h.container.services.oms.list_orders(harness.TENANT_A)
    primary = next(o for o in orders if o.role is enums.OrderRole.PRIMARY)
    # 0.5% of 100000 equity risked over a 1000 stop distance = 0.5 BTC,
    # capped by the 50000 max notional at 95000 -> 0.526 -> 0.5.
    assert primary.quantity_base == D("0.5")
    assert primary.strategy_version_id == "discount-ote-reversal:v3"
    assert {o.role for o in orders} >= {
        enums.OrderRole.STOP_LOSS,
        enums.OrderRole.TAKE_PROFIT,
    }
    again = _post(client, _signal(h))
    assert again["results"][0]["replayed"] is True  # type: ignore[index]
    assert len(h.container.services.oms.list_orders(harness.TENANT_A)) == (
        len(orders)
    )


def test_signal_edge_cases(
    ctx: tuple[harness.Harness, testclient.TestClient],
) -> None:
    """Non-entry, undeployed, stale and unauthenticated signals are safe."""
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
    assert stale["results"][0]["decision"] == "BLOCK"  # type: ignore[index]
    wrong_side = _post(client, _signal(h, signal_id="bad", stop_price="96000"))
    assert wrong_side["results"][0]["error"]["code"] == (  # type: ignore[index]
        "VALIDATION_FAILED"
    )


def test_auto_live_signal_is_blocked_and_audited(
    ctx: tuple[harness.Harness, testclient.TestClient],
) -> None:
    """AUTO_LIVE without a LIVE-capable account never reaches an adapter."""
    h, client = ctx
    _deploy(h, enums.DeploymentMode.AUTO_LIVE, (harness.BTC,))
    result = _post(client, _signal(h, signal_id="live-1"))
    assert result["results"][0]["decision"] == "BLOCK"  # type: ignore[index]
    assert all(not a.calls for a in h.container.registry.live.values())
    assert h.container.paper.submit_calls == 0
    with h.container.uow_factory() as uow:
        actions = [e.action for e in uow.audit.chain("tenant-a")]
    assert "risk.blocked" in actions


def test_disabled_deployment_and_other_tenant_receive_nothing(
    ctx: tuple[harness.Harness, testclient.TestClient],
) -> None:
    """Disabled deployments and foreign tenants produce no orders."""
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
