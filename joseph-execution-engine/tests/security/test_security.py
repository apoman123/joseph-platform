# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Security tests: authentication, secrets, CORS and the LIVE gate."""

from __future__ import annotations

import dataclasses
import decimal
import logging
from typing import NoReturn

import pytest
from fastapi import testclient
from sqlalchemy import exc as sqlalchemy_exc

from joseph_execution import bootstrap
from joseph_execution.adapters import paper
from joseph_execution.audit import service as audit_service
from joseph_execution.domain import accounts
from joseph_execution.domain import deployments
from joseph_execution.domain import enums
from joseph_execution.domain import intents
from joseph_execution.testing import harness

_Decimal = decimal.Decimal


async def _boom(*args: object, **kwargs: object) -> NoReturn:
    """Raises an unexpected error whose message holds a fake secret.

    Args:
        *args: Ignored.
        **kwargs: Ignored.

    Raises:
        RuntimeError: Always.
    """
    raise RuntimeError("database password is hunter2")


def test_every_route_requires_authentication() -> None:
    """All routes but /health refuse anonymous calls; /metrics needs a token."""
    h = harness.build()
    app = bootstrap.build_app(h.container)
    with testclient.TestClient(app) as client:
        checked = 0
        for path, operations in app.openapi()["paths"].items():
            if path == "/health":
                continue
            concrete = path
            for name in (
                "order_id",
                "position_id",
                "account_id",
                "deployment_id",
            ):
                concrete = concrete.replace(f"{{{name}}}", "x")
            for method in operations:
                response = client.request(method.upper(), concrete, json={})
                assert response.status_code == 401, (method, path)
                assert response.json()["code"] == "UNAUTHENTICATED"
                checked += 1
        assert checked >= 15
        forged = client.get(
            "/v1/orders", headers={"Authorization": "Bearer forged"}
        )
        assert forged.status_code == 401
        assert client.get("/metrics").status_code == 401
        metrics = client.get(
            "/metrics",
            headers={"X-Joseph-Service-Token": harness.SERVICE_TOKEN},
        )
        assert metrics.status_code == 200
        assert "joseph_order_unknown_total" in metrics.text


def test_secrets_never_logged_or_returned(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Credential values never appear in logs, responses or audit."""
    h = harness.build()
    secret = "ultra-secret-signing-material-7781"
    with (
        caplog.at_level(logging.DEBUG),
        testclient.TestClient(bootstrap.build_app(h.container)) as client,
    ):
        responses = [
            client.post(
                "/v1/accounts/connect/start",
                json={
                    "venue": "bitget",
                    "label": "b",
                    "credentials": {
                        "api_key": "k1",
                        "api_secret": secret,
                        "passphrase": "pp-123456",
                    },
                    "withdrawal_permission_disabled": True,
                },
                headers=harness.headers(),
            )
        ]
        account_id = responses[0].json()["account_id"]
        responses.append(
            client.post(
                f"/v1/accounts/{account_id}/verify", headers=harness.headers()
            )
        )
        responses.append(
            client.get("/v1/audit/events", headers=harness.headers())
        )
        responses.append(client.get("/v1/accounts", headers=harness.headers()))
        logging.getLogger("joseph.leak").warning("api_secret=%s", secret)
    for response in responses:
        assert secret not in response.text
        assert "pp-123456" not in response.text
    assert secret not in caplog.text
    with h.container.uow_factory() as uow:
        envelope = uow.credentials.get(
            uow.accounts.get(account_id).credential_id  # type: ignore[union-attr, arg-type]  # connect stored the account and its credential
        )
    assert envelope is not None and secret not in str(envelope)


def test_errors_never_leak_internals() -> None:
    """Unexpected errors return a generic INTERNAL body."""
    h = harness.build()
    app = bootstrap.build_app(h.container)
    h.container.services.oms.preview = _boom  # type: ignore[method-assign]  # replaces the bound method to inject a failure
    with testclient.TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/v1/orders/preview",
            json=harness.intent_body(),
            headers=harness.headers(),
        )
    assert response.status_code == 500
    assert response.json()["code"] == "INTERNAL"
    assert "hunter2" not in response.text


def test_cors_allows_only_configured_origins() -> None:
    """CORS preflight succeeds only for configured origins."""
    h = harness.build()
    with testclient.TestClient(bootstrap.build_app(h.container)) as client:
        ok = client.options(
            "/v1/orders",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert ok.headers.get("access-control-allow-origin") == (
            "http://localhost:3000"
        )
        evil = client.options(
            "/v1/orders",
            headers={
                "Origin": "https://evil.example",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert "access-control-allow-origin" not in evil.headers


def test_forged_stream_messages_cannot_trade() -> None:
    """Arbitrary WebSocket messages never create orders."""
    h = harness.build()
    with (
        testclient.TestClient(bootstrap.build_app(h.container)) as client,
        client.websocket_connect(
            f"/v1/stream/user?token={harness.TOKEN_A}"
        ) as ws,
    ):
        for message in (
            {
                "action": "submit_order",
                "order": harness.intent_body(mode="LIVE"),
            },
            {
                "action": "subscribe",
                "channels": [{"channel": "orders"}],
                "type": "order.updated",
            },
            {"type": "risk.kill_switch", "payload": {"engaged": False}},
        ):
            ws.send_json(message)
        ws.send_json({"action": "ping"})
        kinds = {ws.receive_json()["type"] for _ in range(2)}
        assert kinds <= {"subscribed", "heartbeat"}
    assert h.container.services.oms.list_orders(harness.TENANT_A) == []
    assert h.container.paper.submit_calls == 0
    assert all(
        not harness.live_calls(a) for a in h.container.registry.live.values()
    )


async def test_live_gate_positive_control_reaches_only_when_all_pass() -> None:
    """With every gate satisfied the live adapter is reached; else never.

    This proves the LIVE block is enforced by the gates (flag, account
    mode, capability, consent) rather than hard-wired.
    """
    gated = harness.build(live_trading_enabled=True)
    fake = paper.PaperTradingAdapter(
        gated.market, gated.clock, name="fake-live"
    )
    gated.container.registry.live["mock"] = fake
    services = gated.container.services
    with gated.container.uow_factory() as uow:
        account = uow.accounts.get(harness.PAPER_A)
        assert account is not None
        uow.accounts.save(
            dataclasses.replace(
                account, modes=(*account.modes, enums.OrderMode.LIVE)
            )
        )
    gated.market.feed_state = enums.FeedState.LIVE
    body = harness.intent_body(mode="LIVE")
    request = intents.OrderIntentRequest.from_contract(body)
    unarmed = await services.oms.preview(harness.TENANT_A, request)
    assert unarmed.decision.primary_code() == "LIVE_NOT_ARMED"
    with gated.container.uow_factory() as uow:
        # The paper account cannot be armed through the API, so this
        # positive control stores the arming directly (like the forced
        # LIVE mode above).
        forced = uow.accounts.get(harness.PAPER_A)
        assert forced is not None
        uow.live_arming.put(
            accounts.LiveArming.grant(
                forced, "user:user-a", gated.clock.now_ms(), 60_000
            )
        )
    preview = await services.oms.preview(harness.TENANT_A, request)
    assert preview.allowed, preview.decision.failures()
    tokened = dataclasses.replace(
        preview.normalized, preview_token=preview.token
    )
    result = await services.oms.submit_intent(harness.TENANT_A, tokened, "live")
    assert result.decision.allowed
    assert fake.submit_calls == 1

    deployment = services.deployments.create(
        harness.TENANT_A,
        "s:v1",
        harness.PAPER_A,
        enums.DeploymentMode.AUTO_LIVE,
        deployments.DeploymentLimits(
            _Decimal(50000), 2, _Decimal(500), (harness.BTC,), _Decimal(3)
        ),
        deployments.SizingModel("FIXED_QTY", fixed_qty=_Decimal("0.01")),
    )
    granted = await services.deployments.grant_live_consent(
        harness.TENANT_A,
        deployment.deployment_id,
        gated.totp(),
        True,
        3_600_000,
    )
    assert granted.state is enums.DeploymentState.ENABLED
    assert granted.live_consent is not None

    closed = harness.build()
    closed.container.registry.live["mock"] = paper.PaperTradingAdapter(
        closed.market, closed.clock, name="should-not-be-called"
    )
    with closed.container.uow_factory() as uow:
        acct = uow.accounts.get(harness.PAPER_A)
        assert acct is not None
        uow.accounts.save(
            dataclasses.replace(acct, modes=(*acct.modes, enums.OrderMode.LIVE))
        )
    closed.market.feed_state = enums.FeedState.LIVE
    blocked_preview = await closed.container.services.oms.preview(
        harness.TENANT_A, request
    )
    assert not blocked_preview.allowed
    assert blocked_preview.decision.primary_code() == "LIVE_TRADING_DISABLED"
    live_adapter = closed.container.registry.live["mock"]
    assert isinstance(live_adapter, paper.PaperTradingAdapter)
    assert live_adapter.submit_calls == 0
    assert isinstance(accounts.paper_account_id("x"), str)


def test_audit_log_is_append_only_in_the_database() -> None:
    """UPDATE and DELETE on audit rows are refused by the database."""
    h = harness.build()
    with h.container.uow_factory() as uow:
        assert uow.audit.chain("tenant-a")
    for statement in (
        "UPDATE audit_events SET action = 'x'",
        "DELETE FROM audit_events",
    ):
        with (
            pytest.raises(sqlalchemy_exc.DatabaseError),
            h.container.uow_factory() as uow,
        ):
            uow.connection.exec_driver_sql(statement)
    with h.container.uow_factory() as uow:
        assert audit_service.AuditLog.verify(uow, "tenant-a")
