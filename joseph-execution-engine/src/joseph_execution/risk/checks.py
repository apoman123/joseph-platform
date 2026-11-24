# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Individual pre-trade risk checks and the default pipeline.

Each check is a pure function of a ``RiskContext`` returning one
``RiskCheckResult``. A missing input never yields PASS: the check returns
UNKNOWN (which blocks), FAIL, or SKIP where another check already covers
the gap (missing instrument metadata). Many checks relax for
risk-reducing orders (``RiskContext.risk_reducing``) so that positions can
always be closed: they return WARN or SKIP instead of failing.
"""

from __future__ import annotations

import dataclasses
import datetime
import decimal
from collections.abc import Callable
from typing import TypeAlias

from joseph_execution.domain import decimals
from joseph_execution.domain import enums
from joseph_execution.domain import equities
from joseph_execution.domain import risk
from joseph_execution.risk import context

Domain = enums.RiskDomain
Outcome = enums.CheckOutcome
Check: TypeAlias = Callable[[context.RiskContext], risk.RiskCheckResult]

# Feeds fresh enough for LIVE orders; any other feed state only WARNs for
# PAPER/SHADOW orders.
_FRESH_FEEDS = (enums.FeedState.LIVE,)


def _text(value: object) -> str | None:
    """Formats a value for display.

    Args:
        value: Value.

    Returns:
        Text, with decimals in plain notation, or ``None`` for ``None``.
    """
    if value is None:
        return None
    if isinstance(value, decimal.Decimal):
        return decimals.fmt(value)
    return str(value)


def _result(  # noqa: PLR0917 - every check passes the result fields positionally, in RiskCheckResult order.
    check: str,
    domain: enums.RiskDomain,
    outcome: enums.CheckOutcome,
    message: str,
    code: str | None = None,
    observed: object = None,
    limit: object = None,
) -> risk.RiskCheckResult:
    """Builds a check result with stringified values.

    Args:
        check: Check name.
        domain: Domain.
        outcome: Outcome.
        message: Explanation shown to the user.
        code: Error code reported when the check does not pass.
        observed: Observed value.
        limit: Limit value.

    Returns:
        Result.
    """
    return risk.RiskCheckResult(
        check=check,
        domain=domain,
        outcome=outcome,
        message=message,
        code=code,
        observed=_text(observed),
        limit=_text(limit),
    )


def mode_allowed(ctx: context.RiskContext) -> risk.RiskCheckResult:  # noqa: PLR0911 - one return per LIVE gate, checked in a fixed order.
    """Checks the order mode against the account, the LIVE gates and consent.

    The gates, in order: the account is known; its modes include the
    order's mode; PAPER and SHADOW orders then pass. A LIVE order also
    needs ``live_trading_enabled`` and adapter capabilities with status
    AVAILABLE. A STRATEGY order then needs an AUTO_LIVE deployment that is
    ENABLED and has consent valid for the account's venue at ``now_ms``. A
    manual order (ticket, chart, quick trade) needs an ARMED account LIVE
    arming; without one, a risk-reducing order is still allowed with a
    warning, as under a kill switch. EMERGENCY orders (kill switch
    flattening) are not manual and need no arming.

    Args:
        ctx: Risk context.

    Returns:
        UNKNOWN without an account; FAIL at the first unmet gate (WARN for
        an unarmed risk-reducing manual order); else PASS.
    """
    name = "mode.allowed"
    req = ctx.request
    account = ctx.account
    if account is None:
        return _result(
            name,
            Domain.MODE,
            Outcome.UNKNOWN,
            "Account is unknown.",
            "NOT_FOUND",
        )
    if req.mode not in account.modes:
        return _result(
            name,
            Domain.MODE,
            Outcome.FAIL,
            f"{req.mode.value} orders are not enabled for this account.",
            "TRADE_MODE_DISABLED",
            req.mode.value,
            ",".join(m.value for m in account.modes),
        )
    if req.mode is not enums.OrderMode.LIVE:
        return _result(
            name,
            Domain.MODE,
            Outcome.PASS,
            f"{req.mode.value} order; no real venue order is sent.",
        )
    if not ctx.live_trading_enabled:
        return _result(
            name,
            Domain.MODE,
            Outcome.FAIL,
            "Live trading is disabled on this deployment.",
            "LIVE_TRADING_DISABLED",
        )
    caps = ctx.capabilities
    if caps is None or caps.status is not enums.TradingStatus.AVAILABLE:
        return _result(
            name,
            Domain.MODE,
            Outcome.FAIL,
            "The venue adapter cannot trade for this account.",
            "TRADE_MODE_DISABLED",
            None if caps is None else caps.status.value,
            "AVAILABLE",
        )
    if req.source is enums.IntentSource.STRATEGY:
        dep = ctx.deployment
        if (
            dep is None
            or dep.mode is not enums.DeploymentMode.AUTO_LIVE
            or dep.state is not enums.DeploymentState.ENABLED
            or not dep.consent_valid(account.venue, ctx.now_ms)
        ):
            return _result(
                name,
                Domain.MODE,
                Outcome.FAIL,
                "AUTO_LIVE needs an enabled deployment with valid consent.",
                "TRADE_MODE_DISABLED",
            )
    if req.source.is_manual:
        arming = ctx.live_arming
        state = (
            "NOT_ARMED" if arming is None else arming.state(account, ctx.now_ms)
        )
        if state != "ARMED":
            if ctx.risk_reducing:
                return _result(
                    name,
                    Domain.MODE,
                    Outcome.WARN,
                    "Live trading is not armed; risk-reducing order allowed.",
                    observed=state,
                )
            return _result(
                name,
                Domain.MODE,
                Outcome.FAIL,
                "Live trading is not armed for this account; arm it with "
                "a step-up code first.",
                "LIVE_NOT_ARMED",
                state,
                "ARMED",
            )
    return _result(
        name,
        Domain.MODE,
        Outcome.PASS,
        "Live trading is permitted for this scope.",
    )


def identity_scope(ctx: context.RiskContext) -> risk.RiskCheckResult:
    """Checks that the caller's tenant owns a usable account.

    Args:
        ctx: Risk context.

    Returns:
        FAIL when the account is missing, belongs to another tenant, or is
        DISCONNECTED or PENDING_VERIFICATION; else PASS.
    """
    name = "identity.account_scope"
    account = ctx.account
    if account is None:
        return _result(
            name,
            Domain.IDENTITY,
            Outcome.FAIL,
            "Account not found.",
            "NOT_FOUND",
        )
    if account.tenant_id != ctx.principal.tenant_id:
        return _result(
            name,
            Domain.IDENTITY,
            Outcome.FAIL,
            "Account belongs to another tenant.",
            "FORBIDDEN",
        )
    if account.status in (
        enums.AccountStatus.DISCONNECTED,
        enums.AccountStatus.PENDING_VERIFICATION,
    ):
        return _result(
            name,
            Domain.IDENTITY,
            Outcome.FAIL,
            f"Account is {account.status.value}.",
            "AUTH_SCOPE_MISSING",
            account.status.value,
        )
    return _result(name, Domain.IDENTITY, Outcome.PASS, "Account in scope.")


def account_margin(ctx: context.RiskContext) -> risk.RiskCheckResult:
    """Checks free margin for new exposure.

    Args:
        ctx: Risk context.

    Returns:
        SKIP for securities (see ``account_cash``) and risk-reducing orders;
        UNKNOWN when free or required margin is unknown; FAIL when the order
        needs more margin than is free; else PASS.
    """
    name = "account.margin"
    if ctx.security:
        return _result(
            name,
            Domain.ACCOUNT,
            Outcome.SKIP,
            "Securities are bought from cash (see account.cash).",
        )
    if ctx.risk_reducing:
        return _result(
            name,
            Domain.ACCOUNT,
            Outcome.SKIP,
            "Risk-reducing order needs no new margin.",
        )
    if ctx.available_margin is None or ctx.margin_required is None:
        return _result(
            name,
            Domain.ACCOUNT,
            Outcome.UNKNOWN,
            "Free margin or required margin is unknown.",
            "INSUFFICIENT_MARGIN",
        )
    if ctx.margin_required > ctx.available_margin:
        return _result(
            name,
            Domain.ACCOUNT,
            Outcome.FAIL,
            "Not enough free margin.",
            "INSUFFICIENT_MARGIN",
            ctx.margin_required,
            ctx.available_margin,
        )
    return _result(
        name,
        Domain.ACCOUNT,
        Outcome.PASS,
        "Margin available.",
        observed=ctx.margin_required,
        limit=ctx.available_margin,
    )


def account_state_known(ctx: context.RiskContext) -> risk.RiskCheckResult:
    """Blocks new exposure while orders or positions are unreconciled.

    Args:
        ctx: Risk context.

    Returns:
        PASS when nothing is being reconciled; WARN for a risk-reducing
        order while reconciling; else FAIL.
    """
    name = "account.state_known"
    if not ctx.account_state_unknown:
        return _result(
            name,
            Domain.ACCOUNT,
            Outcome.PASS,
            "No unreconciled orders or positions.",
        )
    if ctx.risk_reducing:
        return _result(
            name,
            Domain.ACCOUNT,
            Outcome.WARN,
            "Account is reconciling; reduce-only is allowed.",
        )
    return _result(
        name,
        Domain.ACCOUNT,
        Outcome.FAIL,
        "Unknown orders or positions are being reconciled.",
        "POSITION_UNKNOWN",
    )


def market_status(ctx: context.RiskContext) -> risk.RiskCheckResult:
    """Checks that the instrument is trading.

    Args:
        ctx: Risk context.

    Returns:
        UNKNOWN without instrument metadata; FAIL unless the instrument
        status is TRADING; else PASS.
    """
    name = "market.instrument_status"
    inst = ctx.instrument
    if inst is None:
        return _result(
            name,
            Domain.MARKET_STATUS,
            Outcome.UNKNOWN,
            "Instrument metadata unavailable.",
            "VENUE_UNAVAILABLE",
        )
    if inst.status is not enums.InstrumentStatus.TRADING:
        return _result(
            name,
            Domain.MARKET_STATUS,
            Outcome.FAIL,
            f"Instrument is {inst.status.value}.",
            "VENUE_UNAVAILABLE",
            inst.status.value,
            "TRADING",
        )
    return _result(
        name, Domain.MARKET_STATUS, Outcome.PASS, "Instrument is trading."
    )


def data_quote_fresh(ctx: context.RiskContext) -> risk.RiskCheckResult:
    """Checks quote freshness and provenance.

    LIVE orders require LIVE data. PAPER/SHADOW orders accept polled, replay
    or demo data with a warning. Risk-reducing orders never fail it.

    Args:
        ctx: Risk context.

    Returns:
        UNKNOWN without a reference price; FAIL when the quote is older than
        ``max_quote_age_ms`` or its feed is STALE, UNAVAILABLE or
        CONNECTING; PASS for a fresh LIVE feed; for other feeds FAIL for a
        LIVE order, else WARN. Each UNKNOWN or FAIL is a WARN instead for a
        risk-reducing order.
    """
    name = "data.quote_fresh"
    quote = ctx.quote
    reducing = ctx.risk_reducing
    if quote is None or quote.reference_price is None:
        outcome = Outcome.WARN if reducing else Outcome.UNKNOWN
        return _result(
            name,
            Domain.DATA_QUALITY,
            outcome,
            "No usable mark/last price.",
            "MARKET_STALE",
        )
    age = quote.age_ms(ctx.now_ms)
    if age > ctx.limits.max_quote_age_ms or quote.feed_state in (
        enums.FeedState.STALE,
        enums.FeedState.UNAVAILABLE,
        enums.FeedState.CONNECTING,
    ):
        outcome = Outcome.WARN if reducing else Outcome.FAIL
        return _result(
            name,
            Domain.DATA_QUALITY,
            outcome,
            "Market data is stale.",
            "MARKET_STALE",
            f"{age}ms {quote.feed_state.value}",
            f"{ctx.limits.max_quote_age_ms}ms",
        )
    if quote.feed_state in _FRESH_FEEDS:
        return _result(
            name,
            Domain.DATA_QUALITY,
            Outcome.PASS,
            "Live market data.",
            observed=f"{age}ms",
        )
    if ctx.request.mode is enums.OrderMode.LIVE and not reducing:
        return _result(
            name,
            Domain.DATA_QUALITY,
            Outcome.FAIL,
            "Live orders need live market data.",
            "MARKET_STALE",
            quote.feed_state.value,
            "LIVE",
        )
    return _result(
        name,
        Domain.DATA_QUALITY,
        Outcome.WARN,
        f"Using {quote.feed_state.value} market data.",
        observed=quote.feed_state.value,
    )


def size_quantity(ctx: context.RiskContext) -> risk.RiskCheckResult:
    """Checks lot size, minimum and maximum quantity.

    Args:
        ctx: Risk context.

    Returns:
        UNKNOWN without instrument metadata; FAIL when the base quantity is
        not a multiple of the lot size, is below ``min_qty`` or above
        ``max_qty``; else PASS.
    """
    name = "size.quantity"
    inst = ctx.instrument
    qty = ctx.request.quantity_base
    if inst is None:
        return _result(
            name,
            Domain.SIZE,
            Outcome.UNKNOWN,
            "Lot size unknown.",
            "INVALID_LOT_SIZE",
        )
    if not decimals.is_multiple(qty, inst.lot_size):
        return _result(
            name,
            Domain.SIZE,
            Outcome.FAIL,
            "Quantity is not a multiple of the lot size.",
            "INVALID_LOT_SIZE",
            qty,
            inst.lot_size,
        )
    if qty < inst.min_qty:
        return _result(
            name,
            Domain.SIZE,
            Outcome.FAIL,
            "Quantity is below the minimum.",
            "INVALID_LOT_SIZE",
            qty,
            inst.min_qty,
        )
    if inst.max_qty is not None and qty > inst.max_qty:
        return _result(
            name,
            Domain.SIZE,
            Outcome.FAIL,
            "Quantity is above the maximum.",
            "INVALID_LOT_SIZE",
            qty,
            inst.max_qty,
        )
    return _result(
        name, Domain.SIZE, Outcome.PASS, "Quantity is valid.", observed=qty
    )


def size_tick(ctx: context.RiskContext) -> risk.RiskCheckResult:
    """Checks that limit, trigger and bracket prices sit on the tick grid.

    Args:
        ctx: Risk context.

    Returns:
        SKIP when the order carries no price; UNKNOWN without instrument
        metadata; FAIL for the first price off the tick grid; else PASS.
    """
    name = "size.tick"
    inst = ctx.instrument
    req = ctx.request
    prices = [p for p in (req.limit_price, req.trigger_price) if p is not None]
    bracket = req.bracket
    if bracket.stop_loss is not None:
        prices.append(bracket.stop_loss.trigger)
    prices.extend(leg.trigger for leg in bracket.take_profit)
    if not prices:
        return _result(name, Domain.SIZE, Outcome.SKIP, "No price to check.")
    if inst is None:
        return _result(
            name,
            Domain.SIZE,
            Outcome.UNKNOWN,
            "Tick size unknown.",
            "INVALID_TICK_SIZE",
        )
    for price in prices:
        if not equities.on_tick(price, inst):
            return _result(
                name,
                Domain.SIZE,
                Outcome.FAIL,
                "Price is not a multiple of the tick size.",
                "INVALID_TICK_SIZE",
                price,
                inst.tick_for(price),
            )
    return _result(name, Domain.SIZE, Outcome.PASS, "Prices on tick grid.")


def size_min_notional(ctx: context.RiskContext) -> risk.RiskCheckResult:
    """Checks the venue minimum notional.

    Args:
        ctx: Risk context.

    Returns:
        SKIP when no minimum is published (or metadata is missing; the
        quantity check catches that); UNKNOWN when the notional is unknown;
        FAIL below the minimum unless risk-reducing; else PASS.
    """
    name = "size.min_notional"
    inst = ctx.instrument
    if inst is None or inst.min_notional is None:
        return _result(
            name, Domain.SIZE, Outcome.SKIP, "No minimum notional published."
        )
    if ctx.notional is None:
        return _result(
            name,
            Domain.SIZE,
            Outcome.UNKNOWN,
            "Notional unknown.",
            "MIN_NOTIONAL",
        )
    if ctx.notional < inst.min_notional and not ctx.risk_reducing:
        return _result(
            name,
            Domain.SIZE,
            Outcome.FAIL,
            "Order is below the minimum notional.",
            "MIN_NOTIONAL",
            ctx.notional,
            inst.min_notional,
        )
    return _result(
        name,
        Domain.SIZE,
        Outcome.PASS,
        "Notional valid.",
        observed=ctx.notional,
        limit=inst.min_notional,
    )


def size_leverage(ctx: context.RiskContext) -> risk.RiskCheckResult:
    """Checks leverage against venue, account and deployment caps.

    Args:
        ctx: Risk context.

    Returns:
        FAIL when the requested leverage exceeds the lowest cap among the
        account limit, the instrument maximum and the deployment limit;
        else PASS.
    """
    name = "size.leverage"
    caps = [ctx.limits.max_leverage]
    if ctx.instrument is not None and ctx.instrument.max_leverage is not None:
        caps.append(ctx.instrument.max_leverage)
    if ctx.deployment is not None:
        caps.append(ctx.deployment.limits.max_leverage)
    cap = min(caps)
    if ctx.request.leverage > cap:
        return _result(
            name,
            Domain.SIZE,
            Outcome.FAIL,
            "Leverage exceeds the allowed maximum.",
            "RISK_LIMIT",
            ctx.request.leverage,
            cap,
        )
    return _result(
        name,
        Domain.SIZE,
        Outcome.PASS,
        "Leverage within limits.",
        observed=ctx.request.leverage,
        limit=cap,
    )


def size_price_band(ctx: context.RiskContext) -> risk.RiskCheckResult:
    """Checks that the order price is within a band around the mark.

    The limit price is checked, or the trigger price when there is none.

    Args:
        ctx: Risk context.

    Returns:
        SKIP without a limit or trigger price; UNKNOWN without a reference
        price; FAIL when the price deviates from the reference price by
        more than ``max_price_deviation_pct``; else PASS.
    """
    name = "size.price_band"
    price = ctx.request.limit_price or ctx.request.trigger_price
    if price is None:
        return _result(name, Domain.SIZE, Outcome.SKIP, "Market order.")
    ref = None if ctx.quote is None else ctx.quote.reference_price
    if ref is None:
        return _result(
            name,
            Domain.SIZE,
            Outcome.UNKNOWN,
            "No reference price for the price band.",
            "RISK_LIMIT",
        )
    deviation = abs(price - ref) / ref * decimals.HUNDRED
    if deviation > ctx.limits.max_price_deviation_pct:
        return _result(
            name,
            Domain.SIZE,
            Outcome.FAIL,
            "Price is too far from the mark.",
            "RISK_LIMIT",
            deviation,
            ctx.limits.max_price_deviation_pct,
        )
    return _result(
        name,
        Domain.SIZE,
        Outcome.PASS,
        "Price within band.",
        observed=deviation,
    )


def size_order_type(ctx: context.RiskContext) -> risk.RiskCheckResult:
    """Checks the order type and time in force against capabilities.

    Args:
        ctx: Risk context.

    Returns:
        UNKNOWN without capabilities; FAIL for an unsupported order type or
        time in force, or for a reduce-only order the venue cannot mark
        reduce-only (except a sell on a long-only securities account);
        else PASS.
    """
    name = "size.order_type"
    caps = ctx.capabilities
    req = ctx.request
    if caps is None:
        return _result(
            name,
            Domain.SIZE,
            Outcome.UNKNOWN,
            "Capabilities unknown.",
            "UNSUPPORTED_ORDER_TYPE",
        )
    if req.order_type not in caps.order_types:
        return _result(
            name,
            Domain.SIZE,
            Outcome.FAIL,
            "The venue does not support this order type.",
            "UNSUPPORTED_ORDER_TYPE",
            req.order_type.value,
        )
    if req.time_in_force not in caps.time_in_force:
        return _result(
            name,
            Domain.SIZE,
            Outcome.FAIL,
            "The venue does not support this time in force.",
            "UNSUPPORTED_ORDER_TYPE",
            req.time_in_force.value,
        )
    # A long-only securities account cannot open a short: the broker
    # rejects uncovered sells, so a reduce-only sell stays reducing even
    # without a native reduce-only flag.
    long_only_sell = (
        ctx.security
        and not caps.short_selling
        and req.side is enums.OrderSide.SELL
    )
    if req.reduce_only and not caps.native_reduce_only and not long_only_sell:
        return _result(
            name,
            Domain.SIZE,
            Outcome.FAIL,
            "The venue has no native reduce-only.",
            "UNSUPPORTED_ORDER_TYPE",
        )
    return _result(name, Domain.SIZE, Outcome.PASS, "Order type supported.")


def size_reduce_only(ctx: context.RiskContext) -> risk.RiskCheckResult:
    """Ensures reduce-only orders cannot flip or open a position.

    Args:
        ctx: Risk context.

    Returns:
        SKIP for orders that are not reduce-only; PASS when the order is
        risk-reducing; else FAIL.
    """
    name = "size.reduce_only"
    req = ctx.request
    if not req.reduce_only:
        return _result(name, Domain.SIZE, Outcome.SKIP, "Opening order.")
    if ctx.risk_reducing:
        return _result(
            name,
            Domain.SIZE,
            Outcome.PASS,
            "Reduce-only order cannot exceed the position.",
        )
    return _result(
        name,
        Domain.SIZE,
        Outcome.FAIL,
        "Reduce-only order exceeds or opposes the position.",
        "VALIDATION_FAILED",
        req.quantity_base,
        None if ctx.position is None else ctx.position.quantity,
    )


def exposure_limits(ctx: context.RiskContext) -> risk.RiskCheckResult:  # noqa: PLR0911 - one return per exposure limit, checked in a fixed order.
    """Checks gross, position and open-position limits.

    Args:
        ctx: Risk context.

    Returns:
        SKIP for risk-reducing orders; UNKNOWN when the order or gross
        notional is unknown; FAIL, in this order, when the gross notional
        plus the order exceeds ``max_account_notional``, the position's
        notional plus the order exceeds ``max_position_notional``, the order
        exceeds the deployment's ``max_notional``, or a new position would
        exceed the lower of the account and deployment open-position
        limits; else PASS.
    """
    name = "exposure.notional"
    if ctx.risk_reducing:
        return _result(
            name, Domain.EXPOSURE, Outcome.SKIP, "Risk-reducing order."
        )
    if ctx.notional is None or ctx.gross_notional is None:
        return _result(
            name,
            Domain.EXPOSURE,
            Outcome.UNKNOWN,
            "Exposure cannot be computed.",
            "RISK_LIMIT",
        )
    gross = ctx.gross_notional + ctx.notional
    if gross > ctx.limits.max_account_notional:
        return _result(
            name,
            Domain.EXPOSURE,
            Outcome.FAIL,
            "Account notional limit exceeded.",
            "RISK_LIMIT",
            gross,
            ctx.limits.max_account_notional,
        )
    # Without a reference price the position counts as zero here; such an
    # order (not risk-reducing) is already UNKNOWN in data_quote_fresh.
    current = decimals.ZERO
    ref = None if ctx.quote is None else ctx.quote.reference_price
    if ctx.position is not None and ref is not None:
        current = ctx.position.quantity * ref
    if current + ctx.notional > ctx.limits.max_position_notional:
        return _result(
            name,
            Domain.EXPOSURE,
            Outcome.FAIL,
            "Position notional limit exceeded.",
            "RISK_LIMIT",
            current + ctx.notional,
            ctx.limits.max_position_notional,
        )
    opens_new = ctx.position is None or ctx.position.signed_qty == 0
    max_open = ctx.limits.max_open_positions
    if ctx.deployment is not None:
        max_open = min(max_open, ctx.deployment.limits.max_open_positions)
        if ctx.notional > ctx.deployment.limits.max_notional:
            return _result(
                name,
                Domain.EXPOSURE,
                Outcome.FAIL,
                "Deployment notional limit exceeded.",
                "RISK_LIMIT",
                ctx.notional,
                ctx.deployment.limits.max_notional,
            )
    if opens_new and ctx.open_position_count + 1 > max_open:
        return _result(
            name,
            Domain.EXPOSURE,
            Outcome.FAIL,
            "Too many open positions.",
            "RISK_LIMIT",
            ctx.open_position_count + 1,
            max_open,
        )
    return _result(
        name,
        Domain.EXPOSURE,
        Outcome.PASS,
        "Exposure within limits.",
        observed=gross,
        limit=ctx.limits.max_account_notional,
    )


def exposure_daily_loss(ctx: context.RiskContext) -> risk.RiskCheckResult:
    """Pauses new exposure after the daily loss limit.

    Args:
        ctx: Risk context.

    Returns:
        SKIP for risk-reducing orders; UNKNOWN when the daily PnL is
        unknown; FAIL once the realized loss since 00:00 UTC reaches the
        lower of the account and deployment limits; else PASS.
    """
    name = "exposure.daily_loss"
    if ctx.risk_reducing:
        return _result(
            name, Domain.EXPOSURE, Outcome.SKIP, "Risk-reducing order."
        )
    if ctx.daily_realized_pnl is None:
        return _result(
            name,
            Domain.EXPOSURE,
            Outcome.UNKNOWN,
            "Daily PnL unknown.",
            "RISK_LIMIT",
        )
    limit = ctx.limits.max_daily_loss
    if ctx.deployment is not None:
        limit = min(limit, ctx.deployment.limits.max_daily_loss)
    if -ctx.daily_realized_pnl >= limit:
        return _result(
            name,
            Domain.EXPOSURE,
            Outcome.FAIL,
            "Daily loss limit reached; new exposure paused.",
            "RISK_LIMIT",
            -ctx.daily_realized_pnl,
            limit,
        )
    return _result(
        name,
        Domain.EXPOSURE,
        Outcome.PASS,
        "Daily loss within limit.",
        observed=-ctx.daily_realized_pnl,
        limit=limit,
    )


def liquidity_spread(ctx: context.RiskContext) -> risk.RiskCheckResult:
    """Checks the bid/ask spread.

    Args:
        ctx: Risk context.

    Returns:
        UNKNOWN (WARN when risk-reducing) without a spread; FAIL when wider
        than ``max_spread_pct`` unless risk-reducing; else PASS.
    """
    name = "liquidity.spread"
    spread = None if ctx.quote is None else ctx.quote.spread_pct
    if spread is None:
        outcome = Outcome.WARN if ctx.risk_reducing else Outcome.UNKNOWN
        return _result(
            name, Domain.LIQUIDITY, outcome, "Spread unknown.", "MARKET_STALE"
        )
    if spread > ctx.limits.max_spread_pct and not ctx.risk_reducing:
        return _result(
            name,
            Domain.LIQUIDITY,
            Outcome.FAIL,
            "Spread is too wide.",
            "RISK_LIMIT",
            spread,
            ctx.limits.max_spread_pct,
        )
    return _result(
        name,
        Domain.LIQUIDITY,
        Outcome.PASS,
        "Spread acceptable.",
        observed=spread,
        limit=ctx.limits.max_spread_pct,
    )


def liquidity_slippage(ctx: context.RiskContext) -> risk.RiskCheckResult:
    """Checks estimated slippage of market orders from a fresh book.

    Args:
        ctx: Risk context.

    Returns:
        SKIP for orders that are not MARKET orders. For market orders, FAIL
        when the book is missing, flagged stale or older than
        ``max_book_age_ms``, when it is too thin to estimate slippage, or
        when the estimate exceeds ``max_slippage_pct``; each FAIL is a WARN
        (the last a PASS) for a risk-reducing order; else PASS.
    """
    name = "liquidity.slippage"
    if ctx.request.order_type is not enums.OrderType.MARKET:
        return _result(
            name,
            Domain.LIQUIDITY,
            Outcome.SKIP,
            "Resting order; no taker slippage estimate.",
        )
    book = ctx.book
    reducing = ctx.risk_reducing
    if (
        book is None
        or book.stale
        or (ctx.now_ms - book.as_of_ms > ctx.limits.max_book_age_ms)
    ):
        outcome = Outcome.WARN if reducing else Outcome.FAIL
        return _result(
            name,
            Domain.LIQUIDITY,
            outcome,
            "Order book is missing or stale.",
            "MARKET_STALE",
        )
    if ctx.slippage_pct is None:
        outcome = Outcome.WARN if reducing else Outcome.FAIL
        return _result(
            name,
            Domain.LIQUIDITY,
            outcome,
            "Book depth is insufficient for this size.",
            "RISK_LIMIT",
        )
    if ctx.slippage_pct > ctx.limits.max_slippage_pct and not reducing:
        return _result(
            name,
            Domain.LIQUIDITY,
            Outcome.FAIL,
            "Estimated slippage too high.",
            "RISK_LIMIT",
            ctx.slippage_pct,
            ctx.limits.max_slippage_pct,
        )
    return _result(
        name,
        Domain.LIQUIDITY,
        Outcome.PASS,
        "Slippage acceptable.",
        observed=ctx.slippage_pct,
        limit=ctx.limits.max_slippage_pct,
    )


def decision_quality(ctx: context.RiskContext) -> risk.RiskCheckResult:  # noqa: PLR0911 - one return per deployment or signal condition, checked in a fixed order.
    """Checks deployment, instrument allow-list and signal age.

    Args:
        ctx: Risk context.

    Returns:
        SKIP for orders whose source is not STRATEGY. FAIL when the
        deployment is missing or neither ENABLED nor PENDING_CONSENT, is
        bound to another strategy version, has another order mode, or does
        not allow the instrument (an AUTO_LIVE deployment allows only the
        instruments it lists, so an empty list allows none; other
        deployments are unrestricted while their list is empty). Then
        UNKNOWN when the signal age is unknown, FAIL when it exceeds
        ``max_signal_age_ms``; else PASS.
    """
    name = "decision.strategy"
    req = ctx.request
    if req.source is not enums.IntentSource.STRATEGY:
        return _result(
            name, Domain.DECISION_QUALITY, Outcome.SKIP, "Manual order."
        )
    dep = ctx.deployment
    if dep is None or dep.state not in (
        enums.DeploymentState.ENABLED,
        enums.DeploymentState.PENDING_CONSENT,
    ):
        return _result(
            name,
            Domain.DECISION_QUALITY,
            Outcome.FAIL,
            "No enabled deployment for this strategy version.",
            "TRADE_MODE_DISABLED",
        )
    if dep.strategy_version_id != req.strategy_version_id:
        return _result(
            name,
            Domain.DECISION_QUALITY,
            Outcome.FAIL,
            "Deployment is bound to another strategy version.",
            "TRADE_MODE_DISABLED",
        )
    if dep.mode.order_mode is not req.mode:
        return _result(
            name,
            Domain.DECISION_QUALITY,
            Outcome.FAIL,
            "Order mode does not match the deployment.",
            "TRADE_MODE_DISABLED",
        )
    allowed = dep.limits.allowed_instruments
    if (allowed or dep.mode is enums.DeploymentMode.AUTO_LIVE) and (
        req.instrument_id not in allowed
    ):
        return _result(
            name,
            Domain.DECISION_QUALITY,
            Outcome.FAIL,
            "Instrument is not allowed by the deployment.",
            "RISK_LIMIT",
            req.instrument_id,
        )
    if ctx.signal_age_ms is None:
        return _result(
            name,
            Domain.DECISION_QUALITY,
            Outcome.UNKNOWN,
            "Signal age unknown.",
            "RISK_LIMIT",
        )
    if ctx.signal_age_ms > ctx.limits.max_signal_age_ms:
        return _result(
            name,
            Domain.DECISION_QUALITY,
            Outcome.FAIL,
            "Signal is too old.",
            "RISK_LIMIT",
            f"{ctx.signal_age_ms}ms",
            f"{ctx.limits.max_signal_age_ms}ms",
        )
    return _result(
        name,
        Domain.DECISION_QUALITY,
        Outcome.PASS,
        "Strategy deployment and signal valid.",
    )


def duplicate_guard(ctx: context.RiskContext) -> risk.RiskCheckResult:
    """Blocks a second intent per signal and concurrent strategy orders.

    Args:
        ctx: Risk context.

    Returns:
        FAIL when an intent already exists for the order's signal, or when
        a STRATEGY order's strategy version already has a working opening
        order on the instrument; else PASS.
    """
    name = "duplicate.guard"
    req = ctx.request
    if req.signal_id is not None and ctx.signal_intents > 0:
        return _result(
            name,
            Domain.DUPLICATE,
            Outcome.FAIL,
            "An intent already exists for this signal.",
            "DUPLICATE_INTENT",
            ctx.signal_intents,
            0,
        )
    if req.source is enums.IntentSource.STRATEGY and (
        ctx.strategy_open_orders > 0
    ):
        return _result(
            name,
            Domain.DUPLICATE,
            Outcome.FAIL,
            "The strategy already has a working order here.",
            "DUPLICATE_INTENT",
            ctx.strategy_open_orders,
            0,
        )
    return _result(name, Domain.DUPLICATE, Outcome.PASS, "No duplicate.")


def connectivity(ctx: context.RiskContext) -> risk.RiskCheckResult:
    """Checks adapter health and reject spikes.

    Args:
        ctx: Risk context.

    Returns:
        UNKNOWN when health is unknown; FAIL when the adapter is
        UNAVAILABLE or STALE, or when recent venue rejects reach
        ``reject_spike_threshold``; for DEGRADED, FAIL for a LIVE order that
        is not risk-reducing, else WARN; PASS when HEALTHY.
    """
    name = "connectivity.adapter"
    health = ctx.adapter_health
    if health is None:
        return _result(
            name,
            Domain.CONNECTIVITY,
            Outcome.UNKNOWN,
            "Adapter health unknown.",
            "VENUE_UNAVAILABLE",
        )
    if health in (enums.HealthState.UNAVAILABLE, enums.HealthState.STALE):
        return _result(
            name,
            Domain.CONNECTIVITY,
            Outcome.FAIL,
            "Venue connection is down.",
            "VENUE_UNAVAILABLE",
            health.value,
        )
    if ctx.recent_rejects >= ctx.limits.reject_spike_threshold:
        return _result(
            name,
            Domain.CONNECTIVITY,
            Outcome.FAIL,
            "Venue reject spike; trading paused.",
            "VENUE_UNAVAILABLE",
            ctx.recent_rejects,
            ctx.limits.reject_spike_threshold,
        )
    if health is enums.HealthState.DEGRADED:
        live = ctx.request.mode is enums.OrderMode.LIVE
        outcome = (
            Outcome.FAIL if live and not ctx.risk_reducing else Outcome.WARN
        )
        return _result(
            name,
            Domain.CONNECTIVITY,
            outcome,
            "Venue connection degraded.",
            "VENUE_UNAVAILABLE",
        )
    return _result(
        name, Domain.CONNECTIVITY, Outcome.PASS, "Venue connection healthy."
    )


def kill_switch_guard(ctx: context.RiskContext) -> risk.RiskCheckResult:
    """Blocks new exposure under an engaged kill switch.

    The platform emergency halt counts as a switch covering every order;
    otherwise every engaged tenant switch that covers the order's account
    or strategy version (``KillSwitch.applies_to``) counts. Risk-reducing
    orders stay allowed, with a warning recorded on the decision.

    Args:
        ctx: Risk context.

    Returns:
        PASS when no switch covers the order; WARN for a risk-reducing
        order; else FAIL.
    """
    name = "kill_switch.engaged"
    req = ctx.request
    if ctx.emergency_halt:
        engaged = ["PLATFORM_HALT"]
    else:
        engaged = [
            s.scope.value
            for s in ctx.kill_switches
            if s.applies_to(req.account_id, req.strategy_version_id)
        ]
    if not engaged:
        return _result(
            name, Domain.KILL_SWITCH, Outcome.PASS, "No kill switch engaged."
        )
    if ctx.risk_reducing:
        return _result(
            name,
            Domain.KILL_SWITCH,
            Outcome.WARN,
            "Kill switch engaged; risk-reducing order allowed.",
            observed=",".join(engaged),
        )
    return _result(
        name,
        Domain.KILL_SWITCH,
        Outcome.FAIL,
        "Kill switch engaged; new exposure is blocked.",
        "KILL_SWITCH_ENGAGED",
        ",".join(engaged),
    )


def protection(ctx: context.RiskContext) -> risk.RiskCheckResult:
    """Requires venue-native stop-loss for automated live trading.

    Args:
        ctx: Risk context.

    Returns:
        For LIVE orders from a STRATEGY (auto-live): FAIL when the venue has
        no native stop-loss or the order carries no stop-loss leg, else
        PASS. For other orders: SKIP without protective legs, PASS when the
        venue holds stop-losses natively, else WARN (software-managed legs
        are not guaranteed).
    """
    name = "protection.stop_loss"
    req = ctx.request
    caps = ctx.capabilities
    auto_live = (
        req.mode is enums.OrderMode.LIVE
        and req.source is enums.IntentSource.STRATEGY
    )
    if auto_live:
        if caps is None or not caps.native_stop_loss:
            return _result(
                name,
                Domain.PROTECTION,
                Outcome.FAIL,
                "Auto-live requires venue-native stop-loss.",
                "UNSUPPORTED",
            )
        if req.bracket.stop_loss is None:
            return _result(
                name,
                Domain.PROTECTION,
                Outcome.FAIL,
                "Auto-live orders must carry a stop-loss.",
                "VALIDATION_FAILED",
            )
        return _result(
            name,
            Domain.PROTECTION,
            Outcome.PASS,
            "Venue-native stop-loss attached.",
        )
    if req.bracket.empty:
        return _result(
            name,
            Domain.PROTECTION,
            Outcome.SKIP,
            "No protective orders requested.",
        )
    if caps is not None and caps.native_stop_loss:
        return _result(
            name,
            Domain.PROTECTION,
            Outcome.PASS,
            "Protective orders are venue-held.",
        )
    return _result(
        name,
        Domain.PROTECTION,
        Outcome.WARN,
        "Protective orders are software-managed and not guaranteed.",
    )


# ---------------------------------------------------------------------------
# Broker routing and exchange-listed securities. These checks SKIP for
# crypto instruments, except that DAY and outside-RTH (regular trading
# hours) orders FAIL there because they need an exchange session.
# ---------------------------------------------------------------------------


def _when(ts_ms: int) -> str:
    """Formats a time for messages.

    Args:
        ts_ms: UTC epoch milliseconds.

    Returns:
        Text like ``2026-11-24 01:00 UTC``.
    """
    moment = datetime.datetime.fromtimestamp(ts_ms / 1000, tz=datetime.UTC)
    return moment.strftime("%Y-%m-%d %H:%M UTC")


def routing_venue(ctx: context.RiskContext) -> risk.RiskCheckResult:
    """Checks that the account may trade the instrument's venue.

    Crypto accounts trade their own venue; broker accounts trade the
    exchanges their broker serves; paper accounts trade in their currency;
    rate series are never tradable.

    Args:
        ctx: Risk context.

    Returns:
        UNKNOWN without an account; FAIL when ``equities.route`` refuses the
        instrument for the account, or when the adapter lists its tradable
        exchanges and the instrument's exchange is not among them; else
        PASS.
    """
    name = "routing.venue"
    account = ctx.account
    req = ctx.request
    if account is None:
        return _result(
            name,
            Domain.ACCOUNT,
            Outcome.UNKNOWN,
            "Account is unknown.",
            "NOT_FOUND",
        )
    verdict = equities.route(
        account.venue, account.is_paper, account.currency, req.instrument_id
    )
    if not verdict.allowed:
        return _result(
            name,
            Domain.ACCOUNT,
            Outcome.FAIL,
            verdict.message,
            "UNSUPPORTED",
            req.instrument_id,
            account.venue,
        )
    venue = equities.parse_instrument_id(req.instrument_id).venue
    caps = ctx.capabilities
    if (
        venue in equities.EXCHANGE_VENUES
        and caps is not None
        and caps.tradable_venues
        and venue not in caps.tradable_venues
    ):
        return _result(
            name,
            Domain.ACCOUNT,
            Outcome.FAIL,
            f"The {caps.venue} adapter does not route {venue} orders.",
            "UNSUPPORTED",
            venue,
            ",".join(caps.tradable_venues),
        )
    return _result(name, Domain.ACCOUNT, Outcome.PASS, verdict.message)


def market_session(ctx: context.RiskContext) -> risk.RiskCheckResult:  # noqa: PLR0911, PLR0912 - one branch per session rule, checked in a fixed order.
    """Applies exchange-session rules from the instrument's calendar.

    Market orders while the exchange is closed are rejected, or queue as
    DAY orders for the next open when configuration and the broker allow
    it. IOC/FOK need an open session; resting limit orders wait for it.

    Args:
        ctx: Risk context.

    Returns:
        For crypto instruments: FAIL for DAY or outside-RTH orders, else
        SKIP. For securities: FAIL for an outside-RTH order the account
        cannot flag or that is not LIMIT or STOP_LIMIT; UNKNOWN (WARN when
        risk-reducing) without a session calendar; PASS inside a session.
        While closed: a DAY MARKET order WARNs (it queues for the next
        open) when queueing is configured and supported, and every other
        MARKET order FAILs; IOC/FOK orders FAIL; any other order
        (outside-RTH or resting) WARNs.
    """
    name = "market.session"
    req = ctx.request
    if not ctx.security:
        if req.time_in_force is enums.TimeInForce.DAY:
            return _result(
                name,
                Domain.MARKET_STATUS,
                Outcome.FAIL,
                "DAY orders expire at an exchange close; this market trades "
                "around the clock. Use GTC.",
                "UNSUPPORTED_ORDER_TYPE",
                req.time_in_force.value,
            )
        if req.outside_rth:
            return _result(
                name,
                Domain.MARKET_STATUS,
                Outcome.FAIL,
                "Outside-RTH applies to exchange-listed securities only.",
                "UNSUPPORTED_ORDER_TYPE",
            )
        return _result(
            name, Domain.MARKET_STATUS, Outcome.SKIP, "Market trades 24x7."
        )
    caps = ctx.capabilities
    if req.outside_rth:
        if caps is None or not caps.outside_rth:
            return _result(
                name,
                Domain.MARKET_STATUS,
                Outcome.FAIL,
                "This account cannot flag orders for outside regular hours.",
                "UNSUPPORTED_ORDER_TYPE",
            )
        if req.order_type not in (
            enums.OrderType.LIMIT,
            enums.OrderType.STOP_LIMIT,
        ):
            return _result(
                name,
                Domain.MARKET_STATUS,
                Outcome.FAIL,
                "Outside-RTH orders must be limit or stop-limit orders.",
                "UNSUPPORTED_ORDER_TYPE",
                req.order_type.value,
            )
    if ctx.sessions is None:
        outcome = Outcome.WARN if ctx.risk_reducing else Outcome.UNKNOWN
        return _result(
            name,
            Domain.MARKET_STATUS,
            outcome,
            "The exchange session calendar is unavailable.",
            "MARKET_STALE",
        )
    state = equities.session_state(ctx.sessions, ctx.now_ms)
    if state.current is not None:
        return _result(
            name,
            Domain.MARKET_STATUS,
            Outcome.PASS,
            f"Market open until {_when(state.current.close_ms)}.",
        )
    upcoming = state.next_session
    next_open = "the next open" + (
        "" if upcoming is None else f" ({_when(upcoming.open_ms)})"
    )
    if req.order_type is enums.OrderType.MARKET:
        queueable = (
            ctx.queue_closed_market_orders
            and caps is not None
            and caps.queue_market_when_closed
        )
        if queueable and req.time_in_force is enums.TimeInForce.DAY:
            return _result(
                name,
                Domain.MARKET_STATUS,
                Outcome.WARN,
                f"Market closed; the market order queues as DAY for "
                f"{next_open}.",
            )
        if queueable:
            return _result(
                name,
                Domain.MARKET_STATUS,
                Outcome.FAIL,
                f"Market closed; submit the market order as DAY to queue "
                f"it for {next_open}.",
                "UNSUPPORTED_ORDER_TYPE",
                req.time_in_force.value,
                "DAY",
            )
        return _result(
            name,
            Domain.MARKET_STATUS,
            Outcome.FAIL,
            f"Market closed; market orders are rejected until {next_open}.",
            "VENUE_UNAVAILABLE",
        )
    if req.time_in_force in (enums.TimeInForce.IOC, enums.TimeInForce.FOK):
        return _result(
            name,
            Domain.MARKET_STATUS,
            Outcome.FAIL,
            f"Market closed; {req.time_in_force.value} orders need an open "
            f"session ({next_open}).",
            "UNSUPPORTED_ORDER_TYPE",
            req.time_in_force.value,
        )
    if req.outside_rth:
        return _result(
            name,
            Domain.MARKET_STATUS,
            Outcome.WARN,
            "Outside regular hours; the broker routes the order to "
            "extended-hours trading where the venue offers it.",
        )
    return _result(
        name,
        Domain.MARKET_STATUS,
        Outcome.WARN,
        f"Market closed; the order rests until {next_open}.",
    )


def size_board_lot(ctx: context.RiskContext) -> risk.RiskCheckResult:  # noqa: PLR0911 - one return per lot rule, checked in a fixed order.
    """Checks board lots and intraday odd-lot rules (TWSE, TPEx).

    Common orders are whole board lots (1000 shares on TWSE and TPEx);
    intraday odd-lot orders are 1-999 shares, limit and ROD (rest of day,
    i.e. DAY) only.

    Args:
        ctx: Risk context.

    Returns:
        SKIP unless the instrument is a security with a board lot; PASS for
        whole board lots; FAIL for a mix of board and odd lots. For odd
        lots: UNKNOWN without capabilities; FAIL unless the account
        supports odd lots and the order is a DAY LIMIT order; else PASS.
    """
    name = "size.board_lot"
    inst = ctx.instrument
    req = ctx.request
    if not ctx.security or inst is None or inst.board_lot is None:
        return _result(
            name, Domain.SIZE, Outcome.SKIP, "No board-lot rule applies."
        )
    lot = inst.board_lot
    qty = req.quantity_base
    kind = equities.lot_kind(qty, lot)
    if kind is equities.LotKind.BOARD:
        return _result(
            name,
            Domain.SIZE,
            Outcome.PASS,
            f"{decimals.fmt(qty / lot)} board lot(s) of {decimals.fmt(lot)} "
            "shares.",
        )
    if kind is equities.LotKind.MIXED:
        whole = decimals.floor_to_step(qty, lot)
        return _result(
            name,
            Domain.SIZE,
            Outcome.FAIL,
            f"{decimals.fmt(qty)} shares is not a whole number of "
            f"{decimals.fmt(lot)}-share board lots; send "
            f"{decimals.fmt(whole)} as board lots and "
            f"{decimals.fmt(qty - whole)} as an intraday odd-lot order.",
            "INVALID_LOT_SIZE",
            qty,
            lot,
        )
    caps = ctx.capabilities
    if caps is None:
        return _result(
            name,
            Domain.SIZE,
            Outcome.UNKNOWN,
            "Capabilities unknown; odd-lot support cannot be checked.",
            "INVALID_LOT_SIZE",
        )
    if not caps.odd_lot:
        return _result(
            name,
            Domain.SIZE,
            Outcome.FAIL,
            "This account cannot send intraday odd-lot orders.",
            "INVALID_LOT_SIZE",
            qty,
            lot,
        )
    if req.order_type is not enums.OrderType.LIMIT:
        return _result(
            name,
            Domain.SIZE,
            Outcome.FAIL,
            "Intraday odd-lot orders must be limit orders.",
            "UNSUPPORTED_ORDER_TYPE",
            req.order_type.value,
        )
    if req.time_in_force is not enums.TimeInForce.DAY:
        return _result(
            name,
            Domain.SIZE,
            Outcome.FAIL,
            "Intraday odd-lot orders are ROD (DAY) only.",
            "UNSUPPORTED_ORDER_TYPE",
            req.time_in_force.value,
            "DAY",
        )
    return _result(
        name,
        Domain.SIZE,
        Outcome.PASS,
        f"Intraday odd-lot order of {decimals.fmt(qty)} shares.",
    )


def size_price_limit(ctx: context.RiskContext) -> risk.RiskCheckResult:
    """Checks limit prices against the daily limits (TWSE/TPEx +-10%).

    Args:
        ctx: Risk context.

    Returns:
        SKIP for crypto instruments, instruments without a daily limit and
        orders without a limit price; UNKNOWN without the previous close;
        FAIL outside the band around it; else PASS.
    """
    name = "size.price_limit"
    inst = ctx.instrument
    price = ctx.request.limit_price
    if not ctx.security or inst is None or inst.price_limit_pct is None:
        return _result(name, Domain.SIZE, Outcome.SKIP, "No daily price limit.")
    if price is None:
        return _result(
            name,
            Domain.SIZE,
            Outcome.SKIP,
            "Market and stop orders execute inside the exchange limits.",
        )
    reference = ctx.previous_close
    limits = (
        None if reference is None else equities.price_limits(reference, inst)
    )
    if limits is None:
        return _result(
            name,
            Domain.SIZE,
            Outcome.UNKNOWN,
            "The reference price (previous close) is unknown; daily limits "
            "cannot be checked.",
            "MARKET_STALE",
        )
    band = f"{decimals.fmt(limits.low)}-{decimals.fmt(limits.high)}"
    if not limits.contains(price):
        return _result(
            name,
            Domain.SIZE,
            Outcome.FAIL,
            f"Limit price is outside today's "
            f"{decimals.fmt(inst.price_limit_pct)}% band from the "
            f"{decimals.fmt(limits.reference)} reference ({band}).",
            "RISK_LIMIT",
            price,
            band,
        )
    return _result(
        name,
        Domain.SIZE,
        Outcome.PASS,
        "Limit price within the daily limits.",
        observed=price,
        limit=band,
    )


def account_cash(ctx: context.RiskContext) -> risk.RiskCheckResult:  # noqa: PLR0911 - one return per cash condition, checked in a fixed order.
    """Checks cash buying power and currency for securities (no margin).

    Args:
        ctx: Risk context.

    Returns:
        SKIP for crypto instruments and for sells covered by the long
        position; UNKNOWN without the cash balance; FAIL when the
        instrument's currency is not the cash currency; UNKNOWN without the
        order notional; FAIL when notional plus estimated fee exceeds the
        available cash; else PASS.
    """
    name = "account.cash"
    req = ctx.request
    if not ctx.security:
        return _result(
            name, Domain.ACCOUNT, Outcome.SKIP, "Not a securities order."
        )
    if req.side is enums.OrderSide.SELL and req.quantity_base <= ctx.long_qty:
        return _result(
            name,
            Domain.ACCOUNT,
            Outcome.SKIP,
            "Selling shares already held needs no cash.",
        )
    cash = ctx.cash
    if cash is None:
        return _result(
            name,
            Domain.ACCOUNT,
            Outcome.UNKNOWN,
            "Cash buying power is unknown.",
            "INSUFFICIENT_MARGIN",
        )
    quote = None if ctx.instrument is None else ctx.instrument.quote
    currency = equities.instrument_currency(req.instrument_id, quote)
    if currency != cash.currency:
        return _result(
            name,
            Domain.ACCOUNT,
            Outcome.FAIL,
            f"{req.instrument_id} trades in {currency}; the account holds "
            f"{cash.currency}.",
            "UNSUPPORTED",
            currency,
            cash.currency,
        )
    if ctx.notional is None:
        return _result(
            name,
            Domain.ACCOUNT,
            Outcome.UNKNOWN,
            "Order cost is unknown.",
            "INSUFFICIENT_MARGIN",
        )
    needed = ctx.notional + (ctx.estimated_fee or decimals.ZERO)
    if needed > cash.available:
        return _result(
            name,
            Domain.ACCOUNT,
            Outcome.FAIL,
            f"Not enough {cash.currency} cash; securities are bought from "
            "cash without margin.",
            "INSUFFICIENT_MARGIN",
            needed,
            cash.available,
        )
    return _result(
        name,
        Domain.ACCOUNT,
        Outcome.PASS,
        f"{cash.currency} cash available.",
        observed=needed,
        limit=cash.available,
    )


def size_cash_account(ctx: context.RiskContext) -> risk.RiskCheckResult:
    """Enforces no leverage and long-only selling for securities.

    Args:
        ctx: Risk context.

    Returns:
        SKIP for crypto instruments; UNKNOWN without capabilities; FAIL for
        leverage other than 1 on a cash account, or for a sell larger than
        the long position without short selling; else PASS.
    """
    name = "size.cash_account"
    req = ctx.request
    if not ctx.security:
        return _result(
            name, Domain.SIZE, Outcome.SKIP, "Not a securities order."
        )
    caps = ctx.capabilities
    if caps is None:
        return _result(
            name,
            Domain.SIZE,
            Outcome.UNKNOWN,
            "Capabilities unknown.",
            "UNSUPPORTED",
        )
    if caps.cash_account and req.leverage != decimals.ONE:
        return _result(
            name,
            Domain.SIZE,
            Outcome.FAIL,
            "Cash accounts trade securities without leverage (use 1).",
            "RISK_LIMIT",
            req.leverage,
            decimals.ONE,
        )
    held = ctx.long_qty
    if (
        req.side is enums.OrderSide.SELL
        and req.quantity_base > held
        and not caps.short_selling
    ):
        return _result(
            name,
            Domain.SIZE,
            Outcome.FAIL,
            f"Long-only account: sell at most the {decimals.fmt(held)} "
            "shares held.",
            "VALIDATION_FAILED",
            req.quantity_base,
            held,
        )
    return _result(
        name, Domain.SIZE, Outcome.PASS, "Cash-account rules satisfied."
    )


def protection_bracket(ctx: context.RiskContext) -> risk.RiskCheckResult:
    """Checks that protective legs can be placed with the account's orders.

    Protective children are GTC stop-market and limit orders; a broker
    without them cannot hold a bracket.

    Args:
        ctx: Risk context.

    Returns:
        SKIP without protective legs; UNKNOWN without capabilities; FAIL
        when the venue lacks STOP_MARKET (for a stop-loss), LIMIT (for
        take-profits) or GTC; else PASS.
    """
    name = "protection.bracket_orders"
    bracket = ctx.request.bracket
    if bracket.empty:
        return _result(
            name, Domain.PROTECTION, Outcome.SKIP, "No protective orders."
        )
    caps = ctx.capabilities
    if caps is None:
        return _result(
            name,
            Domain.PROTECTION,
            Outcome.UNKNOWN,
            "Capabilities unknown.",
            "UNSUPPORTED",
        )
    missing: list[str] = []
    if (
        bracket.stop_loss is not None
        and enums.OrderType.STOP_MARKET not in caps.order_types
    ):
        missing.append("stop-market")
    if bracket.take_profit and enums.OrderType.LIMIT not in caps.order_types:
        missing.append("limit")
    if enums.TimeInForce.GTC not in caps.time_in_force:
        missing.append("GTC")
    if missing:
        return _result(
            name,
            Domain.PROTECTION,
            Outcome.FAIL,
            f"Protective orders need {', '.join(missing)} support, which "
            f"{caps.venue} does not offer.",
            "UNSUPPORTED",
            ",".join(missing),
        )
    return _result(
        name,
        Domain.PROTECTION,
        Outcome.PASS,
        "Protective order types are supported.",
    )


@dataclasses.dataclass(frozen=True, slots=True)
class CheckSpec:
    """A check in the pipeline and the decision its FAIL leads to.

    A FAIL leads to BLOCK unless one of the flags is set; UNKNOWN always
    leads to BLOCK.

    Attributes:
        fn: Check function.
        pause_on_fail: A FAIL leads to PAUSE (trading paused) instead.
        reject_on_fail: A FAIL leads to REJECT (the order is invalid as
            submitted); takes precedence over ``pause_on_fail``.
    """

    fn: Check
    pause_on_fail: bool = False
    reject_on_fail: bool = False


PIPELINE: tuple[CheckSpec, ...] = (
    CheckSpec(mode_allowed),
    CheckSpec(identity_scope),
    CheckSpec(routing_venue, reject_on_fail=True),
    CheckSpec(account_state_known),
    CheckSpec(account_margin),
    CheckSpec(account_cash),
    CheckSpec(market_status),
    CheckSpec(market_session, reject_on_fail=True),
    CheckSpec(data_quote_fresh),
    CheckSpec(size_quantity, reject_on_fail=True),
    CheckSpec(size_board_lot, reject_on_fail=True),
    CheckSpec(size_tick, reject_on_fail=True),
    CheckSpec(size_price_limit, reject_on_fail=True),
    CheckSpec(size_min_notional, reject_on_fail=True),
    CheckSpec(size_leverage, reject_on_fail=True),
    CheckSpec(size_cash_account, reject_on_fail=True),
    CheckSpec(size_price_band, reject_on_fail=True),
    CheckSpec(size_order_type, reject_on_fail=True),
    CheckSpec(size_reduce_only, reject_on_fail=True),
    CheckSpec(exposure_limits),
    CheckSpec(exposure_daily_loss, pause_on_fail=True),
    CheckSpec(liquidity_spread),
    CheckSpec(liquidity_slippage),
    CheckSpec(decision_quality),
    CheckSpec(duplicate_guard),
    CheckSpec(connectivity, pause_on_fail=True),
    CheckSpec(kill_switch_guard),
    CheckSpec(protection),
    CheckSpec(protection_bracket, reject_on_fail=True),
)
"""Default pipeline: every check runs for every intent, in this order."""
