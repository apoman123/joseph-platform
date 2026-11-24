# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Account LIVE arming: API, persistence, audit and the Risk Gateway.

Manual LIVE orders need a server-side, expiring arming granted with the
same step-up verification as AUTO_LIVE deployment consent. These tests use
the fake IB Gateway (a broker that really places orders through the OMS)
and, over HTTP, a fake live adapter on a non-paper account.
"""

from __future__ import annotations

import collections.abc
import dataclasses
import datetime
import decimal
import pathlib
from typing import Any
from typing import TypeAlias

import pytest
import sqlalchemy as sa
from fastapi import testclient

from joseph_execution import bootstrap
from joseph_execution.accounts import service as account_service
from joseph_execution.adapters import paper
from joseph_execution.adapters import step_up
from joseph_execution.adapters import vault
from joseph_execution.adapters.brokers import ibkr
from joseph_execution.adapters.persistence import schema
from joseph_execution.adapters.persistence import sql
from joseph_execution.api import errors as api_errors
from joseph_execution.domain import accounts
from joseph_execution.domain import audit
from joseph_execution.domain import enums
from joseph_execution.domain import errors
from joseph_execution.domain import intents
from joseph_execution.oms import service as oms_service
from joseph_execution.platform import config
from joseph_execution.testing import equities as fixtures
from joseph_execution.testing import fake_ib
from joseph_execution.testing import harness

_Decimal = decimal.Decimal
_State = enums.OrderState
EXEC = "execution.schema.json"
MONDAY = datetime.date(2026, 11, 23)
HOUR_MS = 3_600_000
LIVE_ID = "acct-live-a"
_Validate: TypeAlias = collections.abc.Callable[[str, str, object], None]


@dataclasses.dataclass(slots=True)
class Broker:
    """A harness with a verified IBKR account on the fake IB Gateway.

    Attributes:
        h: Harness.
        ib: Fake IB Gateway.
        account_id: Verified IBKR account.
    """

    h: harness.Harness
    ib: fake_ib.FakeIB
    account_id: str


async def _ibkr(**overrides: object) -> Broker:
    """Connects and verifies an IBKR account (LIVE gate open).

    Args:
        **overrides: ``harness.build`` arguments that replace the defaults
            (LIVE trading enabled, IBKR enabled).

    Returns:
        The harness, with its clock at 10:00 New York on ``MONDAY``, the
        fake IB Gateway and the verified, unarmed account.
    """
    settings: dict[str, object] = {
        "live_trading_enabled": True,
        "securities": config.SecuritiesSettings(ibkr_enabled=True),
        **overrides,
    }
    h = harness.build(**settings)
    h.clock.current_ms = fixtures.new_york(MONDAY, 10)
    fixtures.install(h.market, MONDAY)
    h.market.feed_state = enums.FeedState.LIVE
    ib = fake_ib.FakeIB()
    ib.summary = [
        fake_ib.FakeAccountValue("DU1234567", "NetLiquidation", "50000", "USD"),
        fake_ib.FakeAccountValue("DU1234567", "AvailableFunds", "40000", "USD"),
    ]
    lookup = bootstrap.broker_account_lookup(
        h.container.uow_factory,
        vault.AesGcmVault(h.container.settings.vault_master_key),
    )
    adapter = ibkr.IbkrAdapter(
        ibkr.IbkrConfig(ack_timeout_s=0.05, commission_wait_s=0.01),
        lambda: fake_ib.FakeIbFactory(ib),
        h.market,
        h.clock,
        lookup,
    )
    h.container.brokers["ibkr"] = adapter
    h.container.registry.live["ibkr"] = adapter
    services = h.container.services
    connected = await services.accounts.connect_start(
        harness.TENANT_A, "ibkr", "IBKR", {"account": "DU1234567"}, True
    )
    account_id = str(connected["account_id"])
    verified = await services.accounts.verify(harness.TENANT_A, account_id)
    assert verified["modes"] == ["LIVE"]
    return Broker(h, ib, account_id)


def _aapl(account_id: str, **overrides: object) -> intents.OrderIntentRequest:
    """Builds a manual LIVE AAPL limit buy.

    Args:
        account_id: Account to trade.
        **overrides: Request fields that replace the defaults.

    Returns:
        A DAY limit buy of 10 AAPL at 190.01.
    """
    fields: dict[str, object] = {
        "account_id": account_id,
        "instrument_id": fixtures.AAPL,
        "side": enums.OrderSide.BUY,
        "position_effect": enums.PositionEffect.OPEN,
        "order_type": enums.OrderType.LIMIT,
        "limit_price": _Decimal("190.01"),
        "quantity_base": _Decimal(10),
        "time_in_force": enums.TimeInForce.DAY,
        "reduce_only": False,
        "mode": enums.OrderMode.LIVE,
        "source": enums.IntentSource.MANUAL_TICKET,
    }
    fields.update(overrides)
    return intents.OrderIntentRequest(**fields)  # type: ignore[arg-type]  # dict[str, object] holds each field's own type


async def _submit(
    h: harness.Harness, request: intents.OrderIntentRequest, key: str
) -> oms_service.SubmitResult:
    """Previews a request for tenant A and submits it with the preview token.

    Args:
        h: Harness.
        request: Intent request.
        key: Idempotency key.

    Returns:
        The OMS submit result.
    """
    oms = h.container.services.oms
    preview = await oms.preview(harness.TENANT_A, request)
    tokened = dataclasses.replace(
        preview.normalized, preview_token=preview.token
    )
    return await oms.submit_intent(harness.TENANT_A, tokened, key)


def _actions(h: harness.Harness) -> list[str]:
    """Lists tenant A's audit actions in order.

    Args:
        h: Harness.

    Returns:
        Audit action names in chain order.
    """
    with h.container.uow_factory() as uow:
        return [e.action for e in uow.audit.chain("tenant-a")]


async def test_manual_live_order_is_refused_until_armed() -> None:
    """Unarmed LIVE orders are blocked; arming lets them reach IBKR."""
    broker = await _ibkr()
    services = broker.h.container.services
    blocked = await _submit(broker.h, _aapl(broker.account_id), "unarmed")
    assert blocked.decision.primary_code() == "LIVE_NOT_ARMED"
    assert blocked.orders == ()
    assert broker.ib.placed == []
    armed = await services.accounts.grant_live_consent(
        harness.TENANT_A, broker.account_id, broker.h.totp(), True, HOUR_MS
    )
    assert armed["live_armed_until_ms"] == broker.h.clock.now_ms() + HOUR_MS
    listed = await services.accounts.list(harness.TENANT_A)
    assert {a["account_id"]: a["live_armed_until_ms"] for a in listed}[
        broker.account_id
    ] == armed["live_armed_until_ms"]
    sent = await _submit(broker.h, _aapl(broker.account_id), "armed")
    assert sent.decision.allowed, sent.decision.failures()
    (order,) = sent.orders
    assert order.state is _State.ACKNOWLEDGED
    assert broker.ib.placed[0].orderRef == order.client_order_id


async def test_arming_expires_and_defaults_to_the_configured_maximum() -> None:
    """Omitting the duration arms for 8 hours; afterwards LIVE is blocked."""
    broker = await _ibkr()
    services = broker.h.container.services
    granted_at = broker.h.clock.now_ms()
    armed = await services.accounts.grant_live_consent(
        harness.TENANT_A, broker.account_id, broker.h.totp(), True
    )
    assert armed["live_armed_until_ms"] == granted_at + 8 * HOUR_MS
    broker.h.clock.current_ms = granted_at + 8 * HOUR_MS - 1
    still = await services.oms.preview(
        harness.TENANT_A, _aapl(broker.account_id)
    )
    mode = next(c for c in still.decision.checks if c.check == "mode.allowed")
    assert mode.outcome is enums.CheckOutcome.PASS
    broker.h.clock.current_ms = granted_at + 8 * HOUR_MS
    summary = await services.accounts.summary(
        harness.TENANT_A, broker.account_id
    )
    assert summary["live_armed_until_ms"] is None
    expired = await services.oms.preview(
        harness.TENANT_A, _aapl(broker.account_id)
    )
    assert expired.decision.primary_code() == "LIVE_NOT_ARMED"
    failed = next(c for c in expired.decision.failures())
    assert failed.observed == "EXPIRED"


async def test_revoke_disarms_and_amend_needs_arming() -> None:
    """Disarming blocks new LIVE orders and amends; cancels still work."""
    broker = await _ibkr()
    services = broker.h.container.services
    await services.accounts.grant_live_consent(
        harness.TENANT_A, broker.account_id, broker.h.totp(), True, HOUR_MS
    )
    sent = await _submit(broker.h, _aapl(broker.account_id), "resting")
    (order,) = sent.orders
    disarmed = await services.accounts.revoke_live_consent(
        harness.TENANT_A, broker.account_id
    )
    assert disarmed["live_armed_until_ms"] is None
    again = await services.accounts.revoke_live_consent(
        harness.TENANT_A, broker.account_id
    )
    assert again["live_armed_until_ms"] is None
    with pytest.raises(errors.ExecutionError) as info:
        await services.oms.amend_order(
            harness.TENANT_A,
            order.order_id,
            _Decimal("190.02"),
            None,
            None,
            None,
        )
    assert info.value.code == "LIVE_NOT_ARMED"
    assert api_errors.STATUS_BY_CODE["LIVE_NOT_ARMED"] == 403
    canceled = await services.oms.cancel_order(harness.TENANT_A, order.order_id)
    assert canceled.state in (_State.CANCEL_PENDING, _State.CANCELED)
    blocked = await services.oms.preview(
        harness.TENANT_A, _aapl(broker.account_id)
    )
    revoked = next(c for c in blocked.decision.failures())
    assert revoked.code == "LIVE_NOT_ARMED" and revoked.observed == "REVOKED"
    with broker.h.container.uow_factory() as uow:
        events = [
            e
            for e in uow.audit.chain("tenant-a")
            if e.action.startswith("live_arming.")
        ]
    assert [e.action for e in events] == [
        "live_arming.granted",
        "live_arming.revoked",
        "live_arming.revoked",
    ]
    granted = events[0].payload
    assert granted["granted_by"] == "user:user-a"
    assert granted["acknowledgement"] == accounts.LIVE_ARM_ACKNOWLEDGEMENT
    assert granted["duration_ms"] == HOUR_MS
    assert granted["expires_at_ms"] == broker.h.clock.now_ms() + HOUR_MS
    assert events[1].payload == {
        "reason": "user",
        "was_armed": True,
        "expires_at_ms": granted["expires_at_ms"],
    }
    assert events[2].payload["was_armed"] is False


async def test_kill_switch_flatten_and_reconciliation_work_disarmed() -> None:
    """A disarmed account can still reduce, flatten and reconcile."""
    broker = await _ibkr()
    services = broker.h.container.services
    await services.accounts.grant_live_consent(
        harness.TENANT_A, broker.account_id, broker.h.totp(), True, HOUR_MS
    )
    sent = await _submit(broker.h, _aapl(broker.account_id), "open")
    broker.ib.execute(broker.ib.trades()[0], 10, 190.01, commission=1.0)
    await bootstrap.pump_brokers(broker.h.container)
    (order,) = sent.orders
    assert (
        services.oms.get_order(harness.TENANT_A, order.order_id).state
        is _State.FILLED
    )
    await services.accounts.revoke_live_consent(
        harness.TENANT_A, broker.account_id
    )
    position = (
        await services.oms.list_positions(harness.TENANT_A, broker.account_id)
    )[0]
    close = await services.oms.close_request(
        harness.TENANT_A,
        str(position["position_id"]),
        _Decimal("0.5"),
        enums.OrderType.LIMIT,
        _Decimal("190.00"),
    )
    reduced = await _submit(broker.h, close, "reduce-disarmed")
    assert reduced.decision.allowed, reduced.decision.failures()
    warned = next(
        c for c in reduced.decision.checks if c.check == "mode.allowed"
    )
    assert warned.outcome is enums.CheckOutcome.WARN
    assert len(broker.ib.placed) == 2
    await services.kill_switches.set(
        harness.TENANT_A,
        enums.KillSwitchScope.ACCOUNT,
        broker.account_id,
        True,
        True,
        True,
        "drill while disarmed",
    )
    flattened = [o for o in broker.ib.placed[2:] if o.action == "SELL"]
    assert flattened, [o.action for o in broker.ib.placed]
    broker.ib.position_rows = [
        fake_ib.FakePosition(
            "DU1234567", broker.ib.trades()[0].contract, 10, 190.01
        )
    ]
    report = await services.reconciler.run_once()
    assert report.mismatches == 0


async def test_refusals_are_specific_and_audited() -> None:
    """Each arming refusal has its own code and message and is audited."""
    broker = await _ibkr(live_arm_max_ms=2 * HOUR_MS)
    services = broker.h.container.services
    pending = await services.accounts.connect_start(
        harness.TENANT_A, "ibkr", "Second", {"account": "DU7654321"}, True
    )
    cases: list[tuple[str, str, bool, int | None, str, str]] = [
        (
            harness.PAPER_A,
            broker.h.totp(),
            True,
            None,
            "TRADE_MODE_DISABLED",
            "paper",
        ),
        (
            broker.account_id,
            "000000",
            True,
            None,
            "STEP_UP_REQUIRED",
            "step-up",
        ),
        (
            broker.account_id,
            "abcdef",
            True,
            None,
            "STEP_UP_REQUIRED",
            "step-up",
        ),
        (
            broker.account_id,
            broker.h.totp(),
            False,
            None,
            "VALIDATION_FAILED",
            "ack",
        ),
        (
            broker.account_id,
            broker.h.totp(),
            True,
            2 * HOUR_MS + 1,
            "VALIDATION_FAILED",
            "at most 2 hours",
        ),
        (
            broker.account_id,
            broker.h.totp(),
            True,
            0,
            "VALIDATION_FAILED",
            "at most",
        ),
        (
            str(pending["account_id"]),
            broker.h.totp(),
            True,
            None,
            "TRADE_MODE_DISABLED",
            "verify the account first",
        ),
    ]
    for account_id, code, ack, duration, expected, fragment in cases:
        with pytest.raises(errors.ExecutionError) as info:
            await services.accounts.grant_live_consent(
                harness.TENANT_A, account_id, code, ack, duration
            )
        assert info.value.code == expected, (account_id, code, duration)
        assert fragment in info.value.message
    with pytest.raises(errors.ExecutionError) as foreign:
        await services.accounts.grant_live_consent(
            harness.TENANT_B, broker.account_id, broker.h.totp(), True
        )
    assert foreign.value.code == "NOT_FOUND"
    assert _actions(broker.h).count("live_arming.denied") == len(cases)
    with broker.h.container.uow_factory() as uow:
        assert uow.live_arming.get("tenant-a", broker.account_id) is None
        denied = [
            e.payload["code"]
            for e in uow.audit.chain("tenant-a")
            if e.action == "live_arming.denied"
        ]
    assert denied == [c[4] for c in cases]
    gate_off = await _ibkr(live_trading_enabled=False)
    with pytest.raises(errors.ExecutionError) as closed:
        await gate_off.h.container.services.accounts.grant_live_consent(
            harness.TENANT_A, gate_off.account_id, gate_off.h.totp(), True
        )
    assert closed.value.code == "LIVE_TRADING_DISABLED"


async def test_step_up_is_verified_exactly_like_deployment_consent() -> None:
    """One shared TOTP verifier: same window, fail closed without secret."""
    broker = await _ibkr()
    services = broker.h.container.services
    assert services.accounts._step_up is services.deployments._step_up
    now = broker.h.clock.now_ms()
    previous_step = step_up.totp(harness.TOTP_SECRET, now - 30_000)
    stale = step_up.totp(harness.TOTP_SECRET, now - 90_000)
    with pytest.raises(errors.ExecutionError) as info:
        await services.accounts.grant_live_consent(
            harness.TENANT_A, broker.account_id, stale, True
        )
    assert info.value.code == "STEP_UP_REQUIRED"
    armed = await services.accounts.grant_live_consent(
        harness.TENANT_A, broker.account_id, previous_step, True
    )
    assert armed["live_armed_until_ms"] is not None
    no_secret = await _ibkr(step_up_totp_secret=None)
    with pytest.raises(errors.ExecutionError) as closed:
        await no_secret.h.container.services.accounts.grant_live_consent(
            harness.TENANT_A, no_secret.account_id, no_secret.h.totp(), True
        )
    assert closed.value.code == "STEP_UP_REQUIRED"
    consent = services.deployments
    assert consent._step_up.verify("user-a", stale) is False
    assert consent._step_up.verify("user-a", previous_step)


def _arming_events(h: harness.Harness) -> list[audit.AuditEvent]:
    """Lists tenant A's LIVE arming audit events.

    Args:
        h: Harness.

    Returns:
        Audit events in chain order.
    """
    with h.container.uow_factory() as uow:
        return [
            e
            for e in uow.audit.chain("tenant-a")
            if e.action.startswith("live_arming.")
        ]


async def test_losing_live_or_credentials_disarms() -> None:
    """Re-verification without LIVE and credential revocation disarm."""
    broker = await _ibkr()
    services = broker.h.container.services
    await services.accounts.grant_live_consent(
        harness.TENANT_A, broker.account_id, broker.h.totp(), True
    )
    broker.ib.managed = []
    with pytest.raises(errors.ExecutionError) as down:
        await services.accounts.grant_live_consent(
            harness.TENANT_A, broker.account_id, broker.h.totp(), True
        )
    assert down.value.code == "LIVE_TRADING_DISABLED"
    assert "ibkr trading is TRADING_UNAVAILABLE" in down.value.message
    reverified = await services.accounts.verify(
        harness.TENANT_A, broker.account_id
    )
    assert (
        reverified["modes"] == [] and reverified["live_armed_until_ms"] is None
    )
    broker.ib.managed = ["DU1234567"]
    back = await services.accounts.verify(harness.TENANT_A, broker.account_id)
    assert back["modes"] == ["LIVE"] and back["live_armed_until_ms"] is None
    await services.accounts.grant_live_consent(
        harness.TENANT_A, broker.account_id, broker.h.totp(), True
    )
    gone = await services.accounts.revoke(harness.TENANT_A, broker.account_id)
    assert gone["status"] == "DISCONNECTED"
    assert gone["live_armed_until_ms"] is None
    revoked = [
        (e.actor, e.payload["reason"])
        for e in _arming_events(broker.h)
        if e.action == "live_arming.revoked"
    ]
    assert revoked == [
        ("system", "verification"),
        ("user:user-a", "credentials_revoked"),
    ]
    with pytest.raises(errors.ExecutionError) as info:
        await services.accounts.grant_live_consent(
            harness.TENANT_A, broker.account_id, broker.h.totp(), True
        )
    assert info.value.code == "TRADE_MODE_DISABLED"


# ---------------------------------------------------------------------------
# HTTP: a non-paper account on a fake live adapter (no broker needed).
# ---------------------------------------------------------------------------


@pytest.fixture
def live_api() -> collections.abc.Iterator[
    tuple[harness.Harness, testclient.TestClient]
]:
    """Provides an engine with the LIVE gate open, a LIVE account, a client.

    The account ``LIVE_ID`` trades through a paper adapter registered as the
    live ``mock`` venue.

    Yields:
        ``(harness, client)``; the app's lifespan runs until teardown.
    """
    h = harness.build(live_trading_enabled=True)
    h.market.feed_state = enums.FeedState.LIVE
    h.container.registry.live["mock"] = paper.PaperTradingAdapter(
        h.market, h.clock, name="fake-live"
    )
    with h.container.uow_factory() as uow:
        uow.accounts.add(
            accounts.Account(
                account_id=LIVE_ID,
                tenant_id="tenant-a",
                venue="mock",
                label="Live (fake adapter)",
                status=enums.AccountStatus.CONNECTED,
                modes=(enums.OrderMode.LIVE,),
                currency="USD",
                created_at_ms=h.clock.now_ms(),
                credential_id="cred-live-a",
                withdrawal_disabled=True,
            )
        )
    with testclient.TestClient(bootstrap.build_app(h.container)) as client:
        yield h, client


def _live_body(**overrides: object) -> dict[str, object]:
    """Builds a manual LIVE intent for the fake live account.

    Args:
        **overrides: Body fields passed on to ``harness.intent_body``.

    Returns:
        Contract ``OrderIntentRequest``.
    """
    return harness.intent_body(account_id=LIVE_ID, mode="LIVE", **overrides)


def _place(client: testclient.TestClient, key: str) -> dict[str, Any]:
    """Previews and submits a manual LIVE order over HTTP.

    Asserts that the submission answers 201.

    Args:
        client: Test client.
        key: Idempotency key.

    Returns:
        ``SubmitIntentResponse`` body.
    """
    preview = client.post(
        "/v1/orders/preview", json=_live_body(), headers=harness.headers()
    ).json()
    response = client.post(
        "/v1/orders/intents",
        json={
            **preview["normalized"],
            "preview_token": preview["preview_token"],
        },
        headers=harness.headers(idempotency_key=key),
    )
    assert response.status_code == 201, response.json()
    body: dict[str, Any] = response.json()
    return body


def test_live_consent_endpoints_arm_disarm_and_audit(
    live_api: tuple[harness.Harness, testclient.TestClient],
    contract_validate: _Validate,
) -> None:
    """POST arms (summary carries the expiry), DELETE disarms; audited."""
    h, client = live_api
    hdr = harness.headers()
    path = f"/v1/accounts/{LIVE_ID}/live-consent"
    blocked = _place(client, "http-unarmed")
    contract_validate(EXEC, "SubmitIntentResponse", blocked)
    assert blocked["risk"]["decision"] == "BLOCK"
    codes = [c["code"] for c in blocked["risk"]["checks"]]
    assert "LIVE_NOT_ARMED" in codes and blocked["orders"] == []
    request = {
        "step_up_code": h.totp(),
        "acknowledge_risk": True,
        "duration_ms": 2 * HOUR_MS,
    }
    contract_validate(EXEC, "AccountLiveConsentRequest", request)
    armed = client.post(path, json=request, headers=hdr)
    assert armed.status_code == 200, armed.json()
    contract_validate(EXEC, "AccountSummary", armed.json())
    until = h.clock.now_ms() + 2 * HOUR_MS
    assert armed.json()["live_armed_until_ms"] == until
    summary = client.get(f"/v1/accounts/{LIVE_ID}/summary", headers=hdr)
    assert summary.json()["live_armed_until_ms"] == until
    listed = client.get("/v1/accounts", headers=hdr).json()["accounts"]
    contract_validate(EXEC, "AccountList", {"accounts": listed})
    by_id = {a["account_id"]: a["live_armed_until_ms"] for a in listed}
    assert by_id == {LIVE_ID: until, harness.PAPER_A: None}
    placed = _place(client, "http-armed")
    assert placed["risk"]["decision"] == "ALLOW"
    assert len(placed["orders"]) == 1
    disarmed = client.delete(path, headers=hdr)
    assert disarmed.status_code == 200
    contract_validate(EXEC, "AccountSummary", disarmed.json())
    assert disarmed.json()["live_armed_until_ms"] is None
    audit_body = client.get(
        "/v1/audit/events", params={"account_id": LIVE_ID}, headers=hdr
    ).json()
    contract_validate(EXEC, "AuditEventList", audit_body)
    assert audit_body["chain_valid"] is True
    actions = [e["action"] for e in audit_body["events"]]
    assert "live_arming.granted" in actions
    assert "live_arming.revoked" in actions
    granted = next(
        e for e in audit_body["events"] if e["action"] == "live_arming.granted"
    )
    assert granted["actor"] == "user:user-a"
    assert granted["payload"]["expires_at_ms"] == until
    assert "step_up_code" not in str(granted["payload"])


def test_live_consent_http_errors_are_contract_shaped(
    live_api: tuple[harness.Harness, testclient.TestClient],
    contract_validate: _Validate,
) -> None:
    """Refusals map to 403/404/422 with contract error bodies."""
    h, client = live_api
    hdr = harness.headers()
    good = {"step_up_code": h.totp(), "acknowledge_risk": True}
    cases = [
        (LIVE_ID, {**good, "step_up_code": "000000"}, 403, "STEP_UP_REQUIRED"),
        (harness.PAPER_A, good, 403, "TRADE_MODE_DISABLED"),
        (
            LIVE_ID,
            {**good, "acknowledge_risk": False},
            422,
            "VALIDATION_FAILED",
        ),
        (
            LIVE_ID,
            {**good, "duration_ms": 9 * HOUR_MS},
            422,
            "VALIDATION_FAILED",
        ),
        (LIVE_ID, {**good, "step_up_code": "1"}, 422, "VALIDATION_FAILED"),
        (LIVE_ID, {**good, "scope": "all"}, 422, "VALIDATION_FAILED"),
        ("acct-missing", good, 404, "NOT_FOUND"),
    ]
    for account_id, body, status, code in cases:
        response = client.post(
            f"/v1/accounts/{account_id}/live-consent", json=body, headers=hdr
        )
        assert response.status_code == status, (body, response.json())
        contract_validate(
            "common.schema.json", "ErrorResponse", response.json()
        )
        assert response.json()["code"] == code
    foreign = client.post(
        f"/v1/accounts/{LIVE_ID}/live-consent",
        json=good,
        headers=harness.headers(harness.TOKEN_B),
    )
    assert foreign.status_code == 404
    assert (
        client.delete(
            f"/v1/accounts/{LIVE_ID}/live-consent",
            headers=harness.headers(harness.TOKEN_B),
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/v1/accounts/{LIVE_ID}/live-consent", json=good
        ).status_code
        == 401
    )
    paper_refusal = client.post(
        f"/v1/accounts/{harness.PAPER_A}/live-consent", json=good, headers=hdr
    ).json()
    assert "paper accounts never trade LIVE" in paper_refusal["message"]
    with h.container.uow_factory() as uow:
        assert uow.live_arming.get("tenant-a", LIVE_ID) is None


def test_live_arming_persists_and_needs_no_migration(
    tmp_path: pathlib.Path,
) -> None:
    """The arming survives a restart; ``create_all`` adds the new table."""
    url = f"sqlite:///{tmp_path}/execution.db"
    engine = sql.create_engine(url)
    with engine.begin() as conn:
        schema.account_live_arming.drop(conn)
    engine.dispose()
    engine = sql.create_engine(url)
    assert "account_live_arming" in sa.inspect(engine).get_table_names()
    account = accounts.Account(
        "acct-x",
        "tenant-a",
        "binance",
        "x",
        enums.AccountStatus.CONNECTED,
        (enums.OrderMode.LIVE,),
        "USD",
        1,
        credential_id="cred-x",
    )
    arming = accounts.LiveArming.grant(account, "user:user-a", 10, HOUR_MS)
    with sql.SqlUnitOfWorkFactory(engine)() as uow:
        uow.accounts.add(account)
        uow.live_arming.put(arming)
    engine.dispose()
    reopened = sql.SqlUnitOfWorkFactory(sql.create_engine(url))
    with reopened() as uow:
        assert uow.live_arming.get("tenant-a", "acct-x") == arming
        assert uow.live_arming.get("tenant-b", "acct-x") is None
        uow.live_arming.put(arming.revoke("user:user-a", 20))
    with reopened() as uow:
        stored = uow.live_arming.get("tenant-a", "acct-x")
    assert stored is not None and stored.revoked_at_ms == 20
    assert not stored.valid_for(account, 30)


def test_live_arm_max_setting_is_bounded() -> None:
    """``JOSEPH_LIVE_ARM_MAX_MS`` defaults to 8 h and never exceeds 24 h."""
    assert config.from_env({}).live_arm_max_ms == 8 * HOUR_MS
    custom = config.from_env({"JOSEPH_LIVE_ARM_MAX_MS": str(HOUR_MS)})
    assert custom.live_arm_max_ms == HOUR_MS
    ceiling = config.from_env({"JOSEPH_LIVE_ARM_MAX_MS": str(24 * HOUR_MS)})
    assert ceiling.live_arm_max_ms == accounts.LIVE_ARM_CEILING_MS
    for bad in ("0", "-5", str(24 * HOUR_MS + 1)):
        with pytest.raises(ValueError, match="JOSEPH_LIVE_ARM_MAX_MS"):
            config.from_env({"JOSEPH_LIVE_ARM_MAX_MS": bad})


@pytest.mark.parametrize(
    ("duration_ms", "text"),
    [
        (8 * HOUR_MS, "8 hours"),
        (HOUR_MS, "1 hour"),
        (90 * 60_000, "90 minutes"),
        (1_500, "1500 ms"),
    ],
)
def test_duration_limits_are_described_plainly(
    duration_ms: int, text: str
) -> None:
    """The maximum-duration refusal names the limit in plain units."""
    assert account_service._duration_text(duration_ms) == text
