"""System tests for the execution engine running as a real process.

Covers the manual paper flow, idempotency, kill switch, tenant isolation,
audit integrity and the fail-closed LIVE gate.
"""

from __future__ import annotations

import decimal
import time
import uuid
from collections.abc import Callable

import httpx

MOCK_BTC = "mock:perp:BTCUSD"
PAPER = "acct-paper-tenant-a"


def _intent(**overrides: object) -> dict[str, object]:
    """Builds a paper order intent request.

    Args:
        **overrides: Fields to replace.

    Returns:
        Contract ``OrderIntentRequest``.
    """
    body: dict[str, object] = {
        "account_id": PAPER,
        "instrument_id": MOCK_BTC,
        "side": "buy",
        "position_effect": "open",
        "order_type": "market",
        "quantity_base": "0.010",
        "limit_price": None,
        "trigger_price": None,
        "trigger_basis": None,
        "time_in_force": "IOC",
        "reduce_only": False,
        "margin_mode": "isolated",
        "leverage": "2",
        "bracket": None,
        "mode": "PAPER",
        "source": "MANUAL_TICKET",
        "strategy_version_id": None,
        "signal_id": None,
        "expires_at_ms": None,
        "preview_token": None,
    }
    body.update(overrides)
    return body


def _submit(
    client: httpx.Client, body: dict[str, object], key: str | None = None
) -> httpx.Response:
    """Previews then submits an intent with an idempotency key.

    Args:
        client: Authenticated client.
        body: Intent request.
        key: Idempotency key; random when omitted.

    Returns:
        Submit response.
    """
    preview = client.post("/v1/orders/preview", json=body)
    assert preview.status_code == 200, preview.text
    body = {**body, "preview_token": preview.json()["preview_token"]}
    return client.post(
        "/v1/orders/intents",
        json=body,
        headers={"Idempotency-Key": key or str(uuid.uuid4())},
    )


def _resting_price(client: httpx.Client, side: str) -> str:
    """Returns a non-marketable limit price 15% away from the mark.

    The default account band rejects limits more than 10% from the mark, so
    test orders sit inside that band.

    Args:
        client: Authenticated client.
        side: ``buy`` (below mark) or ``sell`` (above mark).

    Returns:
        Tick-aligned price string (BTC tick 0.1).
    """
    preview = client.post("/v1/orders/preview", json=_intent()).json()
    mark = decimal.Decimal(preview["mark_price"])
    factor = (
        decimal.Decimal("0.93") if side == "buy" else decimal.Decimal("1.07")
    )
    return str((mark * factor).quantize(decimal.Decimal("0.1")))


def _wait_for(predicate: Callable[[], bool], timeout_s: float = 15) -> bool:
    """Polls ``predicate`` until it is true.

    Args:
        predicate: Condition.
        timeout_s: Maximum wait.

    Returns:
        Whether the condition became true.
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.25)
    return False


def test_paper_account_is_provisioned(
    execution: httpx.Client, validate: Callable[[str, str, object], None]
) -> None:
    """Each tenant gets a paper account without LIVE mode."""
    accounts = execution.get("/v1/accounts").json()
    validate("execution.schema.json", "AccountList", accounts)
    paper = [a for a in accounts["accounts"] if a["account_id"] == PAPER]
    assert paper and "LIVE" not in paper[0]["modes"]
    summary = execution.get(f"/v1/accounts/{PAPER}/summary").json()
    validate("execution.schema.json", "AccountSummary", summary)


def test_preview_reports_every_risk_check(
    execution: httpx.Client, validate: Callable[[str, str, object], None]
) -> None:
    """Preview returns normalized values and a full risk verdict."""
    preview = execution.post(
        "/v1/orders/preview", json=_intent(quantity_base="0.0104")
    ).json()
    validate("execution.schema.json", "OrderPreview", preview)
    assert preview["normalized"]["quantity_base"] == "0.01"
    domains = {c["domain"] for c in preview["risk"]["checks"]}
    assert {"MODE", "SIZE", "KILL_SWITCH", "DATA_QUALITY"} <= domains


def test_paper_market_order_fills_and_close_flattens(
    execution: httpx.Client, validate: Callable[[str, str, object], None]
) -> None:
    """Market buy fills into a position; reduce-only close flattens it."""
    response = _submit(execution, _intent())
    assert response.status_code in (200, 201), response.text
    body = response.json()
    validate("execution.schema.json", "SubmitIntentResponse", body)
    assert body["risk"]["decision"] == "ALLOW"
    order_id = body["orders"][0]["order_id"]

    def filled() -> bool:
        """Checks whether the order filled."""
        orders = execution.get(
            "/v1/orders", params={"account_id": PAPER}
        ).json()
        return any(
            o["order_id"] == order_id and o["state"] == "FILLED"
            for o in orders["orders"]
        )

    assert _wait_for(filled)
    positions = execution.get(
        "/v1/positions", params={"account_id": PAPER}
    ).json()
    validate("execution.schema.json", "PositionList", positions)
    position = next(
        p
        for p in positions["positions"]
        if p["instrument_id"] == MOCK_BTC and p["side"] != "flat"
    )
    close_req = {
        "fraction": "1",
        "order_type": "market",
        "limit_price": None,
        "preview_token": None,
    }
    preview = execution.post(
        f"/v1/positions/{position['position_id']}/close-preview",
        json=close_req,
    ).json()
    assert preview["normalized"]["reduce_only"] is True
    close_req["preview_token"] = preview["preview_token"]
    closed = execution.post(
        f"/v1/positions/{position['position_id']}/close",
        json=close_req,
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert closed.status_code in (200, 201), closed.text

    def flat() -> bool:
        """Checks whether the position is flat."""
        rows = execution.get(
            "/v1/positions", params={"account_id": PAPER}
        ).json()
        return all(
            p["side"] == "flat" or p["quantity_base"] in ("0", "0.0")
            for p in rows["positions"]
            if p["instrument_id"] == MOCK_BTC
        )

    assert _wait_for(flat)
    fills = execution.get("/v1/fills", params={"account_id": PAPER}).json()
    validate("execution.schema.json", "FillList", fills)
    assert len(fills["fills"]) >= 2


def test_idempotency_key_replays_and_conflicts(execution: httpx.Client) -> None:
    """Same key + body replays; same key + different body conflicts."""
    key = str(uuid.uuid4())
    body = _intent(
        order_type="limit",
        limit_price=_resting_price(execution, "buy"),
        time_in_force="GTC",
    )
    first = _submit(execution, body, key).json()
    preview = execution.post("/v1/orders/preview", json=body).json()
    again = execution.post(
        "/v1/orders/intents",
        json={**body, "preview_token": preview["preview_token"]},
        headers={"Idempotency-Key": key},
    )
    assert again.json()["replayed"] is True
    assert again.json()["intent"]["intent_id"] == first["intent"]["intent_id"]
    conflict = execution.post(
        "/v1/orders/intents",
        json={
            **body,
            "quantity_base": "0.02",
            "preview_token": preview["preview_token"],
        },
        headers={"Idempotency-Key": key},
    )
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "IDEMPOTENCY_CONFLICT"
    order_id = first["orders"][0]["order_id"]
    cancel = execution.post(f"/v1/orders/{order_id}/cancel", json={})
    assert cancel.json()["state"] in ("CANCEL_PENDING", "CANCELED")


def test_far_limit_is_rejected_by_price_band(execution: httpx.Client) -> None:
    """A limit 50% away from the mark is rejected, not silently placed."""
    far = str(
        (
            decimal.Decimal(_resting_price(execution, "buy"))
            / decimal.Decimal("0.93")
            * decimal.Decimal("0.5")
        ).quantize(decimal.Decimal("0.1"))
    )
    response = _submit(
        execution,
        _intent(order_type="limit", limit_price=far, time_in_force="GTC"),
    )
    body = response.json()
    assert body["orders"] == []
    assert body["risk"]["decision"] == "REJECT"
    assert any(
        c["check"] == "size.price_band" and c["outcome"] == "FAIL"
        for c in body["risk"]["checks"]
    )


def test_kill_switch_blocks_opening_orders(execution: httpx.Client) -> None:
    """An engaged global kill switch blocks new exposure."""
    engage = {
        "scope": "GLOBAL",
        "target_id": None,
        "engage": True,
        "cancel_open_orders": False,
        "flatten_positions": False,
        "reason": "system test drill",
    }
    assert (
        execution.post("/v1/risk/kill-switch", json=engage).status_code == 200
    )
    try:
        preview = execution.post("/v1/orders/preview", json=_intent()).json()
        assert preview["allowed"] is False
        assert any(
            c["domain"] == "KILL_SWITCH" and c["outcome"] == "FAIL"
            for c in preview["risk"]["checks"]
        )
    finally:
        execution.post(
            "/v1/risk/kill-switch",
            json={**engage, "engage": False, "reason": "drill complete"},
        )


def test_live_mode_fails_closed(execution: httpx.Client) -> None:
    """LIVE intents are blocked while live trading is disabled."""
    preview = execution.post(
        "/v1/orders/preview", json=_intent(mode="LIVE")
    ).json()
    assert preview["allowed"] is False
    codes = {c.get("code") for c in preview["risk"]["checks"]}
    assert codes & {"LIVE_TRADING_DISABLED", "TRADE_MODE_DISABLED"}


def test_tenant_isolation(
    execution: httpx.Client, execution_b: httpx.Client
) -> None:
    """Tenant B can neither see nor cancel tenant A's orders."""
    body = _intent(
        order_type="limit",
        limit_price=_resting_price(execution, "buy"),
        time_in_force="GTC",
    )
    order_id = _submit(execution, body).json()["orders"][0]["order_id"]
    orders_b = execution_b.get("/v1/orders").json()["orders"]
    assert all(o["order_id"] != order_id for o in orders_b)
    assert (
        execution_b.post(f"/v1/orders/{order_id}/cancel", json={}).status_code
        == 404
    )
    assert execution_b.get(f"/v1/accounts/{PAPER}/summary").status_code == 404
    execution.post(f"/v1/orders/{order_id}/cancel", json={})


def test_audit_chain_is_valid(
    execution: httpx.Client, validate: Callable[[str, str, object], None]
) -> None:
    """Audit events validate and the hash chain verifies."""
    body = execution.get("/v1/audit/events", params={"limit": 500}).json()
    validate("execution.schema.json", "AuditEventList", body)
    assert body["chain_valid"] is True
    assert len(body["events"]) > 0
