# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""HTTP and WebSocket integration tests with contract validation."""

from __future__ import annotations

import collections.abc
from typing import Any
from typing import TypeAlias

import pytest
from fastapi import testclient

from joseph_execution import bootstrap
from joseph_execution.testing import harness

_Validate: TypeAlias = collections.abc.Callable[[str, str, object], None]
EXEC = "execution.schema.json"


@pytest.fixture
def ctx() -> collections.abc.Iterator[
    tuple[harness.Harness, testclient.TestClient]
]:
    """Provides a harness and a running test client.

    Yields:
        ``(harness, client)``; the app's lifespan runs until teardown.
    """
    h = harness.build()
    with testclient.TestClient(bootstrap.build_app(h.container)) as client:
        yield h, client


def test_preview_submit_and_read_models(
    ctx: tuple[harness.Harness, testclient.TestClient],
    contract_validate: _Validate,
) -> None:
    """Preview, submit and every read model conform to the contracts."""
    _, client = ctx
    hdr = harness.headers()
    preview = client.post(
        "/v1/orders/preview", json=harness.intent_body(), headers=hdr
    )
    assert preview.status_code == 200
    contract_validate(EXEC, "OrderPreview", preview.json())
    submitted = client.post(
        "/v1/orders/intents",
        headers=harness.headers(idempotency_key="k1"),
        json={
            **preview.json()["normalized"],
            "preview_token": preview.json()["preview_token"],
        },
    )
    assert submitted.status_code == 201, submitted.json()
    contract_validate(EXEC, "SubmitIntentResponse", submitted.json())
    assert submitted.json()["orders"][0]["state"] == "FILLED"
    assert submitted.headers["X-Trace-Id"]
    for path, definition in (
        ("/v1/orders", "OrderList"),
        ("/v1/positions", "PositionList"),
        ("/v1/fills", "FillList"),
        ("/v1/accounts", "AccountList"),
        ("/v1/risk/kill-switch", "KillSwitchList"),
        ("/v1/audit/events", "AuditEventList"),
        ("/v1/deployments", "DeploymentList"),
        (f"/v1/accounts/{harness.PAPER_A}/summary", "AccountSummary"),
        (f"/v1/accounts/{harness.PAPER_A}/capabilities", "TradingCapabilities"),
    ):
        response = client.get(path, headers=hdr)
        assert response.status_code == 200, path
        contract_validate(EXEC, definition, response.json())
    audit = client.get("/v1/audit/events", headers=hdr).json()
    assert audit["chain_valid"] is True


def test_idempotency_replay_and_conflict(
    ctx: tuple[harness.Harness, testclient.TestClient],
    contract_validate: _Validate,
) -> None:
    """Replays return the original intent, changes conflict, no key is 422."""
    _, client = ctx
    first = client.post(
        "/v1/orders/intents",
        json=harness.intent_body(),
        headers=harness.headers(idempotency_key="same"),
    )
    again = client.post(
        "/v1/orders/intents",
        json=harness.intent_body(),
        headers=harness.headers(idempotency_key="same"),
    )
    assert again.status_code == 200 and again.json()["replayed"]
    assert (
        again.json()["intent"]["intent_id"]
        == (first.json()["intent"]["intent_id"])
    )
    conflict = client.post(
        "/v1/orders/intents",
        json=harness.intent_body(quantity_base="0.020"),
        headers=harness.headers(idempotency_key="same"),
    )
    assert conflict.status_code == 409
    contract_validate("common.schema.json", "ErrorResponse", conflict.json())
    assert conflict.json()["code"] == "IDEMPOTENCY_CONFLICT"
    orders = client.get("/v1/orders", headers=harness.headers()).json()
    assert len(orders["orders"]) == 1
    missing = client.post(
        "/v1/orders/intents",
        json=harness.intent_body(),
        headers=harness.headers(),
    )
    assert missing.status_code == 422


def test_validation_errors_are_contract_shaped(
    ctx: tuple[harness.Harness, testclient.TestClient],
    contract_validate: _Validate,
) -> None:
    """Bad bodies yield VALIDATION_FAILED with field errors."""
    _, client = ctx
    for body in (
        harness.intent_body(quantity_base=0.01),
        harness.intent_body(source="STRATEGY"),
        harness.intent_body(source="EMERGENCY"),
        harness.intent_body(instrument_id="BTCUSDT"),
        {**harness.intent_body(), "eval": "1"},
    ):
        response = client.post(
            "/v1/orders/preview", json=body, headers=harness.headers()
        )
        assert response.status_code == 422
        payload = response.json()
        contract_validate("common.schema.json", "ErrorResponse", payload)
        assert payload["code"] == "VALIDATION_FAILED"
        assert payload["field_errors"]


def test_live_orders_fail_closed_over_http(
    ctx: tuple[harness.Harness, testclient.TestClient],
) -> None:
    """LIVE needs a preview and is then blocked by the Risk Gateway."""
    h, client = ctx
    body = harness.intent_body(mode="LIVE")
    unpreviewed = client.post(
        "/v1/orders/intents",
        json=body,
        headers=harness.headers(idempotency_key="l1"),
    )
    assert unpreviewed.json()["code"] == "PREVIEW_REQUIRED"
    preview = client.post(
        "/v1/orders/preview", json=body, headers=harness.headers()
    ).json()
    assert preview["allowed"] is False
    submitted = client.post(
        "/v1/orders/intents",
        json={
            **preview["normalized"],
            "preview_token": preview["preview_token"],
        },
        headers=harness.headers(idempotency_key="l2"),
    ).json()
    assert submitted["risk"]["decision"] == "BLOCK"
    assert submitted["orders"] == []
    assert all(
        not harness.live_calls(adapter)
        for adapter in h.container.registry.live.values()
    )


def test_tenant_isolation_over_http(
    ctx: tuple[harness.Harness, testclient.TestClient],
) -> None:
    """Tenant B cannot read or mutate tenant A's resources."""
    _, client = ctx
    created = client.post(
        "/v1/orders/intents",
        json=harness.intent_body(
            order_type="limit", limit_price="94000", time_in_force="GTC"
        ),
        headers=harness.headers(idempotency_key="a1"),
    ).json()
    order_id = created["orders"][0]["order_id"]
    headers_b = harness.headers(harness.TOKEN_B)
    assert client.get("/v1/orders", headers=headers_b).json()["orders"] == []
    assert (
        client.post(
            f"/v1/orders/{order_id}/cancel", headers=headers_b
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/v1/orders/{order_id}/amend", json={}, headers=headers_b
        ).status_code
        == 404
    )
    summary = client.get(
        f"/v1/accounts/{harness.PAPER_A}/summary", headers=headers_b
    )
    assert summary.status_code == 404
    steal = client.post(
        "/v1/orders/intents",
        json=harness.intent_body(),
        headers=harness.headers(harness.TOKEN_B, "b1"),
    )
    assert steal.status_code == 404
    accounts = client.get("/v1/accounts", headers=headers_b).json()["accounts"]
    assert [a["account_id"] for a in accounts] == [harness.PAPER_B]


def test_cancel_amend_close_endpoints(
    ctx: tuple[harness.Harness, testclient.TestClient],
    contract_validate: _Validate,
) -> None:
    """The cancel, amend and close endpoints return contract objects."""
    _, client = ctx
    hdr = harness.headers()
    resting = client.post(
        "/v1/orders/intents",
        json=harness.intent_body(
            order_type="limit", limit_price="94000", time_in_force="GTC"
        ),
        headers=harness.headers(idempotency_key="r"),
    ).json()["orders"][0]
    amended = client.post(
        f"/v1/orders/{resting['order_id']}/amend",
        json={"limit_price": "93900", "expected_version": resting["version"]},
        headers=hdr,
    )
    assert amended.status_code == 200, amended.json()
    contract_validate(EXEC, "Order", amended.json())
    conflict = client.post(
        f"/v1/orders/{resting['order_id']}/amend",
        json={"limit_price": "93800", "expected_version": 0},
        headers=hdr,
    )
    assert conflict.status_code == 409
    canceled = client.post(
        f"/v1/orders/{resting['order_id']}/cancel", headers=hdr
    )
    assert canceled.json()["state"] == "CANCELED"
    client.post(
        "/v1/orders/intents",
        json=harness.intent_body(),
        headers=harness.headers(idempotency_key="open"),
    )
    pos = client.get("/v1/positions", headers=hdr).json()["positions"][0]
    preview = client.post(
        f"/v1/positions/{pos['position_id']}/close-preview",
        json={"fraction": "1", "order_type": "market"},
        headers=hdr,
    )
    contract_validate(EXEC, "OrderPreview", preview.json())
    closed = client.post(
        f"/v1/positions/{pos['position_id']}/close",
        json={
            "fraction": "1",
            "order_type": "market",
            "preview_token": preview.json()["preview_token"],
        },
        headers=harness.headers(idempotency_key="close"),
    )
    assert closed.status_code == 201, closed.json()
    assert closed.json()["orders"][0]["reduce_only"] is True
    assert client.get("/v1/positions", headers=hdr).json()["positions"] == []


def test_kill_switch_limits_and_deployments(
    ctx: tuple[harness.Harness, testclient.TestClient],
    contract_validate: _Validate,
) -> None:
    """The kill switch blocks, limits clamp and AUTO_LIVE consent is refused."""
    h, client = ctx
    hdr = harness.headers()
    switch = client.post(
        "/v1/risk/kill-switch",
        json={
            "scope": "GLOBAL",
            "engage": True,
            "cancel_open_orders": False,
            "flatten_positions": False,
            "reason": "drill",
        },
        headers=hdr,
    )
    contract_validate(EXEC, "KillSwitchState", switch.json())
    blocked = client.post(
        "/v1/orders/intents",
        json=harness.intent_body(),
        headers=harness.headers(idempotency_key="ks"),
    )
    assert blocked.json()["risk"]["decision"] == "BLOCK"
    client.post(
        "/v1/risk/kill-switch",
        json={
            "scope": "GLOBAL",
            "engage": False,
            "cancel_open_orders": False,
            "flatten_positions": False,
            "reason": "done",
        },
        headers=hdr,
    )
    limits = client.put(
        "/v1/risk/limits",
        json={"account_id": harness.PAPER_A, "limits": {"max_leverage": "999"}},
        headers=hdr,
    ).json()
    assert limits["limits"]["max_leverage"] == "50"
    bad = client.put(
        "/v1/risk/limits",
        json={"account_id": harness.PAPER_A, "limits": {"nope": "1"}},
        headers=hdr,
    )
    assert bad.status_code == 422
    dep_body: dict[str, Any] = {
        "strategy_version_id": "ema-trend-long:v3",
        "account_id": harness.PAPER_A,
        "mode": "PAPER",
        "limits": {
            "max_notional": "5000",
            "max_open_positions": 2,
            "max_daily_loss": "200",
            "allowed_instruments": [],
            "max_leverage": "3",
        },
        "sizing": {"model": "RISK_PCT", "risk_pct": "0.5"},
    }
    created = client.post("/v1/deployments", json=dep_body, headers=hdr)
    assert created.status_code == 201
    contract_validate(EXEC, "Deployment", created.json())
    assert created.json()["state"] == "ENABLED"
    live = client.post(
        "/v1/deployments", json={**dep_body, "mode": "AUTO_LIVE"}, headers=hdr
    ).json()
    assert live["state"] == "PENDING_CONSENT"
    no_step_up = client.post(
        f"/v1/deployments/{live['deployment_id']}/live-consent",
        json={"step_up_code": "000000", "acknowledge_risk": True},
        headers=hdr,
    )
    assert no_step_up.json()["code"] in (
        "STEP_UP_REQUIRED",
        "VALIDATION_FAILED",
    )
    with_instruments = client.post(
        "/v1/deployments",
        json={
            **dep_body,
            "mode": "AUTO_LIVE",
            "limits": {
                **dep_body["limits"],
                "allowed_instruments": [harness.BTC],
            },
        },
        headers=hdr,
    ).json()
    consent = client.post(
        f"/v1/deployments/{with_instruments['deployment_id']}/live-consent",
        json={"step_up_code": h.totp(), "acknowledge_risk": True},
        headers=hdr,
    )
    assert consent.status_code == 403
    assert consent.json()["code"] == "LIVE_TRADING_DISABLED"
    disabled = client.post(
        f"/v1/deployments/{created.json()['deployment_id']}/disable",
        headers=hdr,
    )
    assert disabled.json()["state"] == "DISABLED"
    audit = client.get("/v1/audit/events", headers=hdr).json()
    actions = {e["action"] for e in audit["events"]}
    assert {
        "live_consent.denied",
        "deployment.created",
        "deployment.disabled",
        "kill_switch.engaged",
    } <= actions


def test_account_connection_never_echoes_secrets(
    ctx: tuple[harness.Harness, testclient.TestClient],
    contract_validate: _Validate,
) -> None:
    """Credentials are sealed, never echoed and can be revoked."""
    _, client = ctx
    hdr = harness.headers()
    secret = "VERY-SECRET-binance-value-42"
    connect = client.post(
        "/v1/accounts/connect/start",
        json={
            "venue": "binance",
            "label": "Main",
            "credentials": {"api_key": "public-key-1", "api_secret": secret},
            "withdrawal_permission_disabled": True,
        },
        headers=hdr,
    )
    assert connect.status_code == 201, connect.json()
    contract_validate(EXEC, "AccountSummary", connect.json())
    assert secret not in connect.text
    account_id = connect.json()["account_id"]
    verified = client.post(
        f"/v1/accounts/{account_id}/verify", headers=hdr
    ).json()
    assert verified["status"] == "TRADING_UNAVAILABLE"
    assert verified["modes"] == []
    assert verified["capabilities"]["status"] == "TRADING_UNAVAILABLE"
    order = client.post(
        "/v1/orders/intents",
        json=harness.intent_body(
            account_id=account_id, instrument_id="binance:perp:BTCUSDT"
        ),
        headers=harness.headers(idempotency_key="v1"),
    ).json()
    assert order["risk"]["decision"] == "BLOCK"
    revoked = client.post(
        f"/v1/accounts/{account_id}/revoke", headers=hdr
    ).json()
    assert revoked["status"] == "DISCONNECTED"
    everything = client.get("/v1/audit/events", headers=hdr).text
    assert secret not in everything
    for bad in (
        {"credentials": {"seed_phrase": "a b c"}},
        {"withdrawal_permission_disabled": False},
        {"venue": "hyperliquid", "credentials": {"api_key": "x"}},
    ):
        body = {
            "venue": "binance",
            "label": "x",
            "credentials": {"api_key": "k", "api_secret": "s"},
            "withdrawal_permission_disabled": True,
            **bad,
        }
        response = client.post(
            "/v1/accounts/connect/start", json=body, headers=hdr
        )
        assert response.status_code == 422, bad


def test_user_stream_auth_isolation_and_replay(
    ctx: tuple[harness.Harness, testclient.TestClient],
    contract_validate: _Validate,
) -> None:
    """The user stream is authenticated, tenant scoped and resumable."""
    _, client = ctx
    with client.websocket_connect("/v1/stream/user?token=nope") as ws:
        error = ws.receive_json()
        assert error["type"] == "error"
        assert error["payload"]["code"] == "UNAUTHENTICATED"
    with (
        client.websocket_connect(
            f"/v1/stream/user?token={harness.TOKEN_A}"
        ) as ws_a,
        client.websocket_connect(
            f"/v1/stream/user?token={harness.TOKEN_B}"
        ) as ws_b,
    ):
        ws_a.send_json(
            {
                "action": "subscribe",
                "channels": [{"channel": "orders"}, {"channel": "fills"}],
            }
        )
        assert ws_a.receive_json()["type"] == "subscribed"
        ws_b.send_json(
            {"action": "subscribe", "channels": [{"channel": "orders"}]}
        )
        assert ws_b.receive_json()["type"] == "subscribed"
        client.post(
            "/v1/orders/intents",
            json=harness.intent_body(),
            headers=harness.headers(idempotency_key="ws"),
        )
        seen: list[dict[str, Any]] = []
        while len(seen) < 3:
            envelope = ws_a.receive_json()
            if envelope["type"] == "heartbeat":
                continue
            contract_validate("stream.schema.json", "StreamEnvelope", envelope)
            seen.append(envelope)
        assert {e["type"] for e in seen} <= {"order.updated", "fill.created"}
        assert all(
            e["payload"].get("tenant_id", "tenant-a") == "tenant-a"
            for e in seen
        )
        b_event = ws_b.receive_json()
        assert b_event["type"] == "heartbeat"
        last = seen[0]["sequence"]
        ws_a.send_json(
            {
                "action": "subscribe",
                "channels": [{"channel": "orders"}],
                "last_sequence": last,
            }
        )
        assert ws_a.receive_json()["type"] == "subscribed"
        replayed = ws_a.receive_json()
        assert replayed["sequence"] > last
        ws_a.send_json(
            {"action": "subscribe", "channels": [], "last_sequence": -5}
        )
        assert ws_a.receive_json()["type"] == "subscribed"
        window = ws_a.receive_json()
        assert window["type"] == "snapshot_required"
        assert window["payload"]["reason"] == "replay window exceeded"
        # A sequence from before a server restart is ahead of the new one.
        ws_a.send_json(
            {
                "action": "subscribe",
                "channels": [{"channel": "orders"}],
                "last_sequence": seen[-1]["sequence"] + 1_000,
            }
        )
        assert ws_a.receive_json()["type"] == "subscribed"
        restarted = ws_a.receive_json()
        contract_validate("stream.schema.json", "StreamEnvelope", restarted)
        assert restarted["type"] == "snapshot_required"
        assert restarted["payload"] == {
            "reason": "server restarted",
            "channels": ["orders"],
        }
