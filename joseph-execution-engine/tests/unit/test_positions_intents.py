# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Unit tests for positions, intents and related value objects."""

from __future__ import annotations

import dataclasses
import decimal
from collections.abc import Callable
from typing import TypeAlias

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

_Decimal = decimal.Decimal
BUY = enums.OrderSide.BUY
SELL = enums.OrderSide.SELL
_Validate: TypeAlias = Callable[[str, str, object], None]


def _position() -> positions.Position:
    """Builds an empty position.

    Returns:
        A flat paper position on the mock BTC perp.
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
    pos.apply_fill(BUY, _Decimal(1), _Decimal(100), _Decimal(0), 1)
    pos.apply_fill(BUY, _Decimal(1), _Decimal(110), _Decimal(0), 2)
    assert pos.signed_qty == _Decimal(2)
    assert pos.entry_price == _Decimal(105)
    assert pos.side == "long"


def test_position_reduce_realizes_pnl_net_of_fee() -> None:
    """Reducing realizes PnL against the average entry minus fees."""
    pos = _position()
    pos.apply_fill(BUY, _Decimal(2), _Decimal(100), _Decimal(1), 1)
    realized = pos.apply_fill(
        SELL, _Decimal(1), _Decimal(110), _Decimal("0.5"), 2
    )
    assert realized == _Decimal("9.5")
    assert pos.signed_qty == _Decimal(1)
    assert pos.entry_price == _Decimal(100)
    assert pos.realized_pnl == _Decimal("8.5")


def test_position_flip_resets_entry() -> None:
    """Crossing through zero opens the remainder at the fill price."""
    pos = _position()
    pos.apply_fill(BUY, _Decimal(1), _Decimal(100), _Decimal(0), 1)
    pos.apply_fill(SELL, _Decimal(3), _Decimal(90), _Decimal(0), 2)
    assert pos.signed_qty == _Decimal(-2)
    assert pos.entry_price == _Decimal(90)
    assert pos.realized_pnl == _Decimal(-10)
    assert pos.side == "short"


def test_position_close_to_flat() -> None:
    """Closing fully clears the entry."""
    pos = _position()
    pos.apply_fill(SELL, _Decimal(1), _Decimal(100), _Decimal(0), 1)
    pos.apply_fill(BUY, _Decimal(1), _Decimal(80), _Decimal(0), 2)
    assert pos.side == "flat"
    assert pos.entry_price is None
    assert pos.realized_pnl == _Decimal(20)


def test_unrealized_and_margin() -> None:
    """Unrealized PnL and margin follow mark and leverage."""
    pos = _position()
    pos.apply_fill(
        BUY, _Decimal(2), _Decimal(100), _Decimal(0), 1, leverage=_Decimal(4)
    )
    assert pos.unrealized_pnl(_Decimal(110)) == _Decimal(20)
    assert pos.unrealized_pnl(None) is None
    assert pos.margin() == _Decimal(50)


def _switch(
    scope: enums.KillSwitchScope, target: str | None, engaged: bool = True
) -> kill_switch.KillSwitch:
    """Builds a kill switch of tenant ``t`` that neither cancels nor flattens.

    Args:
        scope: Scope.
        target: Account id or strategy id; ``None`` for the GLOBAL scope.
        engaged: Whether the switch is engaged.

    Returns:
        The switch.
    """
    return kill_switch.KillSwitch(
        "t", scope, target, engaged, False, False, "r", "u", 1
    )


def _request(**overrides: object) -> intents.OrderIntentRequest:
    """Builds a paper intent request.

    Args:
        **overrides: Request fields that replace the defaults.

    Returns:
        By default a manual GTC limit buy of 1 BTC at 100.
    """
    fields: dict[str, object] = {
        "account_id": "a",
        "instrument_id": "mock:perp:BTCUSD",
        "side": BUY,
        "position_effect": enums.PositionEffect.OPEN,
        "order_type": enums.OrderType.LIMIT,
        "quantity_base": _Decimal(1),
        "time_in_force": enums.TimeInForce.GTC,
        "reduce_only": False,
        "mode": enums.OrderMode.PAPER,
        "source": enums.IntentSource.MANUAL_TICKET,
        "limit_price": _Decimal(100),
    }
    fields.update(overrides)
    return intents.OrderIntentRequest(**fields)  # type: ignore[arg-type]  # dict[str, object] holds each field's own type


@pytest.mark.parametrize(
    "overrides",
    [
        {"quantity_base": _Decimal(0)},
        {"limit_price": None},
        {"order_type": enums.OrderType.MARKET},
        {"order_type": enums.OrderType.STOP_MARKET, "limit_price": None},
        {"position_effect": enums.PositionEffect.CLOSE},
        {"source": enums.IntentSource.STRATEGY},
        {"leverage": _Decimal(0)},
        {
            "bracket": intents.Bracket(
                take_profit=(
                    intents.TakeProfitSpec(_Decimal(110), _Decimal("0.6")),
                    intents.TakeProfitSpec(_Decimal(120), _Decimal("0.6")),
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


def test_request_round_trip_and_body_hash(
    contract_validate: _Validate,
) -> None:
    """Contract round trip is lossless; body hash ignores preview tokens."""
    req = _request(
        bracket=intents.Bracket(
            stop_loss=intents.StopLossSpec(_Decimal(95)),
            take_profit=(intents.TakeProfitSpec(_Decimal(110)),),
        )
    )
    body = req.to_contract()
    contract_validate(
        "execution.schema.json",
        "OrderIntentRequest",
        body,
    )
    again = intents.OrderIntentRequest.from_contract(body)
    assert again == req
    tokened = intents.OrderIntentRequest.from_contract(
        {**body, "preview_token": "abc"}
    )
    assert tokened.body_hash() == req.body_hash()
    assert _request(quantity_base=_Decimal(2)).body_hash() != req.body_hash()


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
    assert _switch(enums.KillSwitchScope.GLOBAL, None).applies_to("a", None)
    assert _switch(enums.KillSwitchScope.ACCOUNT, "a").applies_to("a", None)
    assert not _switch(enums.KillSwitchScope.ACCOUNT, "b").applies_to("a", None)
    strategy_switch = _switch(enums.KillSwitchScope.STRATEGY, "my-strat")
    assert strategy_switch.applies_to("a", "my-strat:v3")
    assert not strategy_switch.applies_to("a", "other:v1")
    assert not strategy_switch.applies_to("a", None)
    assert not _switch(
        enums.KillSwitchScope.GLOBAL, None, engaged=False
    ).applies_to("a", None)


def test_live_consent_binds_scope_and_expiry() -> None:
    """Consent is invalid after expiry or any scope change."""
    limits = deployments.DeploymentLimits(
        _Decimal(1000), 2, _Decimal(100), ("binance:perp:BTCUSDT",), _Decimal(2)
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
    deployment = deployments.Deployment(
        "d",
        "t",
        "s:v1",
        "acct",
        enums.DeploymentMode.AUTO_LIVE,
        enums.DeploymentState.ENABLED,
        limits,
        deployments.SizingModel("FIXED_QTY", fixed_qty=_Decimal(1)),
        1,
        1,
        consent,
    )
    assert deployment.consent_valid("binance", 5_000)
    assert not deployment.consent_valid("binance", expires)
    assert not deployment.consent_valid("bitget", 5_000)
    bigger = deployments.DeploymentLimits(
        _Decimal(2000), 2, _Decimal(100), ("binance:perp:BTCUSDT",), _Decimal(2)
    )
    assert not dataclasses.replace(deployment, limits=bigger).consent_valid(
        "binance", 5_000
    )
    assert not dataclasses.replace(
        deployment, strategy_version_id="s:v2"
    ).consent_valid("binance", 5_000)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"model": "RISK_PCT"},
        {"model": "RISK_PCT", "risk_pct": _Decimal(9)},
        {"model": "FIXED_QTY"},
        {"model": "MARTINGALE"},
    ],
)
def test_sizing_validation(kwargs: dict[str, object]) -> None:
    """Sizing models refuse missing, out-of-range or unknown parameters."""
    with pytest.raises(errors.ExecutionError):
        deployments.SizingModel(**kwargs)  # type: ignore[arg-type]  # parametrized kwargs are typed object


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
    assert decimals.floor_to_step(
        _Decimal("0.0199"), _Decimal("0.001")
    ) == _Decimal("0.019")
    assert decimals.round_to_step(
        _Decimal("100.26"), _Decimal("0.5")
    ) == _Decimal("100.5")
    assert decimals.fmt(_Decimal("1E+3")) == "1000"
    assert decimals.fmt(_Decimal("-0.00")) == "0"
    assert decimals.is_multiple(_Decimal("0.03"), _Decimal("0.01"))
    assert not decimals.is_multiple(_Decimal("0.035"), _Decimal("0.01"))
    assert decimals.to_decimal(0.1) == _Decimal("0.1")
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
