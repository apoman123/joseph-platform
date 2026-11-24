# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Unit tests for every Risk Gateway check and the aggregation policy."""

from __future__ import annotations

import dataclasses
import decimal

import pytest

from joseph_execution.domain import accounts
from joseph_execution.domain import deployments
from joseph_execution.domain import enums
from joseph_execution.domain import identity
from joseph_execution.domain import intents
from joseph_execution.domain import kill_switch
from joseph_execution.domain import market
from joseph_execution.domain import positions
from joseph_execution.risk import checks
from joseph_execution.risk import context
from joseph_execution.risk import gateway
from joseph_execution.risk import limits

D = decimal.Decimal
OUT = enums.CheckOutcome
NOW = 1_000_000
BTC = "mock:perp:BTCUSD"

PAPER = accounts.Account(
    "acct",
    "t",
    "mock",
    "Paper",
    enums.AccountStatus.CONNECTED,
    (enums.OrderMode.PAPER, enums.OrderMode.SHADOW),
    "USD",
    1,
    D(100000),
    True,
)
CAPS = accounts.TradingCapabilities(
    "mock",
    enums.TradingStatus.AVAILABLE,
    tuple(enums.OrderType),
    tuple(enums.TimeInForce),
    True,
    True,
    True,
    False,
    True,
    False,
    ("ONE_WAY",),
    (enums.MarginMode.ISOLATED,),
    True,
    True,
)
INSTRUMENT = market.InstrumentMeta(
    BTC,
    "mock",
    D("0.1"),
    D("0.001"),
    D("0.001"),
    enums.InstrumentStatus.TRADING,
    min_notional=D(5),
    max_leverage=D(50),
)
QUOTE = market.MarketQuote(
    BTC,
    enums.FeedState.LIVE,
    NOW,
    D(95000),
    D(95000),
    D("94999.9"),
    D("95000.1"),
)
BOOK = market.BookView(
    BTC, ((D("94999.9"), D(10)),), ((D("95000.1"), D(10)),), NOW
)


def _request(**overrides: object) -> intents.OrderIntentRequest:
    """Builds a market buy request.

    Args:
        **overrides: Field overrides.

    Returns:
        Request.
    """
    fields: dict[str, object] = {
        "account_id": "acct",
        "instrument_id": BTC,
        "side": enums.OrderSide.BUY,
        "position_effect": enums.PositionEffect.OPEN,
        "order_type": enums.OrderType.MARKET,
        "quantity_base": D("0.01"),
        "time_in_force": enums.TimeInForce.IOC,
        "reduce_only": False,
        "mode": enums.OrderMode.PAPER,
        "source": enums.IntentSource.MANUAL_TICKET,
    }
    fields.update(overrides)
    return intents.OrderIntentRequest(**fields)  # type: ignore[arg-type]


def _ctx(**overrides: object) -> context.RiskContext:
    """Builds a context in which every check passes.

    Args:
        **overrides: Field overrides.

    Returns:
        Context.
    """
    fields: dict[str, object] = {
        "request": _request(),
        "principal": identity.Principal("t", "u"),
        "now_ms": NOW,
        "limits": limits.RiskLimits(),
        "live_trading_enabled": False,
        "emergency_halt": False,
        "account": PAPER,
        "capabilities": CAPS,
        "adapter_health": enums.HealthState.HEALTHY,
        "instrument": INSTRUMENT,
        "quote": QUOTE,
        "book": BOOK,
        "position": None,
        "open_position_count": 0,
        "gross_notional": D(0),
        "equity": D(100000),
        "available_margin": D(100000),
        "daily_realized_pnl": D(0),
        "account_state_unknown": False,
        "kill_switches": (),
        "deployment": None,
        "signal_age_ms": None,
        "signal_intents": 0,
        "strategy_open_orders": 0,
        "recent_rejects": 0,
        "notional": D(950),
        "margin_required": D(950),
        "slippage_pct": D("0.0001"),
    }
    fields.update(overrides)
    return context.RiskContext(**fields)  # type: ignore[arg-type]


def _long(qty: str = "0.02") -> positions.Position:
    """Builds a long position.

    Args:
        qty: Quantity.

    Returns:
        Position.
    """
    pos = positions.Position("p", "t", "acct", BTC, enums.OrderMode.PAPER)
    pos.apply_fill(enums.OrderSide.BUY, D(qty), D(95000), D(0), 1)
    return pos


def _close(qty: str = "0.02") -> intents.OrderIntentRequest:
    """Builds a reduce-only close of the long position.

    Args:
        qty: Quantity.

    Returns:
        Request.
    """
    return _request(
        side=enums.OrderSide.SELL,
        position_effect=enums.PositionEffect.CLOSE,
        quantity_base=D(qty),
        reduce_only=True,
    )


def test_baseline_context_allows() -> None:
    """The baseline context passes every check."""
    decision = gateway.RiskGateway().evaluate(_ctx())
    assert decision.allowed, decision.failures()
    assert len(decision.checks) == len(checks.PIPELINE)


@pytest.mark.parametrize(
    ("check", "overrides", "outcome", "code"),
    [
        (
            checks.mode_allowed,
            {"request": _request(mode=enums.OrderMode.LIVE)},
            OUT.FAIL,
            "TRADE_MODE_DISABLED",
        ),
        (checks.mode_allowed, {"account": None}, OUT.UNKNOWN, "NOT_FOUND"),
        (
            checks.identity_scope,
            {"account": dataclasses.replace(PAPER, tenant_id="other")},
            OUT.FAIL,
            "FORBIDDEN",
        ),
        (
            checks.identity_scope,
            {
                "account": dataclasses.replace(
                    PAPER, status=enums.AccountStatus.DISCONNECTED
                )
            },
            OUT.FAIL,
            "AUTH_SCOPE_MISSING",
        ),
        (
            checks.account_margin,
            {"available_margin": D(10)},
            OUT.FAIL,
            "INSUFFICIENT_MARGIN",
        ),
        (
            checks.account_margin,
            {"available_margin": None},
            OUT.UNKNOWN,
            "INSUFFICIENT_MARGIN",
        ),
        (
            checks.account_state_known,
            {"account_state_unknown": True},
            OUT.FAIL,
            "POSITION_UNKNOWN",
        ),
        (
            checks.market_status,
            {
                "instrument": dataclasses.replace(
                    INSTRUMENT, status=enums.InstrumentStatus.HALTED
                )
            },
            OUT.FAIL,
            "VENUE_UNAVAILABLE",
        ),
        (
            checks.market_status,
            {"instrument": None},
            OUT.UNKNOWN,
            "VENUE_UNAVAILABLE",
        ),
        (checks.data_quote_fresh, {"quote": None}, OUT.UNKNOWN, "MARKET_STALE"),
        (
            checks.data_quote_fresh,
            {"quote": dataclasses.replace(QUOTE, as_of_ms=NOW - 60_000)},
            OUT.FAIL,
            "MARKET_STALE",
        ),
        (
            checks.data_quote_fresh,
            {
                "quote": dataclasses.replace(
                    QUOTE, feed_state=enums.FeedState.STALE
                )
            },
            OUT.FAIL,
            "MARKET_STALE",
        ),
        (
            checks.size_quantity,
            {"request": _request(quantity_base=D("0.0105"))},
            OUT.FAIL,
            "INVALID_LOT_SIZE",
        ),
        (
            checks.size_quantity,
            {"instrument": dataclasses.replace(INSTRUMENT, min_qty=D(1))},
            OUT.FAIL,
            "INVALID_LOT_SIZE",
        ),
        (
            checks.size_tick,
            {
                "request": _request(
                    order_type=enums.OrderType.LIMIT,
                    limit_price=D("95000.05"),
                    time_in_force=enums.TimeInForce.GTC,
                )
            },
            OUT.FAIL,
            "INVALID_TICK_SIZE",
        ),
        (
            checks.size_min_notional,
            {"notional": D(1)},
            OUT.FAIL,
            "MIN_NOTIONAL",
        ),
        (
            checks.size_leverage,
            {"request": _request(leverage=D(60))},
            OUT.FAIL,
            "RISK_LIMIT",
        ),
        (
            checks.size_price_band,
            {
                "request": _request(
                    order_type=enums.OrderType.LIMIT,
                    limit_price=D(50000),
                    time_in_force=enums.TimeInForce.GTC,
                )
            },
            OUT.FAIL,
            "RISK_LIMIT",
        ),
        (
            checks.size_order_type,
            {
                "capabilities": dataclasses.replace(
                    CAPS, order_types=(enums.OrderType.LIMIT,)
                )
            },
            OUT.FAIL,
            "UNSUPPORTED_ORDER_TYPE",
        ),
        (
            checks.size_reduce_only,
            {"request": _close()},
            OUT.FAIL,
            "VALIDATION_FAILED",
        ),
        (
            checks.exposure_limits,
            {"gross_notional": D(999_500)},
            OUT.FAIL,
            "RISK_LIMIT",
        ),
        (
            checks.exposure_limits,
            {"open_position_count": 10},
            OUT.FAIL,
            "RISK_LIMIT",
        ),
        (
            checks.exposure_limits,
            {"gross_notional": None},
            OUT.UNKNOWN,
            "RISK_LIMIT",
        ),
        (
            checks.exposure_daily_loss,
            {"daily_realized_pnl": D(-5000)},
            OUT.FAIL,
            "RISK_LIMIT",
        ),
        (
            checks.liquidity_spread,
            {
                "quote": dataclasses.replace(
                    QUOTE, best_bid=D(94000), best_ask=D(96000)
                )
            },
            OUT.FAIL,
            "RISK_LIMIT",
        ),
        (
            checks.liquidity_slippage,
            {"book": dataclasses.replace(BOOK, stale=True)},
            OUT.FAIL,
            "MARKET_STALE",
        ),
        (
            checks.liquidity_slippage,
            {"slippage_pct": D(3)},
            OUT.FAIL,
            "RISK_LIMIT",
        ),
        (
            checks.liquidity_slippage,
            {"slippage_pct": None},
            OUT.FAIL,
            "RISK_LIMIT",
        ),
        (
            checks.duplicate_guard,
            {"request": _request(signal_id="s"), "signal_intents": 1},
            OUT.FAIL,
            "DUPLICATE_INTENT",
        ),
        (
            checks.connectivity,
            {"adapter_health": enums.HealthState.UNAVAILABLE},
            OUT.FAIL,
            "VENUE_UNAVAILABLE",
        ),
        (
            checks.connectivity,
            {"recent_rejects": 5},
            OUT.FAIL,
            "VENUE_UNAVAILABLE",
        ),
        (
            checks.connectivity,
            {"adapter_health": None},
            OUT.UNKNOWN,
            "VENUE_UNAVAILABLE",
        ),
        (
            checks.kill_switch_guard,
            {
                "kill_switches": (
                    kill_switch.KillSwitch(
                        "t",
                        enums.KillSwitchScope.GLOBAL,
                        None,
                        True,
                        False,
                        False,
                        "r",
                        "u",
                        1,
                    ),
                )
            },
            OUT.FAIL,
            "KILL_SWITCH_ENGAGED",
        ),
        (
            checks.kill_switch_guard,
            {"emergency_halt": True},
            OUT.FAIL,
            "KILL_SWITCH_ENGAGED",
        ),
    ],
)
def test_check_failures(
    check: checks.Check,
    overrides: dict[str, object],
    outcome: enums.CheckOutcome,
    code: str,
) -> None:
    """Each check fails (or is unknown) with an actionable code."""
    assert check(_ctx()).outcome in (OUT.PASS, OUT.SKIP, OUT.WARN)
    result = check(_ctx(**overrides))
    assert result.outcome is outcome, result
    assert result.code == code


def test_paper_on_demo_data_warns_but_live_fails() -> None:
    """DEMO data is a warning for PAPER and a failure for LIVE."""
    demo = dataclasses.replace(QUOTE, feed_state=enums.FeedState.DEMO)
    assert checks.data_quote_fresh(_ctx(quote=demo)).outcome is OUT.WARN
    live = _ctx(quote=demo, request=_request(mode=enums.OrderMode.LIVE))
    assert checks.data_quote_fresh(live).outcome is OUT.FAIL


def test_live_requires_flag_account_capability_and_consent() -> None:
    """Every LIVE gate must pass; each one alone blocks."""
    live_account = dataclasses.replace(PAPER, modes=(enums.OrderMode.LIVE,))
    req = _request(mode=enums.OrderMode.LIVE)
    flag_off = _ctx(request=req, account=live_account)
    assert checks.mode_allowed(flag_off).code == "LIVE_TRADING_DISABLED"
    unavailable = _ctx(
        request=req,
        account=live_account,
        live_trading_enabled=True,
        capabilities=dataclasses.replace(
            CAPS, status=enums.TradingStatus.TRADING_UNAVAILABLE
        ),
    )
    assert checks.mode_allowed(unavailable).code == "TRADE_MODE_DISABLED"
    unarmed = _ctx(request=req, account=live_account, live_trading_enabled=True)
    assert checks.mode_allowed(unarmed).code == "LIVE_NOT_ARMED"
    manual_ok = _ctx(
        request=req,
        account=live_account,
        live_trading_enabled=True,
        live_arming=_armed(live_account),
    )
    assert checks.mode_allowed(manual_ok).outcome is OUT.PASS
    strategy = _request(
        mode=enums.OrderMode.LIVE,
        source=enums.IntentSource.STRATEGY,
        strategy_version_id="s:v1",
        signal_id="sig",
    )
    no_consent = _ctx(
        request=strategy, account=live_account, live_trading_enabled=True
    )
    assert checks.mode_allowed(no_consent).outcome is OUT.FAIL


def _armed(
    account: accounts.Account, granted_ms: int = NOW - 1_000
) -> accounts.LiveArming:
    """Builds a one-hour LIVE arming of an account.

    Args:
        account: Account.
        granted_ms: Grant time.

    Returns:
        Arming.
    """
    return accounts.LiveArming.grant(account, "user:u", granted_ms, 3_600_000)


LIVE_ACCOUNT = dataclasses.replace(
    PAPER,
    modes=(enums.OrderMode.LIVE,),
    is_paper=False,
    credential_id="cred-1",
)


def _live_ctx(**overrides: object) -> context.RiskContext:
    """Builds a LIVE context on a LIVE account with the gate open.

    Args:
        **overrides: Field overrides.

    Returns:
        Context (not armed unless ``live_arming`` is given).
    """
    fields: dict[str, object] = {
        "request": _request(mode=enums.OrderMode.LIVE),
        "account": LIVE_ACCOUNT,
        "live_trading_enabled": True,
    }
    fields.update(overrides)
    return _ctx(**fields)


@pytest.mark.parametrize(
    ("arming", "state"),
    [
        (None, "NOT_ARMED"),
        (_armed(LIVE_ACCOUNT, NOW - 3_600_000), "EXPIRED"),
        (_armed(LIVE_ACCOUNT).revoke("user:u", NOW - 1), "REVOKED"),
        (
            _armed(dataclasses.replace(LIVE_ACCOUNT, account_id="other")),
            "OUT_OF_SCOPE",
        ),
        (
            dataclasses.replace(
                _armed(LIVE_ACCOUNT), expires_at_ms=NOW + 86_400_000
            ),
            "OUT_OF_SCOPE",
        ),
        (
            _armed(dataclasses.replace(LIVE_ACCOUNT, credential_id="cred-2")),
            "OUT_OF_SCOPE",
        ),
    ],
)
def test_manual_live_orders_need_a_valid_arming(
    arming: accounts.LiveArming | None, state: str
) -> None:
    """Missing, expired, revoked or foreign armings fail LIVE_NOT_ARMED."""
    ctx = _live_ctx(live_arming=arming)
    result = checks.mode_allowed(ctx)
    assert result.outcome is OUT.FAIL
    assert result.code == "LIVE_NOT_ARMED"
    assert result.observed == state and result.limit == "ARMED"
    decision = gateway.RiskGateway().evaluate(ctx)
    assert decision.decision is enums.Decision.BLOCK
    assert decision.primary_code() == "LIVE_NOT_ARMED"


@pytest.mark.parametrize(
    "source",
    [
        enums.IntentSource.MANUAL_TICKET,
        enums.IntentSource.CHART,
        enums.IntentSource.QUICK_TRADE,
    ],
)
def test_every_manual_source_passes_once_armed(
    source: enums.IntentSource,
) -> None:
    """Ticket, chart and quick-trade LIVE orders pass with a valid arming."""
    ctx = _live_ctx(
        request=_request(mode=enums.OrderMode.LIVE, source=source),
        live_arming=_armed(LIVE_ACCOUNT),
    )
    assert checks.mode_allowed(ctx).outcome is OUT.PASS
    assert gateway.RiskGateway().evaluate(ctx).allowed


def test_risk_reducing_live_orders_pass_while_disarmed() -> None:
    """A reduce-only close is allowed with a WARN when not armed."""
    live_long = _long()
    live_long.mode = enums.OrderMode.LIVE
    ctx = _live_ctx(
        request=dataclasses.replace(_close(), mode=enums.OrderMode.LIVE),
        position=live_long,
    )
    result = checks.mode_allowed(ctx)
    assert result.outcome is OUT.WARN and result.observed == "NOT_ARMED"
    decision = gateway.RiskGateway().evaluate(ctx)
    assert decision.allowed, decision.failures()
    flip = _live_ctx(
        request=dataclasses.replace(_close("0.05"), mode=enums.OrderMode.LIVE),
        position=live_long,
    )
    assert checks.mode_allowed(flip).code == "LIVE_NOT_ARMED"


def test_strategy_and_emergency_live_orders_do_not_use_arming() -> None:
    """AUTO_LIVE keeps deployment consent; emergency flattening is exempt."""
    lim = deployments.DeploymentLimits(D(10000), 3, D(500), (BTC,), D(5))
    expires = NOW + 3_600_000
    dep = deployments.Deployment(
        "d",
        "t",
        "s:v1",
        "acct",
        enums.DeploymentMode.AUTO_LIVE,
        enums.DeploymentState.ENABLED,
        lim,
        deployments.SizingModel("FIXED_QTY", fixed_qty=D("0.01")),
        1,
        1,
        deployments.LiveConsent(
            NOW,
            expires,
            deployments.consent_scope_hash(
                "acct", "mock", "s:v1", lim, expires
            ),
            "user:u",
        ),
    )
    strategy = _request(
        mode=enums.OrderMode.LIVE,
        source=enums.IntentSource.STRATEGY,
        strategy_version_id="s:v1",
        signal_id="sig",
    )
    consented = _live_ctx(request=strategy, deployment=dep)
    assert checks.mode_allowed(consented).outcome is OUT.PASS
    armed_only = _live_ctx(request=strategy, live_arming=_armed(LIVE_ACCOUNT))
    assert checks.mode_allowed(armed_only).code == "TRADE_MODE_DISABLED"
    emergency = _live_ctx(
        request=_request(
            mode=enums.OrderMode.LIVE, source=enums.IntentSource.EMERGENCY
        )
    )
    assert checks.mode_allowed(emergency).outcome is OUT.PASS


def test_live_arming_domain_lifecycle() -> None:
    """Grant, state, revoke (idempotent) and serialization of an arming."""
    arming = _armed(LIVE_ACCOUNT)
    assert arming.expires_at_ms == NOW - 1_000 + 3_600_000
    assert arming.acknowledgement == accounts.LIVE_ARM_ACKNOWLEDGEMENT
    assert arming.scope_hash.startswith("sha256:")
    assert arming.valid_for(LIVE_ACCOUNT, NOW)
    assert arming.state(LIVE_ACCOUNT, arming.expires_at_ms) == "EXPIRED"
    revoked = arming.revoke("user:x", NOW)
    assert revoked.revoke("user:y", NOW + 5) is revoked
    assert revoked.revoked_by == "user:x" and revoked.revoked_at_ms == NOW
    assert not revoked.valid_for(LIVE_ACCOUNT, NOW)
    body = revoked.to_contract()
    assert body["granted_by"] == "user:u" and body["revoked_by"] == "user:x"
    assert accounts.LIVE_ARM_DEFAULT_MS == 8 * 3_600_000
    assert accounts.LIVE_ARM_CEILING_MS == 24 * 3_600_000


def test_auto_live_requires_native_stop_loss() -> None:
    """Automated live orders need a venue-native stop and a stop leg."""
    strategy = _request(
        mode=enums.OrderMode.LIVE,
        source=enums.IntentSource.STRATEGY,
        strategy_version_id="s:v1",
        signal_id="sig",
    )
    no_native = _ctx(
        request=strategy,
        capabilities=dataclasses.replace(CAPS, native_stop_loss=False),
    )
    assert checks.protection(no_native).code == "UNSUPPORTED"
    no_leg = _ctx(request=strategy)
    assert checks.protection(no_leg).code == "VALIDATION_FAILED"
    with_leg = _ctx(
        request=dataclasses.replace(
            strategy,
            bracket=intents.Bracket(stop_loss=intents.StopLossSpec(D(94000))),
        )
    )
    assert checks.protection(with_leg).outcome is OUT.PASS


def test_kill_switch_allows_risk_reducing_orders() -> None:
    """Reduce-only closes pass a kill switch with a warning."""
    switch = kill_switch.KillSwitch(
        "t",
        enums.KillSwitchScope.ACCOUNT,
        "acct",
        True,
        False,
        False,
        "r",
        "u",
        1,
    )
    ctx = _ctx(
        request=_close(),
        position=_long(),
        kill_switches=(switch,),
        account_state_unknown=True,
        quote=None,
        book=None,
    )
    decision = gateway.RiskGateway().evaluate(ctx)
    assert decision.allowed, decision.failures()
    outcomes = {c.check: c.outcome for c in decision.checks}
    assert outcomes["kill_switch.engaged"] is OUT.WARN
    assert outcomes["size.reduce_only"] is OUT.PASS


def test_strategy_decision_quality() -> None:
    """Strategy intents need a matching deployment and a fresh signal."""
    strategy = _request(
        source=enums.IntentSource.STRATEGY,
        strategy_version_id="s:v1",
        signal_id="sig",
    )
    lim = deployments.DeploymentLimits(D(10000), 3, D(500), (), D(5))
    dep = deployments.Deployment(
        "d",
        "t",
        "s:v1",
        "acct",
        enums.DeploymentMode.PAPER,
        enums.DeploymentState.ENABLED,
        lim,
        deployments.SizingModel("FIXED_QTY", fixed_qty=D("0.01")),
        1,
        1,
    )
    assert checks.decision_quality(_ctx(request=strategy)).outcome is OUT.FAIL
    fresh = _ctx(request=strategy, deployment=dep, signal_age_ms=1000)
    assert checks.decision_quality(fresh).outcome is OUT.PASS
    stale = _ctx(request=strategy, deployment=dep, signal_age_ms=999_999)
    assert checks.decision_quality(stale).outcome is OUT.FAIL
    other = dataclasses.replace(dep, strategy_version_id="s:v2")
    assert (
        checks.decision_quality(
            _ctx(request=strategy, deployment=other, signal_age_ms=1)
        ).outcome
        is OUT.FAIL
    )
    allow_list = dataclasses.replace(
        dep,
        limits=dataclasses.replace(
            lim, allowed_instruments=("mock:perp:ETHUSD",)
        ),
    )
    assert (
        checks.decision_quality(
            _ctx(request=strategy, deployment=allow_list, signal_age_ms=1)
        ).outcome
        is OUT.FAIL
    )
    busy = _ctx(
        request=strategy,
        deployment=dep,
        signal_age_ms=1,
        strategy_open_orders=1,
    )
    assert checks.duplicate_guard(busy).code == "DUPLICATE_INTENT"


def test_aggregation_reject_pause_block_and_fail_closed() -> None:
    """Size failures reject, daily loss pauses, unknowns block."""
    gw = gateway.RiskGateway()
    reject = gw.evaluate(_ctx(notional=D(1)))
    assert reject.decision is enums.Decision.REJECT
    pause = gw.evaluate(_ctx(daily_realized_pnl=D(-6000)))
    assert pause.decision is enums.Decision.PAUSE
    block = gw.evaluate(_ctx(quote=None))
    assert block.decision is enums.Decision.BLOCK

    def explode(ctx: context.RiskContext) -> object:
        """Raises to simulate a buggy check.

        Args:
            ctx: Context.

        Raises:
            RuntimeError: Always.
        """
        raise RuntimeError("bug")

    broken = gateway.RiskGateway((checks.CheckSpec(explode),))  # type: ignore[arg-type]
    decision = broken.evaluate(_ctx())
    assert decision.decision is enums.Decision.BLOCK
    assert decision.checks[0].outcome is OUT.UNKNOWN


def test_limits_are_clamped_and_validated() -> None:
    """User limits never exceed platform caps; bad keys are rejected."""
    effective = limits.from_mapping(
        {"max_leverage": "500", "max_open_positions": "3"}
    )
    assert effective.max_leverage == limits.PLATFORM_HARD_LIMITS.max_leverage
    assert effective.max_open_positions == 3
    with pytest.raises(Exception, match="unknown risk limit"):
        limits.from_mapping({"max_leverge": "2"})
    with pytest.raises(Exception, match="invalid value"):
        limits.from_mapping({"max_leverage": "-1"})
    assert limits.from_mapping(effective.to_dict()) == effective
