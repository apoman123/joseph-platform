# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Safety tests: broker adapters stay behind every LIVE gate."""

from __future__ import annotations

import dataclasses
import datetime
import decimal

import pytest

from joseph_execution import bootstrap
from joseph_execution.adapters import vault
from joseph_execution.adapters.brokers import ibkr
from joseph_execution.domain import enums
from joseph_execution.domain import errors
from joseph_execution.domain import intents
from joseph_execution.platform import config
from joseph_execution.ports import trading
from joseph_execution.testing import equities as fixtures
from joseph_execution.testing import fake_ib
from joseph_execution.testing import harness

D = decimal.Decimal
MONDAY = datetime.date(2026, 11, 23)


def _live_request(
    account_id: str, **overrides: object
) -> intents.OrderIntentRequest:
    """Builds a LIVE AAPL limit buy.

    Args:
        account_id: Account to trade.
        **overrides: Request fields that replace the defaults.

    Returns:
        A manual DAY limit buy of 10 AAPL at 190.01.
    """
    fields: dict[str, object] = {
        "account_id": account_id,
        "instrument_id": fixtures.AAPL,
        "side": enums.OrderSide.BUY,
        "position_effect": enums.PositionEffect.OPEN,
        "order_type": enums.OrderType.LIMIT,
        "limit_price": D("190.01"),
        "quantity_base": D(10),
        "time_in_force": enums.TimeInForce.DAY,
        "reduce_only": False,
        "mode": enums.OrderMode.LIVE,
        "source": enums.IntentSource.MANUAL_TICKET,
    }
    fields.update(overrides)
    return intents.OrderIntentRequest(**fields)  # type: ignore[arg-type]  # dict[str, object] holds each field's own type


async def _connect(h: harness.Harness, venue: str, number: str) -> str:
    """Connects a broker account and forces LIVE mode on it.

    Args:
        h: Harness.
        venue: Broker venue id, such as ``ibkr``; also the account label.
        number: Broker account number.

    Returns:
        The new account id, CONNECTED with LIVE mode.
    """
    services = h.container.services
    summary = await services.accounts.connect_start(
        harness.TENANT_A, venue, venue, {"account": number}, True
    )
    account_id = str(summary["account_id"])
    _force_live(h, account_id)
    return account_id


def _force_live(h: harness.Harness, account_id: str) -> None:
    """Grants LIVE mode to an account (bypassing verification).

    Args:
        h: Harness.
        account_id: Account to update.
    """
    with h.container.uow_factory() as uow:
        account = uow.accounts.get(account_id)
        assert account is not None
        uow.accounts.save(
            dataclasses.replace(
                account,
                status=enums.AccountStatus.CONNECTED,
                modes=(enums.OrderMode.LIVE,),
            )
        )


async def test_default_deployment_never_reaches_a_broker() -> None:
    """With LIVE off nothing connects and LIVE orders are blocked."""
    h = harness.build()
    h.clock.current_ms = fixtures.new_york(MONDAY, 10)
    fixtures.install(h.market, MONDAY)
    h.market.feed_state = enums.FeedState.LIVE
    account_id = await _connect(h, "ibkr", "DU1234567")
    verified = await h.container.services.accounts.verify(
        harness.TENANT_A, account_id
    )
    assert verified["status"] == "TRADING_UNAVAILABLE"
    _force_live(h, account_id)
    request = _live_request(account_id)
    preview = await h.container.services.oms.preview(harness.TENANT_A, request)
    assert preview.decision.primary_code() == "LIVE_TRADING_DISABLED"
    token = dataclasses.replace(preview.normalized, preview_token=preview.token)
    result = await h.container.services.oms.submit_intent(
        harness.TENANT_A, token, "blocked"
    )
    assert not result.decision.allowed and result.orders == ()
    for adapter in h.container.brokers.values():
        assert "submit_order" not in adapter.calls  # type: ignore[attr-defined]  # the BrokerAdapter protocol omits calls; every broker records it
    ib_adapter = h.container.brokers["ibkr"]
    assert isinstance(ib_adapter, ibkr.IbkrAdapter)
    assert ib_adapter._factory is None
    assert await bootstrap.pump_brokers(h.container) == 0


async def test_live_flag_alone_does_not_open_a_broker() -> None:
    """A broker stays gated until it is explicitly enabled."""
    h = harness.build(live_trading_enabled=True)
    account_id = await _connect(h, "sinopac", "9A95-0000001")
    caps = await h.container.services.accounts.capabilities(
        h.container.services.accounts.get(harness.TENANT_A, account_id)
    )
    assert caps.status is enums.TradingStatus.TRADING_UNAVAILABLE
    assert "JOSEPH_SINOPAC_ENABLED" in caps.notes[-1]
    adapter = h.container.brokers["sinopac"]
    with pytest.raises(errors.AdapterUnavailableError):
        await adapter.submit_order(
            dataclasses.replace(
                _live_venue_request(account_id), instrument_id=fixtures.TSMC
            )
        )


def _live_venue_request(account_id: str) -> trading.VenueOrderRequest:
    """Builds the venue request of a LIVE order.

    Args:
        account_id: Account to trade.

    Returns:
        A DAY limit buy of 10 AAPL at 190.01 with a fixed client order id.
    """
    return trading.VenueOrderRequest(
        account_id=account_id,
        instrument_id=fixtures.AAPL,
        client_order_id="j" + "1" * 30,
        side=enums.OrderSide.BUY,
        order_type=enums.OrderType.LIMIT,
        quantity_base=D(10),
        time_in_force=enums.TimeInForce.DAY,
        reduce_only=False,
        limit_price=D("190.01"),
    )


async def test_every_gate_still_applies_to_open_brokers() -> None:
    """Preview, consent, arming and the kill switch guard an open broker."""
    h = harness.build(
        live_trading_enabled=True,
        securities=config.SecuritiesSettings(ibkr_enabled=True),
    )
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
        ibkr.IbkrConfig(ack_timeout_s=0.05),
        lambda: fake_ib.FakeIbFactory(ib),
        h.market,
        h.clock,
        lookup,
    )
    h.container.registry.live["ibkr"] = adapter
    account_id = await _connect(h, "ibkr", "DU1234567")
    services = h.container.services
    with pytest.raises(errors.ExecutionError) as info:
        await services.oms.submit_intent(
            harness.TENANT_A, _live_request(account_id), "no-preview"
        )
    assert info.value.code == "PREVIEW_REQUIRED"
    strategy = _live_request(
        account_id,
        source=enums.IntentSource.STRATEGY,
        strategy_version_id="s:v1",
        signal_id="sig-1",
    )
    auto = await services.oms.submit_intent(harness.TENANT_A, strategy, "auto")
    assert auto.decision.primary_code() == "TRADE_MODE_DISABLED"
    unarmed = await services.oms.preview(
        harness.TENANT_A, _live_request(account_id)
    )
    assert unarmed.decision.primary_code() == "LIVE_NOT_ARMED"
    armed = await services.accounts.grant_live_consent(
        harness.TENANT_A, account_id, h.totp(), True
    )
    assert armed["live_armed_until_ms"] is not None
    await services.kill_switches.set(
        harness.TENANT_A,
        enums.KillSwitchScope.ACCOUNT,
        account_id,
        True,
        False,
        False,
        "drill",
    )
    preview = await services.oms.preview(
        harness.TENANT_A, _live_request(account_id)
    )
    assert preview.decision.primary_code() == "KILL_SWITCH_ENGAGED"
    assert ib.placed == []


async def test_broker_secrets_never_travel_through_the_api() -> None:
    """Broker connections refuse login fields; configs hide secrets."""
    h = harness.build()
    with pytest.raises(errors.ExecutionError) as info:
        await h.container.services.accounts.connect_start(
            harness.TENANT_A,
            "ibkr",
            "x",
            {"account": "DU1234567", "password": "hunter2-secret"},
            True,
        )
    assert info.value.code == "VALIDATION_FAILED"
    assert "hunter2-secret" not in info.value.message
    settings = config.SecuritiesSettings(
        sinopac_api_key="api-key-value", sinopac_secret_key="secret-value-x"
    )
    assert "secret-value-x" not in repr(settings)
    assert "secret-value-x" not in repr(h.container.settings)
