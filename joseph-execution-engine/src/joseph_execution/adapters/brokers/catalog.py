# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Builds the broker adapters with the deployment's gates applied.

A broker is open only when the global LIVE flag is on *and* the broker is
explicitly enabled and configured; otherwise its adapter is gated and
refuses every call without creating an SDK client (no network). Cathay is
always fail-closed (no API yet). The remaining LIVE gates (account LIVE
mode, preview tokens, AUTO_LIVE consent with step-up, Risk Gateway, kill
switch) still apply to every order in the OMS (order management system).
"""

from __future__ import annotations

from collections.abc import Callable

from joseph_execution.adapters.brokers import base
from joseph_execution.adapters.brokers import cathay
from joseph_execution.adapters.brokers import ibkr
from joseph_execution.adapters.brokers import sinopac
from joseph_execution.domain import enums
from joseph_execution.ports import market as market_port
from joseph_execution.ports import services

LIVE_OFF = (
    "live trading is disabled on this deployment "
    "(JOSEPH_LIVE_TRADING_ENABLED=false)"
)


def gate_reason(
    live_trading_enabled: bool, enabled: bool, variable: str
) -> str | None:
    """Explains why a broker is gated, if it is.

    The global LIVE flag is checked first, so it is the reason reported
    when both gates are closed.

    Args:
        live_trading_enabled: Global LIVE flag.
        enabled: The broker's own enable flag (``variable``).
        variable: Environment variable that enables the broker (named in
            the reason).

    Returns:
        Reason, or ``None`` when the broker may connect.
    """
    if not live_trading_enabled:
        return LIVE_OFF
    if not enabled:
        return f"the broker is not enabled ({variable}=false)"
    return None


def build(
    *,
    live_trading_enabled: bool,
    ibkr_config: ibkr.IbkrConfig,
    ibkr_enabled: bool,
    sinopac_config: sinopac.SinopacConfig | None,
    sinopac_enabled: bool,
    market_data: market_port.MarketDataGateway,
    clock: services.Clock,
    account_lookup: base.AccountLookup,
    ib_factory: Callable[[], ibkr.IbFactory] = ibkr.IbAsyncFactory,
    sj_factory: Callable[[], sinopac.SjFactory] = sinopac.ShioajiFactory,
) -> dict[str, base.BrokerAdapter]:
    """Creates one adapter per broker.

    Args:
        live_trading_enabled: Global LIVE flag.
        ibkr_config: IBKR connection settings.
        ibkr_enabled: IBKR explicitly enabled (``JOSEPH_IBKR_ENABLED``).
        sinopac_config: Shioaji settings, ``None`` when not configured (the
            SinoPac adapter then stays gated).
        sinopac_enabled: SinoPac explicitly enabled
            (``JOSEPH_SINOPAC_ENABLED``).
        market_data: Market gateway (instrument metadata).
        clock: Time source.
        account_lookup: Joseph account id -> broker account number.
        ib_factory: Creates the ``ib_async`` factory on first connect
            (tests inject fakes).
        sj_factory: Creates the Shioaji factory on first login (tests
            inject fakes).

    Returns:
        Adapters keyed by broker code (``ibkr``, ``sinopac``, ``cathay``).
    """
    return {
        enums.Broker.IBKR.value: ibkr.IbkrAdapter(
            ibkr_config,
            ib_factory,
            market_data,
            clock,
            account_lookup,
            gate_reason(
                live_trading_enabled, ibkr_enabled, "JOSEPH_IBKR_ENABLED"
            ),
        ),
        enums.Broker.SINOPAC.value: sinopac.SinopacAdapter(
            sinopac_config,
            sj_factory,
            clock,
            account_lookup,
            gate_reason(
                live_trading_enabled, sinopac_enabled, "JOSEPH_SINOPAC_ENABLED"
            ),
            market_data=market_data,
        ),
        enums.Broker.CATHAY.value: cathay.build(),
    }
