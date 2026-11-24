# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""System tests: server-side LIVE arming with the global gate open.

The ``live`` execution engine runs the stock entry point with
``JOSEPH_LIVE_TRADING_ENABLED=true``, a step-up TOTP secret and no venue or
broker credentials, so no account can become LIVE-capable and nothing is
ever sent to a venue. Arming and LIVE orders are therefore refused by the
account checks that follow the (open) gate: paper accounts never trade
LIVE, unverified or unavailable accounts must be verified first (the
``ibkr`` and ``sinopac`` brokers stay disabled, ``cathay`` fails closed),
and the gate code ``LIVE_TRADING_DISABLED`` never appears. ``LIVE_NOT_ARMED``
itself needs an AVAILABLE adapter (credentials and a reachable venue);
the execution engine's ``tests/integration/test_live_arming.py`` covers it
with a fake IB Gateway. The gate-closed behaviour is
``test_execution_system.py::test_live_mode_fails_closed``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import struct
import time
import uuid
from collections.abc import Callable
from typing import TypeAlias

import httpx
import pytest

from tests import conftest

PAPER = "acct-paper-tenant-a"
BROKER_ACCOUNTS = {
    "ibkr": "DU1234567",
    "sinopac": "9A95-0000000",
    "cathay": "8888-0000000",
}
"""Broker account numbers (the only field a broker connection takes)."""
WHY_UNAVAILABLE = {
    "ibkr": "JOSEPH_IBKR_ENABLED=false",
    "sinopac": "JOSEPH_SINOPAC_ENABLED=false",
    "cathay": "Awaiting the Cathay API SDK",
}
"""Capability note explaining why each broker cannot trade here."""

_Validate: TypeAlias = Callable[[str, str, object], None]


def _totp(secret_b32: str, at_ms: int) -> str:
    """Computes an RFC 6238 TOTP code (30 s steps, 6 digits, SHA-1).

    Args:
        secret_b32: Base32 shared secret.
        at_ms: Time in UTC epoch milliseconds.

    Returns:
        The six-digit code, zero-padded.
    """
    key = base64.b32decode(secret_b32 + "=" * (-len(secret_b32) % 8))
    counter = struct.pack(">Q", at_ms // 1000 // 30)
    digest = hmac.new(key, counter, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(value % 1_000_000).zfill(6)


def _code() -> str:
    """Returns a currently valid step-up code of the ``live`` engine.

    Returns:
        TOTP code.
    """
    return _totp(conftest.STEP_UP_SECRET, int(time.time() * 1000))


def _arm(
    client: httpx.Client, account_id: str, **overrides: object
) -> httpx.Response:
    """Requests account LIVE arming.

    The default body carries a valid step-up code, acknowledges the risk and
    asks for one hour.

    Args:
        client: Execution engine client.
        account_id: Account to arm.
        **overrides: Body fields that replace the defaults.

    Returns:
        Response of ``POST /v1/accounts/{account_id}/live-consent``.
    """
    body: dict[str, object] = {
        "step_up_code": _code(),
        "acknowledge_risk": True,
        "duration_ms": 3_600_000,
    }
    body.update(overrides)
    return client.post(f"/v1/accounts/{account_id}/live-consent", json=body)


def _refused(
    response: httpx.Response, validate: _Validate, status: int, code: str
) -> str:
    """Asserts a contract error and returns its message.

    Args:
        response: Response to check.
        validate: Contract validator.
        status: Expected HTTP status.
        code: Expected error code.

    Returns:
        Error message.
    """
    assert response.status_code == status, response.text
    body = response.json()
    validate("common.schema.json", "ErrorResponse", body)
    assert body["code"] == code, body
    return str(body["message"])


def _connect(
    client: httpx.Client, validate: _Validate, broker: str = "ibkr"
) -> dict[str, object]:
    """Connects a broker account by its account number (no secrets).

    Args:
        client: Execution engine client.
        validate: Contract validator.
        broker: Broker venue id, a key of ``BROKER_ACCOUNTS``.

    Returns:
        Contract ``AccountSummary`` (pending verification).
    """
    response = client.post(
        "/v1/accounts/connect/start",
        json={
            "venue": broker,
            "label": f"{broker} account",
            "credentials": {"account": BROKER_ACCOUNTS[broker]},
            "withdrawal_permission_disabled": True,
        },
    )
    assert response.status_code == 201, response.text
    summary = response.json()
    validate("execution.schema.json", "AccountSummary", summary)
    return summary


def _live_intent(account_id: str) -> dict[str, object]:
    """Builds a manual LIVE limit buy of 0.010 BTC at 50000.

    Args:
        account_id: Account to trade.

    Returns:
        Contract ``OrderIntentRequest``.
    """
    return {
        "account_id": account_id,
        "instrument_id": "mock:perp:BTCUSD",
        "side": "buy",
        "position_effect": "open",
        "order_type": "limit",
        "quantity_base": "0.010",
        "limit_price": "50000.0",
        "time_in_force": "GTC",
        "reduce_only": False,
        "leverage": "2",
        "mode": "LIVE",
        "source": "MANUAL_TICKET",
    }


def _mode_check(preview: dict[str, object]) -> dict[str, object]:
    """Returns the ``mode.allowed`` check of a risk verdict.

    Args:
        preview: Contract ``OrderPreview`` or ``SubmitIntentResponse``.

    Returns:
        Contract ``RiskCheckResult``.
    """
    risk = preview["risk"]
    assert isinstance(risk, dict)
    return next(c for c in risk["checks"] if c["check"] == "mode.allowed")


def test_arming_a_paper_account_is_refused(
    live: httpx.Client, validate: _Validate
) -> None:
    """Paper accounts never trade LIVE: 403 TRADE_MODE_DISABLED."""
    for account_id in (PAPER, "acct-twd-paper-tenant-a"):
        message = _refused(
            _arm(live, account_id), validate, 403, "TRADE_MODE_DISABLED"
        )
        assert "paper accounts never trade LIVE" in message
    summary = live.get(f"/v1/accounts/{PAPER}/summary").json()
    validate("execution.schema.json", "AccountSummary", summary)
    assert summary["live_armed_until_ms"] is None


@pytest.mark.parametrize("broker", sorted(BROKER_ACCOUNTS))
def test_arming_an_unverified_account_is_refused(
    live: httpx.Client, validate: _Validate, broker: str
) -> None:
    """A pending (then unavailable) broker account cannot be armed."""
    account = _connect(live, validate, broker)
    account_id = str(account["account_id"])
    assert account["status"] == "PENDING_VERIFICATION"
    assert account["modes"] == []
    message = _refused(
        _arm(live, account_id), validate, 403, "TRADE_MODE_DISABLED"
    )
    assert "verify the account first" in message
    _refused(
        _arm(live, account_id, step_up_code="000000"),
        validate,
        403,
        "STEP_UP_REQUIRED",
    )
    _refused(
        _arm(live, account_id, duration_ms=86_400_000),
        validate,
        422,
        "VALIDATION_FAILED",
    )
    # Verifying a gated broker reaches no gateway and grants no LIVE;
    # Cathay fails closed until its SDK exists.
    verified = live.post(f"/v1/accounts/{account_id}/verify").json()
    validate("execution.schema.json", "AccountSummary", verified)
    assert verified["status"] == "TRADING_UNAVAILABLE"
    assert verified["modes"] == []
    capabilities = verified["capabilities"]
    assert capabilities["status"] == "TRADING_UNAVAILABLE"
    assert any(WHY_UNAVAILABLE[broker] in n for n in capabilities["notes"])
    message = _refused(
        _arm(live, account_id), validate, 403, "TRADE_MODE_DISABLED"
    )
    assert "TRADING_UNAVAILABLE" in message
    disarmed = live.delete(f"/v1/accounts/{account_id}/live-consent")
    assert disarmed.status_code == 200, disarmed.text
    validate("execution.schema.json", "AccountSummary", disarmed.json())
    assert disarmed.json()["live_armed_until_ms"] is None
    events = live.get("/v1/audit/events", params={"limit": 500}).json()
    validate("execution.schema.json", "AuditEventList", events)
    assert events["chain_valid"] is True
    denied = [
        event
        for event in events["events"]
        if event["action"] == "live_arming.denied"
        and event["entity_id"] == account_id
    ]
    assert len(denied) == 4


@pytest.mark.parametrize("target", ["paper", "unverified"])
def test_unarmed_live_order_is_refused_past_the_open_gate(
    live: httpx.Client, validate: _Validate, target: str
) -> None:
    """With the gate open, an unarmed manual LIVE order is still blocked."""
    account_id = (
        PAPER
        if target == "paper"
        else str(_connect(live, validate)["account_id"])
    )
    body = _live_intent(account_id)
    response = live.post("/v1/orders/preview", json=body)
    assert response.status_code == 200, response.text
    preview = response.json()
    validate("execution.schema.json", "OrderPreview", preview)
    assert preview["allowed"] is False
    mode = _mode_check(preview)
    assert (mode["outcome"], mode["code"]) == ("FAIL", "TRADE_MODE_DISABLED")
    codes = {c.get("code") for c in preview["risk"]["checks"]}
    assert "LIVE_TRADING_DISABLED" not in codes
    message = _refused(
        live.post(
            "/v1/orders/intents",
            json=body,
            headers={"Idempotency-Key": str(uuid.uuid4())},
        ),
        validate,
        400,
        "PREVIEW_REQUIRED",
    )
    assert "previewed" in message
    submitted = live.post(
        "/v1/orders/intents",
        json={**body, "preview_token": preview["preview_token"]},
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert submitted.status_code in (200, 201), submitted.text
    result = submitted.json()
    validate("execution.schema.json", "SubmitIntentResponse", result)
    assert result["risk"]["decision"] in ("REJECT", "BLOCK")
    assert result["orders"] == []
    assert _mode_check(result)["code"] == "TRADE_MODE_DISABLED"
    orders = live.get("/v1/orders", params={"account_id": account_id}).json()
    assert all(o["mode"] != "LIVE" for o in orders["orders"])


def test_gate_closed_engine_refuses_arming(
    execution: httpx.Client, validate: _Validate
) -> None:
    """With the gate closed, paper and broker accounts cannot be armed."""
    _refused(_arm(execution, PAPER), validate, 403, "TRADE_MODE_DISABLED")
    account_id = str(_connect(execution, validate)["account_id"])
    # No step-up secret is configured there: every code fails closed.
    _refused(_arm(execution, account_id), validate, 403, "STEP_UP_REQUIRED")
