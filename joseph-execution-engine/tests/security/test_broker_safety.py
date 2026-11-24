# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Safety tests: broker adapters stay behind every LIVE gate."""

from __future__ import annotations

import dataclasses
import datetime
import decimal
from typing import Any

import pytest

from joseph_execution import bootstrap
from joseph_execution.adapters import market_static
from joseph_execution.adapters import vault
from joseph_execution.adapters.brokers import ibkr
from joseph_execution.adapters.brokers import sinopac
from joseph_execution.domain import enums
from joseph_execution.domain import errors
from joseph_execution.domain import intents
from joseph_execution.platform import clock as clock_lib
from joseph_execution.platform import config
from joseph_execution.ports import trading
from joseph_execution.testing import equities as fixtures
from joseph_execution.testing import fake_ib
from joseph_execution.testing import fake_shioaji
from joseph_execution.testing import harness

_Decimal = decimal.Decimal
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
        "limit_price": _Decimal("190.01"),
        "quantity_base": _Decimal(10),
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
        quantity_base=_Decimal(10),
        time_in_force=enums.TimeInForce.DAY,
        reduce_only=False,
        limit_price=_Decimal("190.01"),
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


async def test_ibkr_error_log_never_shows_an_account_number(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """TWS error text is logged with every account number masked."""
    clock = clock_lib.ManualClock(fixtures.new_york(MONDAY, 10))
    gateway = market_static.StaticMarketDataGateway(clock)
    fixtures.install(gateway, MONDAY)
    ib = fake_ib.FakeIB()
    adapter = ibkr.IbkrAdapter(
        ibkr.IbkrConfig(),
        lambda: fake_ib.FakeIbFactory(ib),
        gateway,
        clock,
        # A bound number not shaped like an IBKR one is masked too.
        {"acct-ib": "DU1234567", "acct-odd": "acct98765x"}.get,
    )
    assert adapter.bind("acct-ib") and adapter.bind("acct-odd")
    await adapter.capabilities("acct-ib")
    with caplog.at_level("INFO", logger=ibkr.__name__):
        ib.errorEvent.emit(
            7,
            201,
            "Order rejected - reason: account DU1234567 lacks permissions; "
            "advisor F7654321 and acct98765x were notified.",
            None,
        )
    assert "Order rejected" in caplog.text
    for number in ("DU1234567", "F7654321", "acct98765x"):
        assert number not in caplog.text
    assert "***4567" in caplog.text and "***4321" in caplog.text
    await adapter.close()


async def _submit_previewed(
    h: harness.Harness, account_id: str, key: str, **overrides: object
) -> list[dict[str, object]]:
    """Previews and submits a LIVE buy through the OMS.

    Args:
        h: Harness.
        account_id: Armed broker account.
        key: Idempotency key.
        **overrides: Request fields that replace the AAPL defaults.

    Returns:
        The contract ``Order`` of each order the submission created.
    """
    oms = h.container.services.oms
    preview = await oms.preview(
        harness.TENANT_A, _live_request(account_id, **overrides)
    )
    assert preview.allowed, preview.decision.failures()
    tokened = dataclasses.replace(
        preview.normalized, preview_token=preview.token
    )
    result = await oms.submit_intent(harness.TENANT_A, tokened, key)
    return [order.to_contract() for order in result.orders]


async def test_ibkr_account_numbers_never_reach_orders_audit_or_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """TWS rejection and cancel texts are masked before the OMS sees them."""
    caplog.set_level("DEBUG")
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
    adapter = ibkr.IbkrAdapter(
        ibkr.IbkrConfig(ack_timeout_s=0.05, commission_wait_s=0.01),
        lambda: fake_ib.FakeIbFactory(ib),
        h.market,
        h.clock,
        bootstrap.broker_account_lookup(
            h.container.uow_factory,
            vault.AesGcmVault(h.container.settings.vault_master_key),
        ),
    )
    h.container.brokers["ibkr"] = adapter
    h.container.registry.live["ibkr"] = adapter
    services = h.container.services
    account_id = await _connect(h, "ibkr", "DU1234567")
    await services.accounts.grant_live_consent(
        harness.TENANT_A, account_id, h.totp(), True
    )
    ib.reject_next = (
        201,
        "Order rejected - reason: account DU1234567 may not trade this "
        "product; advisor F7654321 was notified.",
    )
    (rejected,) = await _submit_previewed(h, account_id, "rejected")
    assert rejected["state"] == "REJECTED"
    (working,) = await _submit_previewed(h, account_id, "working")
    hold = ib.cancelOrder

    def _refuse(order: fake_ib.FakeOrder) -> fake_ib.FakeTrade | None:
        """Leaves the order PendingCancel and refuses the cancel.

        Args:
            order: Order to cancel.

        Returns:
            The trade, still pending cancel.
        """
        ib.cancel_mode = "hold"
        trade = hold(order)
        ib.errorEvent.emit(
            order.orderId,
            10148,
            "OrderId cannot be cancelled for account DU1234567.",
            None,
        )
        return trade

    ib.cancelOrder = _refuse  # type: ignore[method-assign]  # injects a cancel refusal into this fake
    refused = await services.oms.cancel_order(
        harness.TENANT_A, str(working["order_id"])
    )
    listed = services.oms.list_orders(harness.TENANT_A, account_id, None)
    with h.container.uow_factory() as uow:
        audit = [e.to_contract() for e in uow.audit.chain("tenant-a")]
    exposed = {
        "rejected order": str(rejected),
        "refused cancel": str(refused.to_contract()),
        "listed orders": str([o.to_contract() for o in listed]),
        "audit log": str(audit),
        "logs": caplog.text,
    }
    assert "***4567" in exposed["rejected order"]
    assert "***4321" in exposed["rejected order"]
    assert "cancel failed" in exposed["refused cancel"]
    assert "***4567" in exposed["refused cancel"]
    for where, text in exposed.items():
        for number in ("DU1234567", "F7654321"):
            assert number not in text, f"{number} in {where}"


async def test_sinopac_identifiers_never_reach_orders_audit_or_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Shioaji rejection and update texts are masked before the OMS."""
    caplog.set_level("DEBUG")
    h = harness.build(live_trading_enabled=True)
    h.clock.current_ms = fixtures.taipei(MONDAY, 10)
    fixtures.install(h.market, MONDAY - datetime.timedelta(days=7))
    h.market.feed_state = enums.FeedState.LIVE
    api = fake_shioaji.FakeShioaji()
    # Enough settled cash for the amendment's re-check of a second lot.
    api.balance = 10_000_000.0
    adapter = sinopac.SinopacAdapter(
        sinopac.SinopacConfig(
            api_key="key-123456",
            secret_key="secret-123456",
            ca_path="/run/joseph-secrets/Sinopac.pfx",
            ca_password="ca-pass-123",
            person_id="A123456789",
        ),
        lambda: fake_shioaji.FakeSjFactory(api),
        h.clock,
        bootstrap.broker_account_lookup(
            h.container.uow_factory,
            vault.AesGcmVault(h.container.settings.vault_master_key),
        ),
        runner=fake_shioaji.run_inline,
        market_data=h.market,
    )
    h.container.brokers["sinopac"] = adapter
    h.container.registry.live["sinopac"] = adapter
    services = h.container.services
    account_id = await _connect(h, "sinopac", "9A95-0000001")
    await services.accounts.grant_live_consent(
        harness.TENANT_A, account_id, h.totp(), True
    )
    tsmc = {
        "instrument_id": fixtures.TSMC,
        "limit_price": _Decimal(601),
        "quantity_base": _Decimal(1000),
    }
    leak = (
        "account 9A95-0000001 (0000001) of A123456789, co-holder "
        "B287654321, is not permitted"
    )
    api.reject_next = f"委託失敗: {leak}"
    (rejected,) = await _submit_previewed(h, account_id, "rejected", **tsmc)
    assert rejected["state"] == "REJECTED"
    (working,) = await _submit_previewed(h, account_id, "working", **tsmc)

    def _refuse(trade: Any, price: Any = None, qty: Any = None) -> Any:
        """Reports a failed price update whose message names the account.

        Args:
            trade: Fake trade being amended.
            price: Ignored new price.
            qty: Ignored new quantity.

        Returns:
            ``trade``, unchanged.
        """
        del price, qty
        api._order_event(trade, "UpdatePrice", "77", f"locked: {leak}")
        return trade

    api.update_order = _refuse  # type: ignore[method-assign, assignment]  # injects a failure; the adapter never passes timeout
    # Deliver callbacks synchronously, as an SDK reply would before return.
    api.set_order_callback(adapter._on_order)
    with pytest.raises(errors.ExecutionError) as info:
        await services.oms.amend_order(
            harness.TENANT_A,
            str(working["order_id"]),
            _Decimal(600),
            None,
            None,
            None,
        )
    assert info.value.code == "CONFLICT"
    listed = services.oms.list_orders(harness.TENANT_A, account_id, None)
    with h.container.uow_factory() as uow:
        audit = [e.to_contract() for e in uow.audit.chain("tenant-a")]
    exposed = {
        "rejected order": str(rejected),
        "refused amend": info.value.message,
        "listed orders": str([o.to_contract() for o in listed]),
        "audit log": str(audit),
        "logs": caplog.text,
    }
    for where in ("rejected order", "refused amend"):
        assert "***0001" in exposed[where], where
    for where, text in exposed.items():
        for secret in ("9A95-0000001", "0000001", "A123456789", "B287654321"):
            assert secret not in text, f"{secret} in {where}"
