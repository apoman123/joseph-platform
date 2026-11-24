# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Unit tests for positions, intents and related value objects."""

from __future__ import annotations

import dataclasses
import decimal

import pytest

from joseph_execution.domain import audit
from joseph_execution.domain import decimals
from joseph_execution.domain import deployments
from joseph_execution.domain import enums
from joseph_execution.domain import errors
from joseph_execution.domain import ids
from joseph_execution.domain import intents
from joseph_execution.domain import kill_switch
from joseph_execution.domain import positions
from joseph_execution.domain import risk

D = decimal.Decimal
BUY = enums.OrderSide.BUY
SELL = enums.OrderSide.SELL


def _position() -> positions.Position:
    """Builds an empty position.

    Returns:
        Position.
    """
    return positions.Position(
        position_id="p",
        tenant_id="t",
        account_id="a",
        instrument_id="mock:perp:BTCUSD",
        mode=enums.OrderMode.PAPER,
    )


def test_position_open_increase_and_average() -> None:
    """Increasing fills average the entry price."""
    pos = _position()
    pos.apply_fill(BUY, D(1), D(100), D(0), 1)
    pos.apply_fill(BUY, D(1), D(110), D(0), 2)
    assert pos.signed_qty == D(2)
    assert pos.entry_price == D(105)
    assert pos.side == "long"


def test_position_reduce_realizes_pnl_net_of_fee() -> None:
    """Reducing realizes PnL against the average entry minus fees."""
    pos = _position()
    pos.apply_fill(BUY, D(2), D(100), D(1), 1)
    realized = pos.apply_fill(SELL, D(1), D(110), D("0.5"), 2)
    assert realized == D("9.5")
    assert pos.signed_qty == D(1)
    assert pos.entry_price == D(100)
    assert pos.realized_pnl == D("8.5")


def test_position_flip_resets_entry() -> None:
    """Crossing through zero opens the remainder at the fill price."""
    pos = _position()
    pos.apply_fill(BUY, D(1), D(100), D(0), 1)
    pos.apply_fill(SELL, D(3), D(90), D(0), 2)
    assert pos.signed_qty == D(-2)
    assert pos.entry_price == D(90)
    assert pos.realized_pnl == D(-10)
    assert pos.side == "short"


def test_position_close_to_flat() -> None:
    """Closing fully clears the entry."""
    pos = _position()
    pos.apply_fill(SELL, D(1), D(100), D(0), 1)
    pos.apply_fill(BUY, D(1), D(80), D(0), 2)
    assert pos.side == "flat"
    assert pos.entry_price is None
    assert pos.realized_pnl == D(20)


def test_unrealized_and_margin() -> None:
    """Unrealized PnL and margin follow mark and leverage."""
    pos = _position()
    pos.apply_fill(BUY, D(2), D(100), D(0), 1, leverage=D(4))
    assert pos.unrealized_pnl(D(110)) == D(20)
    assert pos.unrealized_pnl(None) is None
    assert pos.margin() == D(50)


def _request(**overrides: object) -> intents.OrderIntentRequest:
    """Builds a request.

    Args:
        **overrides: Field overrides.

    Returns:
        Request.
    """
    fields: dict[str, object] = {
        "account_id": "a",
        "instrument_id": "mock:perp:BTCUSD",
        "side": BUY,
        "position_effect": enums.PositionEffect.OPEN,
        "order_type": enums.OrderType.LIMIT,
        "quantity_base": D(1),
        "time_in_force": enums.TimeInForce.GTC,
        "reduce_only": False,
        "mode": enums.OrderMode.PAPER,
        "source": enums.IntentSource.MANUAL_TICKET,
        "limit_price": D(100),
    }
    fields.update(overrides)
    return intents.OrderIntentRequest(**fields)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "overrides",
    [
        {"quantity_base": D(0)},
        {"limit_price": None},
        {"order_type": enums.OrderType.MARKET},
        {"order_type": enums.OrderType.STOP_MARKET, "limit_price": None},
        {"position_effect": enums.PositionEffect.CLOSE},
        {"source": enums.IntentSource.STRATEGY},
        {"leverage": D(0)},
        {
            "bracket": intents.Bracket(
                take_profit=(
                    intents.TakeProfitSpec(D(110), D("0.6")),
                    intents.TakeProfitSpec(D(120), D("0.6")),
                )
            )
        },
    ],
)
def test_request_validation(overrides: dict[str, object]) -> None:
    """Inconsistent requests are rejected with VALIDATION_FAILED."""
    with pytest.raises(errors.ExecutionError) as info:
        _request(**overrides)
    assert info.value.code == "VALIDATION_FAILED"


def test_request_round_trip_and_body_hash(contract_validate: object) -> None:
    """Contract round trip is lossless; body hash ignores preview tokens."""
    req = _request(
        bracket=intents.Bracket(
            stop_loss=intents.StopLossSpec(D(95)),
            take_profit=(intents.TakeProfitSpec(D(110)),),
        )
    )
    body = req.to_contract()
    contract_validate(
        "execution.schema.json",  # type: ignore[operator]
        "OrderIntentRequest",
        body,
    )
    again = intents.OrderIntentRequest.from_contract(body)
    assert again == req
    tokened = intents.OrderIntentRequest.from_contract(
        {**body, "preview_token": "abc"}
    )
    assert tokened.body_hash() == req.body_hash()
    assert _request(quantity_base=D(2)).body_hash() != req.body_hash()


def test_audit_chain_detects_tampering() -> None:
    """Changing, removing or reordering records breaks verification."""
    chain: list[audit.AuditEvent] = []
    prev = audit.GENESIS_HASH
    for n in range(3):
        digest = audit.chain_hash(
            prev, f"e{n}", "t", "u", "act", "x", "1", n, {"n": n}
        )
        chain.append(
            audit.AuditEvent(
                f"e{n}",
                "t",
                "u",
                "act",
                "x",
                "1",
                "tr",
                n,
                {"n": n},
                prev,
                digest,
            )
        )
        prev = digest
    assert audit.verify_chain(chain)
    tampered = list(chain)
    tampered[1] = dataclasses.replace(chain[1], payload={"n": 99})
    assert not audit.verify_chain(tampered)
    assert not audit.verify_chain([chain[0], chain[2]])
    assert not audit.verify_chain([chain[1], chain[0], chain[2]])


def test_kill_switch_scopes() -> None:
    """Switches apply by scope; released switches never apply."""

    def switch(
        scope: enums.KillSwitchScope, target: str | None, engaged: bool = True
    ) -> kill_switch.KillSwitch:
        """Builds a switch.

        Args:
            scope: Scope.
            target: Target.
            engaged: Engaged flag.

        Returns:
            Switch.
        """
        return kill_switch.KillSwitch(
            "t", scope, target, engaged, False, False, "r", "u", 1
        )

    assert switch(enums.KillSwitchScope.GLOBAL, None).applies_to("a", None)
    assert switch(enums.KillSwitchScope.ACCOUNT, "a").applies_to("a", None)
    assert not switch(enums.KillSwitchScope.ACCOUNT, "b").applies_to("a", None)
    strat = switch(enums.KillSwitchScope.STRATEGY, "my-strat")
    assert strat.applies_to("a", "my-strat:v3")
    assert not strat.applies_to("a", "other:v1")
    assert not strat.applies_to("a", None)
    assert not switch(
        enums.KillSwitchScope.GLOBAL, None, engaged=False
    ).applies_to("a", None)


def test_live_consent_binds_scope_and_expiry() -> None:
    """Consent is invalid after expiry or any scope change."""
    limits = deployments.DeploymentLimits(
        D(1000), 2, D(100), ("binance:perp:BTCUSDT",), D(2)
    )
    expires = 10_000
    consent = deployments.LiveConsent(
        1,
        expires,
        deployments.consent_scope_hash(
            "acct", "binance", "s:v1", limits, expires
        ),
        "user:u",
    )
    dep = deployments.Deployment(
        "d",
        "t",
        "s:v1",
        "acct",
        enums.DeploymentMode.AUTO_LIVE,
        enums.DeploymentState.ENABLED,
        limits,
        deployments.SizingModel("FIXED_QTY", fixed_qty=D(1)),
        1,
        1,
        consent,
    )
    assert dep.consent_valid("binance", 5_000)
    assert not dep.consent_valid("binance", expires)
    assert not dep.consent_valid("bitget", 5_000)
    bigger = deployments.DeploymentLimits(
        D(2000), 2, D(100), ("binance:perp:BTCUSDT",), D(2)
    )
    assert not dataclasses.replace(dep, limits=bigger).consent_valid(
        "binance", 5_000
    )
    assert not dataclasses.replace(
        dep, strategy_version_id="s:v2"
    ).consent_valid("binance", 5_000)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"model": "RISK_PCT"},
        {"model": "RISK_PCT", "risk_pct": D(9)},
        {"model": "FIXED_QTY"},
        {"model": "MARTINGALE"},
    ],
)
def test_sizing_validation(kwargs: dict[str, object]) -> None:
    """Sizing models validate their parameters."""
    with pytest.raises(errors.ExecutionError):
        deployments.SizingModel(**kwargs)  # type: ignore[arg-type]


def test_risk_decision_round_trip() -> None:
    """Decisions survive persistence and expose the primary code."""
    decision = risk.RiskDecision(
        enums.Decision.BLOCK,
        (
            risk.RiskCheckResult(
                "a", enums.RiskDomain.MODE, enums.CheckOutcome.PASS, "ok"
            ),
            risk.RiskCheckResult(
                "b",
                enums.RiskDomain.KILL_SWITCH,
                enums.CheckOutcome.FAIL,
                "no",
                "KILL_SWITCH_ENGAGED",
            ),
        ),
        "p",
        5,
    )
    again = risk.RiskDecision.from_contract(decision.to_contract())
    assert again == decision
    assert again.primary_code() == "KILL_SWITCH_ENGAGED"
    assert not again.allowed


def test_decimal_helpers() -> None:
    """Decimal helpers floor, round and format exactly."""
    assert decimals.floor_to_step(D("0.0199"), D("0.001")) == D("0.019")
    assert decimals.round_to_step(D("100.26"), D("0.5")) == D("100.5")
    assert decimals.fmt(D("1E+3")) == "1000"
    assert decimals.fmt(D("-0.00")) == "0"
    assert decimals.is_multiple(D("0.03"), D("0.01"))
    assert not decimals.is_multiple(D("0.035"), D("0.01"))
    assert decimals.to_decimal(0.1) == D("0.1")
    with pytest.raises(ValueError):
        decimals.to_decimal("nan")
    with pytest.raises(ValueError):
        decimals.to_decimal(True)


def test_ids() -> None:
    """UUIDv7 ids are ordered and client ids are venue-safe."""
    values = [ids.uuid7() for _ in range(100)]
    assert values == sorted(values)
    cid = ids.client_order_id(values[0])
    assert len(cid) <= 31 and cid.isalnum()
    assert ids.stable_id("p", 1) == ids.stable_id("p", 1)
