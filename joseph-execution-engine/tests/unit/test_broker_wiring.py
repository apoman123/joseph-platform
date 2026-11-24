# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Unit tests for broker wiring: catalog, Cathay, config and market data."""

from __future__ import annotations

import decimal
import pathlib

import httpx
import pytest
import respx

from joseph_execution import bootstrap
from joseph_execution.adapters import market_http
from joseph_execution.adapters.brokers import catalog
from joseph_execution.adapters.brokers import cathay
from joseph_execution.adapters.brokers import ibkr
from joseph_execution.domain import enums
from joseph_execution.domain import errors
from joseph_execution.domain import risk
from joseph_execution.platform import clock as clock_lib
from joseph_execution.platform import config
from joseph_execution.platform import logging as log_lib
from joseph_execution.ports import trading
from joseph_execution.risk import limits
from joseph_execution.testing import equities as fixtures
from joseph_execution.testing import fake_ib
from joseph_execution.testing import harness

_Decimal = decimal.Decimal


def test_gate_reasons() -> None:
    """Brokers open only with the LIVE flag and an explicit enable."""
    assert catalog.gate_reason(False, True, "X") == catalog.LIVE_OFF
    assert "X=false" in (catalog.gate_reason(True, False, "X") or "")
    assert catalog.gate_reason(True, True, "X") is None


async def test_catalog_builds_gated_brokers_without_clients() -> None:
    """Default wiring builds every broker gated and never creates clients."""
    created: list[str] = []

    def ib_factory() -> ibkr.IbFactory:
        """Records that the IB client factory was created.

        Returns:
            A fake IB client factory.
        """
        created.append("ib")
        return fake_ib.FakeIbFactory(fake_ib.FakeIB())

    clock = clock_lib.ManualClock()
    adapters = catalog.build(
        live_trading_enabled=False,
        ibkr_config=ibkr.IbkrConfig(),
        ibkr_enabled=True,
        sinopac_config=None,
        sinopac_enabled=True,
        market_data=harness.build().market,
        clock=clock,
        account_lookup=lambda account_id: "DU1",
        ib_factory=ib_factory,
    )
    assert sorted(adapters) == ["cathay", "ibkr", "sinopac"]
    for adapter in adapters.values():
        caps = await adapter.capabilities("acct")
        assert caps.status is enums.TradingStatus.TRADING_UNAVAILABLE
        with pytest.raises(errors.AdapterUnavailableError) as info:
            await adapter.submit_order(_request("acct", fixtures.AAPL))
        assert info.value.code == "LIVE_TRADING_DISABLED"
    assert created == []


def _request(account_id: str, instrument_id: str) -> trading.VenueOrderRequest:
    """Builds a minimal venue request.

    Args:
        account_id: Account to trade.
        instrument_id: Instrument to buy.

    Returns:
        A DAY limit buy of 1 unit at 1 with a fixed client order id.
    """
    return trading.VenueOrderRequest(
        account_id=account_id,
        instrument_id=instrument_id,
        client_order_id="j" + "0" * 30,
        side=enums.OrderSide.BUY,
        order_type=enums.OrderType.LIMIT,
        quantity_base=_Decimal(1),
        time_in_force=enums.TimeInForce.DAY,
        reduce_only=False,
        limit_price=_Decimal(1),
    )


async def test_cathay_is_fail_closed_awaiting_the_sdk() -> None:
    """Cathay declares TWSE/TPEx but refuses everything until its SDK."""
    adapter = cathay.build()
    caps = await adapter.capabilities("acct")
    assert caps.status is enums.TradingStatus.TRADING_UNAVAILABLE
    assert caps.tradable_venues == ("twse", "tpex")
    assert caps.currencies == ("TWD",)
    assert "Awaiting the Cathay API SDK" in caps.notes[-1]
    assert await adapter.health() is enums.HealthState.UNAVAILABLE
    for call in (
        adapter.account_snapshot("a"),
        adapter.list_open_orders("a"),
        adapter.list_positions("a"),
        adapter.list_recent_fills("a"),
        adapter.submit_order(_request("a", fixtures.TSMC)),
        adapter.cancel_order("a", "x"),
        adapter.amend_order("a", "x", _Decimal(1), None, None),
        adapter.get_order_by_client_id("a", "x"),
    ):
        with pytest.raises(errors.AdapterUnavailableError) as info:
            await call
        assert "Awaiting the Cathay API SDK" in info.value.message
    with pytest.raises(errors.AdapterUnavailableError):
        await anext(adapter.stream_account_events("a"))
    assert adapter.name == "cathay"
    assert not adapter.bind("a") and adapter.drain_events("a") == []
    assert adapter.event_accounts() == []
    assert await adapter.resync() == 0
    await adapter.close()
    assert "submit_order" in adapter.calls


def test_container_registers_brokers_behind_the_live_gate() -> None:
    """The container wires broker adapters into the live registry, gated."""
    h = harness.build()
    assert sorted(h.container.brokers) == ["cathay", "ibkr", "sinopac"]
    for name, adapter in h.container.brokers.items():
        assert h.container.registry.live[name] is adapter
    assert "binance" in h.container.registry.live
    assert not h.container.services.oms.settings.queue_closed_market_orders


async def test_pump_brokers_without_brokers_is_a_no_op() -> None:
    """The broker pump does nothing without brokers; four jobs remain."""
    h = harness.build()
    h.container.brokers.clear()
    assert await bootstrap.pump_brokers(h.container) == 0
    jobs = bootstrap.background_jobs(h.container)
    assert len(jobs) == 4


def test_sinopac_settings_build_a_config() -> None:
    """Configured Shioaji credentials wire the adapter and stay out of repr."""
    settings = config.SecuritiesSettings(
        sinopac_enabled=True,
        sinopac_api_key="k" * 8,
        sinopac_secret_key="s" * 8,
    )
    h = harness.build(securities=settings)
    adapter = h.container.brokers["sinopac"]
    assert adapter.name == "sinopac"
    assert settings.sinopac_configured
    assert "s" * 8 not in repr(settings)


def test_securities_settings_from_env(tmp_path: pathlib.Path) -> None:
    """Broker settings load from env and secret files, redacted."""
    secret = tmp_path / "secret"
    secret.write_text("shioaji-secret-value\n", encoding="utf-8")
    settings = config.securities_from_env(
        {
            "JOSEPH_EQUITY_CLOSED_MARKET_ORDERS": "queue",
            "JOSEPH_IBKR_ENABLED": "true",
            "JOSEPH_IBKR_HOST": "ib-gateway",
            "JOSEPH_IBKR_PORT": "4004",
            "JOSEPH_IBKR_CLIENT_ID": "21",
            "JOSEPH_SINOPAC_ENABLED": "1",
            "JOSEPH_SINOPAC_SIMULATION": "false",
            "JOSEPH_SINOPAC_API_KEY": "shioaji-api-key",
            "JOSEPH_SINOPAC_SECRET_KEY_FILE": str(secret),
            "JOSEPH_SINOPAC_CA_PATH": "/run/secrets/Sinopac.pfx",
            "JOSEPH_PAPER_TWD_STARTING_CASH": "off",
        }
    )
    assert settings.closed_market_orders == "queue"
    assert (
        settings.ibkr_host,
        settings.ibkr_port,
        settings.ibkr_client_id,
    ) == (
        "ib-gateway",
        4004,
        21,
    )
    assert settings.ibkr_paper and settings.sinopac_enabled
    assert not settings.sinopac_simulation
    assert settings.sinopac_secret_key == "shioaji-secret-value"
    assert settings.paper_twd_starting_cash is None
    assert "shioaji-secret-value" not in repr(settings)
    scrubbed = log_lib.SECRETS.scrub("token shioaji-secret-value")
    assert "shioaji-secret-value" not in scrubbed
    defaults = config.securities_from_env({})
    assert defaults.paper_twd_starting_cash == _Decimal(3_000_000)
    assert not defaults.ibkr_enabled and defaults.sinopac_simulation
    full = config.from_env({"JOSEPH_IBKR_PORT": "4002"})
    assert full.securities.ibkr_port == 4002


@pytest.mark.parametrize(
    "env",
    [
        {"JOSEPH_EQUITY_CLOSED_MARKET_ORDERS": "maybe"},
        {"JOSEPH_IBKR_PORT": "4001"},
        {"JOSEPH_IBKR_PORT": "4002", "JOSEPH_IBKR_PAPER": "false"},
        {"JOSEPH_SINOPAC_API_KEY_FILE": "/nonexistent/joseph/secret"},
    ],
)
def test_securities_settings_reject_inconsistent_values(
    env: dict[str, str],
) -> None:
    """Paper/live port mismatches and bad values fail at startup."""
    with pytest.raises(ValueError, match="JOSEPH_"):
        config.securities_from_env(env)


def test_money_limits_scale_with_the_account_currency() -> None:
    """TWD accounts get TWD-sized notional and loss limits."""
    usd = limits.from_mapping(None)
    twd = limits.from_mapping(None, "TWD")
    assert twd.max_position_notional == usd.max_position_notional * 30
    assert twd.max_daily_loss == usd.max_daily_loss * 30
    assert twd.max_leverage == usd.max_leverage
    capped = limits.from_mapping({"max_position_notional": "99999999"}, "TWD")
    assert capped.max_position_notional == (
        limits.PLATFORM_HARD_LIMITS.max_position_notional * 30
    )
    assert limits.hard_limits("USD") == limits.PLATFORM_HARD_LIMITS
    assert limits.money_factor("TWD") == risk.DEFAULT_TWD_LIMIT_FACTOR == 30
    assert limits.money_factor("USD", _Decimal(40)) == 1
    custom = limits.from_mapping(None, "TWD", _Decimal(32))
    assert custom.max_account_notional == usd.max_account_notional * 32
    assert limits.hard_limits("TWD", _Decimal(32)).max_daily_loss == (
        limits.PLATFORM_HARD_LIMITS.max_daily_loss * 32
    )
    assert limits.from_mapping(None, "USD", _Decimal(32)) == usd


def test_twd_limit_factor_is_configurable() -> None:
    """JOSEPH_TWD_LIMIT_FACTOR sets the TWD factor; the default stays 30."""
    assert config.securities_from_env({}).twd_limit_factor == _Decimal(30)
    assert config.securities_from_env(
        {"JOSEPH_TWD_LIMIT_FACTOR": " "}
    ).twd_limit_factor == _Decimal(30)
    tuned = config.from_env({"JOSEPH_TWD_LIMIT_FACTOR": "31.5"})
    assert tuned.securities.twd_limit_factor == _Decimal("31.5")
    for bad in ("0", "-30", "thirty", "NaN", "Infinity"):
        with pytest.raises(ValueError, match="JOSEPH_TWD_LIMIT_FACTOR"):
            config.securities_from_env({"JOSEPH_TWD_LIMIT_FACTOR": bad})


@respx.mock
async def test_http_gateway_reads_sessions_closes_and_securities() -> None:
    """The HTTP gateway parses calendars, daily closes and TWSE metadata."""
    base = "http://market"
    respx.get(f"{base}/v1/calendars/XTAI/sessions").mock(
        return_value=httpx.Response(
            200,
            json={
                "calendar": "XTAI",
                "from_ms": 0,
                "to_ms": 10,
                "sessions": [
                    {"day": "2026-11-24", "open_ms": 200, "close_ms": 300},
                    {"day": "2026-11-23", "open_ms": 100, "close_ms": 150},
                ],
            },
        )
    )
    respx.get(f"{base}/v1/candles").mock(
        return_value=httpx.Response(
            200,
            json={
                "candles": [
                    {"start_ms": 1, "close": "598", "is_final": True},
                    {"start_ms": 2, "close": "600", "is_final": True},
                    {"start_ms": 3, "close": "605", "is_final": False},
                ]
            },
        )
    )
    respx.get(f"{base}/v1/instruments").mock(
        return_value=httpx.Response(
            200,
            json={
                "instruments": [
                    {
                        "instrument_id": fixtures.TSMC,
                        "key": {"venue": "twse", "quote": "TWD"},
                        "tick_size": "0.01",
                        "lot_size": "1",
                        "min_qty": "1",
                        "status": "TRADING",
                        "calendar": "XTAI",
                        "listing_exchange": "TWSE",
                        "tick_ladder": [
                            {"min_price": "500", "tick_size": "1"},
                            {"min_price": "0", "tick_size": "0.01"},
                        ],
                        "price_limit_pct": "10",
                        "board_lot": "1000",
                        "asset_class": "EQUITY",
                    }
                ]
            },
        )
    )
    gateway = market_http.HttpMarketDataGateway(base)
    sessions = await gateway.sessions("XTAI", 0, 10)
    assert sessions is not None
    assert [s.day for s in sessions] == ["2026-11-23", "2026-11-24"]
    assert await gateway.sessions("XTAI", 0, 10) is sessions
    assert await gateway.sessions("24x7", 0, 10) == ()
    assert await gateway.previous_close(fixtures.TSMC) == _Decimal(600)
    assert await gateway.previous_close(fixtures.TSMC) == _Decimal(600)
    meta = await gateway.instrument(fixtures.TSMC)
    assert meta is not None and meta.board_lot == _Decimal(1000)
    assert (
        meta.tick_for(_Decimal(600)) == _Decimal(1) and meta.calendar == "XTAI"
    )
    assert meta.listing_exchange == "TWSE" and meta.quote == "TWD"
    assert meta.asset_class is enums.AssetClass.EQUITY
    await gateway.close()


@respx.mock
async def test_http_gateway_fails_closed_on_bad_payloads() -> None:
    """Bad calendars and candles return None; bare instruments get defaults."""
    base = "http://market"
    respx.get(f"{base}/v1/calendars/XNYS/sessions").mock(
        return_value=httpx.Response(200, json={"sessions": "nope"})
    )
    respx.get(f"{base}/v1/calendars/XTAI/sessions").mock(
        return_value=httpx.Response(503)
    )
    respx.get(f"{base}/v1/candles").mock(
        return_value=httpx.Response(
            200, json={"candles": [{"close": "x", "is_final": True}]}
        )
    )
    gateway = market_http.HttpMarketDataGateway(base)
    assert await gateway.sessions("XNYS", 0, 1) is None
    assert await gateway.sessions("XTAI", 0, 1) is None
    assert await gateway.previous_close(fixtures.TSMC) is None
    assert market_http.parse_previous_close({"candles": []}) is None
    bare = market_http.parse_instrument(
        {
            "instrument_id": "mock:perp:BTCUSD",
            "tick_size": "0.1",
            "lot_size": "0.001",
            "min_qty": "0.001",
        }
    )
    assert bare.continuous and bare.asset_class is enums.AssetClass.UNKNOWN
    row = {
        "instrument_id": fixtures.YUANTA_US20,
        "tick_size": "0.01",
        "lot_size": "1",
        "min_qty": "1",
    }
    for raw, parsed in (
        ("BOND", enums.AssetClass.BOND),
        ("PRIVATE_CREDIT", enums.AssetClass.UNKNOWN),
        (None, enums.AssetClass.UNKNOWN),
    ):
        meta = market_http.parse_instrument({**row, "asset_class": raw})
        assert meta.asset_class is parsed
    await gateway.close()


@respx.mock
async def test_http_gateway_returns_none_for_a_body_that_is_not_json(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A 200 reply that is not JSON yields None and a warning, not a raise."""
    base = "http://market"
    respx.get(f"{base}/v1/market/snapshot").mock(
        return_value=httpx.Response(200, text="<html>maintenance</html>")
    )
    gateway = market_http.HttpMarketDataGateway(base)
    with caplog.at_level("WARNING", logger=market_http.__name__):
        assert await gateway.quote("binance:perp:BTCUSDT") is None
    assert "/v1/market/snapshot returned a body that is not JSON" in (
        caplog.text
    )
    await gateway.close()
