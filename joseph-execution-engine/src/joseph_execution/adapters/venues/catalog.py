"""Documented trading capabilities of the five first-batch venues.

Values follow each venue's official API documentation as understood at
build time and must be re-verified per account at runtime (V5 §4). They
describe what an implemented adapter could negotiate; in this build every
adapter is fail-closed (``TRADING_UNAVAILABLE``).
"""

from __future__ import annotations

from joseph_execution.adapters.venues import base
from joseph_execution.domain import accounts
from joseph_execution.domain import enums

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
            "USDⓈ-M /fapi/v1/order with newClientOrderId; reconcile via "
            "origClientOrderId and user data streams.",
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
            "POST /exchange signed by a user-approved agent wallet; cloid "
            "for idempotency; market orders are aggressive IOC limits.",
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
        query_by_client_id=False,
        notes=(
            "Official lighter-python SignerClient with API key index and "
            "nonce; client order index lookup must be verified.",
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
            "UTA futures place-order with clientOid; posSide differs "
            "between one-way and hedge mode; API versions must be "
            "pinned.",
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
            "Futures /api/v1/private/order/create with externalOid. "
            "Automated-trading eligibility and regional restrictions "
            "must be verified for every account.",
        ),
    ),
}


def build_live_adapters() -> dict[str, base.FailClosedVenueAdapter]:
    """Creates one fail-closed adapter per first-batch venue.

    Returns:
        Adapters keyed by venue code.
    """
    return {
        venue: base.FailClosedVenueAdapter(caps)
        for venue, caps in DOCUMENTED.items()
    }
