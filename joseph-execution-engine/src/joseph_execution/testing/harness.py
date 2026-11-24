# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Builds a deterministic in-memory execution engine for tests and demos.

Uses SQLite in memory, a manual clock, the static DEMO market gateway and
no background loops, so tests drive dispatch, ticks and reconciliation
explicitly.
"""

from __future__ import annotations

import dataclasses
import decimal
from collections.abc import Mapping

from joseph_execution import bootstrap
from joseph_execution.adapters import market_static
from joseph_execution.adapters import step_up
from joseph_execution.domain import identity
from joseph_execution.platform import clock as clock_lib
from joseph_execution.platform import config

TOKEN_A = "token-tenant-a"  # noqa: S105
TOKEN_B = "token-tenant-b"  # noqa: S105
SERVICE_TOKEN = "service-token-test"  # noqa: S105
TOTP_SECRET = "JBSWY3DPEHPK3PXP"  # noqa: S105
TENANT_A = identity.Principal("tenant-a", "user-a")
TENANT_B = identity.Principal("tenant-b", "user-b")
PAPER_A = "acct-paper-tenant-a"
PAPER_B = "acct-paper-tenant-b"
BTC = "mock:perp:BTCUSD"
ETH = "mock:perp:ETHUSD"


def settings(**overrides: object) -> config.Settings:
    """Returns test settings.

    Args:
        **overrides: Field overrides.

    Returns:
        Settings.
    """
    base = config.Settings(
        environment="test",
        database_url="sqlite://",
        market_gateway="static",
        dev_tokens={
            TOKEN_A: ("tenant-a", "user-a"),
            TOKEN_B: ("tenant-b", "user-b"),
        },
        service_token=SERVICE_TOKEN,
        vault_master_key=b"k" * 32,
        preview_secret=b"preview-secret-for-tests",
        step_up_totp_secret=TOTP_SECRET,
        background_tasks=False,
        stream_heartbeat_s=0.2,
    )
    return dataclasses.replace(base, **overrides)  # type: ignore[arg-type]


@dataclasses.dataclass(slots=True)
class Harness:
    """Wired engine plus handles to the simulated pieces.

    Attributes:
        container: Container.
        clock: Manual clock.
        market: Static market gateway.
    """

    container: bootstrap.Container
    clock: clock_lib.ManualClock
    market: market_static.StaticMarketDataGateway

    @property
    def oms(self) -> object:
        """Returns the OMS service."""
        return self.container.services.oms

    def totp(self) -> str:
        """Returns a valid step-up code for the harness clock.

        Returns:
            Six-digit code.
        """
        return step_up.totp(TOTP_SECRET, self.clock.now_ms())

    async def pump(self) -> int:
        """Ticks simulators and applies their events.

        Returns:
            Events applied.
        """
        return await bootstrap.pump_simulators(self.container)

    def set_mark(self, instrument_id: str, mark: str) -> None:
        """Moves a mock mark price.

        Args:
            instrument_id: Instrument.
            mark: New mark as a decimal string.
        """
        self.market.set_mark(instrument_id, decimal.Decimal(mark))


def build(**overrides: object) -> Harness:
    """Builds a harness.

    Args:
        **overrides: Settings overrides.

    Returns:
        Harness.
    """
    clock = clock_lib.ManualClock()
    market = market_static.StaticMarketDataGateway(clock)
    container = bootstrap.build_container(settings(**overrides), clock, market)
    for principal in (TENANT_A, TENANT_B):
        container.services.accounts.ensure_paper_account(principal.tenant_id)
    return Harness(container, clock, market)


def headers(
    token: str = TOKEN_A, idempotency_key: str | None = None
) -> Mapping[str, str]:
    """Builds request headers.

    Args:
        token: Bearer token.
        idempotency_key: Optional Idempotency-Key.

    Returns:
        Headers.
    """
    result = {"Authorization": f"Bearer {token}"}
    if idempotency_key is not None:
        result["Idempotency-Key"] = idempotency_key
    return result


def intent_body(**overrides: object) -> dict[str, object]:
    """Builds a contract ``OrderIntentRequest`` for tenant A's paper account.

    Args:
        **overrides: Field overrides.

    Returns:
        Body.
    """
    body: dict[str, object] = {
        "account_id": PAPER_A,
        "instrument_id": BTC,
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
