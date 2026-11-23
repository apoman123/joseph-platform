"""Documented trading capabilities of the five crypto venues.

Values follow each venue's official API documentation (re-read 2026-11-23;
sources are cited in each adapter module) and are re-verified per account
at runtime (V5 §4): an adapter reports ``AVAILABLE`` only with stored
credentials, a healthy venue and a passing permission probe, and always
negotiates one-way position mode.
"""

from __future__ import annotations

import asyncio
import dataclasses
import time
from collections.abc import Awaitable
from collections.abc import Callable
from collections.abc import Collection
from collections.abc import Mapping
from typing import Any

import httpx

from joseph_execution.adapters.venues import base
from joseph_execution.adapters.venues import binance
from joseph_execution.adapters.venues import bitget
from joseph_execution.adapters.venues import credentials as creds_lib
from joseph_execution.adapters.venues import directory as directory_lib
from joseph_execution.adapters.venues import hyperliquid
from joseph_execution.adapters.venues import lighter
from joseph_execution.adapters.venues import live
from joseph_execution.adapters.venues import mexc
from joseph_execution.adapters.venues import ws
from joseph_execution.domain import accounts
from joseph_execution.domain import enums
from joseph_execution.ports import services
from joseph_execution.ports import trading

_T = enums.OrderType
_F = enums.TimeInForce
_M = enums.MarginMode

DOCUMENTED: dict[str, accounts.TradingCapabilities] = {
    "binance": accounts.TradingCapabilities(
        venue="binance",
        status=enums.TradingStatus.TRADING_UNAVAILABLE,
        order_types=(_T.MARKET, _T.LIMIT, _T.STOP_MARKET, _T.STOP_LIMIT),
        time_in_force=(_F.GTC, _F.IOC, _F.FOK, _F.POST_ONLY),
        native_reduce_only=True,
        native_stop_loss=True,
        native_take_profit=True,
        native_oco=False,
        amend=True,
        batch=True,
        position_modes=("ONE_WAY", "HEDGE"),
        margin_modes=(_M.ISOLATED, _M.CROSS),
        client_order_id=True,
        query_by_client_id=True,
        notes=(
            "USDⓈ-M /fapi/v1/order with newClientOrderId; stop orders via "
            "/fapi/v1/algoOrder (clientAlgoId) since 2025-12-09; only LIMIT "
            "orders can be amended; user data on the /private stream.",
        ),
    ),
    "hyperliquid": accounts.TradingCapabilities(
        venue="hyperliquid",
        status=enums.TradingStatus.TRADING_UNAVAILABLE,
        order_types=(_T.MARKET, _T.LIMIT, _T.STOP_MARKET, _T.STOP_LIMIT),
        time_in_force=(_F.GTC, _F.IOC, _F.POST_ONLY),
        native_reduce_only=True,
        native_stop_loss=True,
        native_take_profit=True,
        native_oco=False,
        amend=True,
        batch=True,
        position_modes=("ONE_WAY",),
        margin_modes=(_M.ISOLATED, _M.CROSS),
        client_order_id=True,
        query_by_client_id=True,
        notes=(
            "POST /exchange signed by a user-approved API (agent) wallet; "
            "cloid for idempotency; market orders are aggressive IOC limits.",
        ),
    ),
    "lighter": accounts.TradingCapabilities(
        venue="lighter",
        status=enums.TradingStatus.TRADING_UNAVAILABLE,
        order_types=(_T.MARKET, _T.LIMIT, _T.STOP_MARKET, _T.STOP_LIMIT),
        time_in_force=(_F.GTC, _F.IOC, _F.POST_ONLY),
        native_reduce_only=True,
        native_stop_loss=True,
        native_take_profit=True,
        native_oco=False,
        amend=True,
        batch=False,
        position_modes=("ONE_WAY",),
        margin_modes=(_M.CROSS,),
        client_order_id=True,
        query_by_client_id=True,
        notes=(
            "Official lighter-sdk SignerClient with API key index and "
            "nonce; client_order_index (uint48) for cancel, modify and "
            "accountOrders lookups; orders are accepted asynchronously.",
        ),
    ),
    "bitget": accounts.TradingCapabilities(
        venue="bitget",
        status=enums.TradingStatus.TRADING_UNAVAILABLE,
        order_types=(_T.MARKET, _T.LIMIT, _T.STOP_MARKET, _T.STOP_LIMIT),
        time_in_force=(_F.GTC, _F.IOC, _F.FOK, _F.POST_ONLY),
        native_reduce_only=True,
        native_stop_loss=True,
        native_take_profit=True,
        native_oco=False,
        amend=True,
        batch=True,
        position_modes=("ONE_WAY", "HEDGE"),
        margin_modes=(_M.ISOLATED, _M.CROSS),
        client_order_id=True,
        query_by_client_id=True,
        notes=(
            "Unified Trading Account (API v3) place-order with clientOid; "
            "Classic accounts must upgrade to UTA; stop orders are trigger "
            "strategy orders.",
        ),
    ),
    "mexc": accounts.TradingCapabilities(
        venue="mexc",
        status=enums.TradingStatus.TRADING_UNAVAILABLE,
        order_types=(_T.MARKET, _T.LIMIT, _T.STOP_MARKET, _T.STOP_LIMIT),
        time_in_force=(_F.GTC, _F.IOC, _F.FOK, _F.POST_ONLY),
        native_reduce_only=True,
        native_stop_loss=True,
        native_take_profit=True,
        native_oco=False,
        amend=False,
        batch=True,
        position_modes=("ONE_WAY", "HEDGE"),
        margin_modes=(_M.ISOLATED, _M.CROSS),
        client_order_id=True,
        query_by_client_id=True,
        notes=(
            "Futures /api/v1/private/order/create with externalOid. Order "
            "API eligibility needs KYC and the 'Order Placing' key "
            "permission (reopened 2026-03-31) and varies by region; no "
            "sandbox.",
        ),
    ),
}


# ----------------------------------------------------------------------
# Live adapter factory (credentialed venues).
# ----------------------------------------------------------------------

_ADAPTERS: dict[str, type[live.LiveVenueAdapter[Any]]] = {
    "binance": binance.BinanceFuturesAdapter,
    "bitget": bitget.BitgetFuturesAdapter,
    "hyperliquid": hyperliquid.HyperliquidAdapter,
    "lighter": lighter.LighterAdapter,
    "mexc": mexc.MexcFuturesAdapter,
}


def build_live_adapters(  # noqa: PLR0917
    credentials: creds_lib.CredentialSource | None = None,
    clock: services.Clock | None = None,
    options: live.VenueOptions | None = None,
    sandbox_venues: Collection[str] = (),
    directory: directory_lib.OrderDirectory | None = None,
    urls: Mapping[str, Mapping[str, str]] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    connect: ws.ConnectFn = ws.default_connect,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> dict[str, trading.TradingAdapter]:
    """Creates one adapter per crypto venue.

    Without a credential source every venue gets a ``FailClosedVenueAdapter``
    (nothing can be signed). With one, each venue gets its live adapter,
    which still refuses accounts that have no stored credentials.

    Args:
        credentials: Credential source (the vault in production).
        clock: Wall clock (required with ``credentials``).
        options: Shared options (global LIVE gate, timeouts, slippage).
        sandbox_venues: Venues that default to their test environment.
        directory: OMS order lookups.
        urls: Per-venue URL overrides (adapter keyword arguments such as
            ``rest_url`` or ``testnet_rest_url``).
        transport: HTTP transport override (tests).
        connect: WebSocket connector override (tests).
        monotonic: Monotonic clock override (tests).
        sleep: Sleep override (tests).

    Returns:
        Adapters keyed by venue code.

    Raises:
        ValueError: ``credentials`` without ``clock``.
    """
    if credentials is None:
        return {
            venue: base.FailClosedVenueAdapter(caps)
            for venue, caps in DOCUMENTED.items()
        }
    if clock is None:
        raise ValueError("live adapters need a clock")
    shared = options or live.VenueOptions()
    adapters: dict[str, trading.TradingAdapter] = {}
    for venue, adapter_type in _ADAPTERS.items():
        venue_options = dataclasses.replace(
            shared, sandbox=venue in sandbox_venues
        )
        adapters[venue] = adapter_type(
            DOCUMENTED[venue],
            credentials,
            clock,
            venue_options,
            directory=directory,
            transport=transport,
            connect=connect,
            monotonic=monotonic,
            sleep=sleep,
            urls=(urls or {}).get(venue),
        )
    return adapters
