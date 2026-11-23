"""Documented trading capabilities of the securities brokers.

Values follow each broker's official API documentation (verified for
``ib_async`` 2.1 / TWS API 10.x and Shioaji 1.7) and describe cash
accounts: no leverage, no margin modes, long-only unless short selling is
explicitly configured for an IBKR margin account.
"""

from __future__ import annotations

from joseph_execution.domain import accounts
from joseph_execution.domain import enums

_T = enums.OrderType
_F = enums.TimeInForce

CATHAY_AWAITING_SDK = (
    "Awaiting the Cathay API SDK/spec from the owner's Cathay "
    "representative: Cathay Securities publishes no developer API, so this "
    "broker is fail-closed and sends nothing."
)


def ibkr(
    *, short_selling: bool = False, simulation: bool = True
) -> accounts.TradingCapabilities:
    """Returns Interactive Brokers capabilities (US stocks and ETFs).

    Args:
        short_selling: The IBKR account is a margin account allowed to
            short (cash accounts cannot).
        simulation: Connected to the IBKR paper account.

    Returns:
        Capabilities with status AVAILABLE (adapters downgrade it).
    """
    return accounts.TradingCapabilities(
        venue=enums.Broker.IBKR.value,
        status=enums.TradingStatus.AVAILABLE,
        order_types=(_T.MARKET, _T.LIMIT, _T.STOP_MARKET, _T.STOP_LIMIT),
        time_in_force=(_F.DAY, _F.GTC, _F.IOC),
        native_reduce_only=False,
        native_stop_loss=True,
        native_take_profit=True,
        native_oco=False,
        amend=True,
        batch=False,
        position_modes=("ONE_WAY",),
        margin_modes=(),
        client_order_id=True,
        query_by_client_id=True,
        notes=(
            "Interactive Brokers through IB Gateway/TWS (ib_async): SMART "
            "routing with the listing exchange as primaryExchange; client "
            "order id in orderRef; stop orders are held by IBKR.",
            "Outside-RTH applies to limit and stop-limit orders; IBKR "
            "queues DAY market orders placed while closed for the open.",
        ),
        tradable_venues=(
            enums.Venue.NASDAQ.value,
            enums.Venue.NYSE.value,
            enums.Venue.CBOE.value,
        ),
        cash_account=not short_selling,
        short_selling=short_selling,
        outside_rth=True,
        odd_lot=False,
        queue_market_when_closed=True,
        simulation=simulation,
        currencies=("USD",),
    )


def sinopac(*, simulation: bool = True) -> accounts.TradingCapabilities:
    """Returns SinoPac Shioaji capabilities (TWSE and TPEx listings).

    Args:
        simulation: Shioaji simulation mode.

    Returns:
        Capabilities with status AVAILABLE (adapters downgrade it).
    """
    return accounts.TradingCapabilities(
        venue=enums.Broker.SINOPAC.value,
        status=enums.TradingStatus.AVAILABLE,
        order_types=(_T.MARKET, _T.LIMIT),
        time_in_force=(_F.DAY, _F.IOC, _F.FOK),
        native_reduce_only=False,
        native_stop_loss=False,
        native_take_profit=False,
        native_oco=False,
        amend=True,
        batch=False,
        position_modes=("ONE_WAY",),
        margin_modes=(),
        client_order_id=False,
        query_by_client_id=True,
        notes=(
            "SinoPac Shioaji: stock orders with price_type LMT/MKT, "
            "order_type ROD (DAY)/IOC/FOK, order_lot Common (board lots of "
            "1000 shares) or IntradayOdd (1-999 shares, limit ROD only), "
            "order_cond Cash.",
            "Shioaji keeps no client order id; Joseph tags orders with a "
            "6-character custom_field and tracks the trade id. Stop orders "
            "and brackets are not available.",
            "Deal reports carry no fees: commission (0.1425% maximum) and "
            "transaction tax (0.3% stocks, 0.1% ETFs) are estimated.",
            "TWSE codes resolve in Shioaji's Stocks.TSE contracts and TPEx "
            "codes in Stocks.OTC; emerging-board (OES) stocks are not "
            "routed.",
        ),
        tradable_venues=(enums.Venue.TWSE.value, enums.Venue.TPEX.value),
        cash_account=True,
        short_selling=False,
        outside_rth=False,
        odd_lot=True,
        queue_market_when_closed=False,
        simulation=simulation,
        currencies=("TWD",),
    )


def cathay() -> accounts.TradingCapabilities:
    """Returns the Cathay Securities entry (TWSE, TPEx; fail-closed).

    Returns:
        Capabilities with status TRADING_UNAVAILABLE.
    """
    return accounts.TradingCapabilities(
        venue=enums.Broker.CATHAY.value,
        status=enums.TradingStatus.TRADING_UNAVAILABLE,
        order_types=(),
        time_in_force=(),
        native_reduce_only=False,
        native_stop_loss=False,
        native_take_profit=False,
        native_oco=False,
        amend=False,
        batch=False,
        position_modes=("ONE_WAY",),
        margin_modes=(),
        client_order_id=False,
        query_by_client_id=False,
        notes=(),
        tradable_venues=(enums.Venue.TWSE.value, enums.Venue.TPEX.value),
        cash_account=True,
        short_selling=False,
        outside_rth=False,
        odd_lot=False,
        queue_market_when_closed=False,
        simulation=False,
        currencies=("TWD",),
    )
