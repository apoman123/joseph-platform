"""Individual pre-trade checks (V5 §8 risk table).

Each check is a pure function of a ``RiskContext`` returning one
``RiskCheckResult``. Missing inputs produce UNKNOWN, never PASS.
"""

from __future__ import annotations

import dataclasses
import datetime
import decimal
from collections.abc import Callable

from joseph_execution.domain import decimals
from joseph_execution.domain import enums
from joseph_execution.domain import equities
from joseph_execution.domain import risk
from joseph_execution.risk import context

Domain = enums.RiskDomain
Outcome = enums.CheckOutcome
Check = Callable[[context.RiskContext], risk.RiskCheckResult]

_FRESH_FEEDS = (enums.FeedState.LIVE,)
_DISPLAY_FEEDS = (
    enums.FeedState.POLL,
    enums.FeedState.DEMO,
    enums.FeedState.REPLAY,
)


def _result(  # noqa: PLR0917
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
        message: Explanation.
        code: Error code.
        observed: Observed value.
        limit: Limit value.

    Returns:
        Result.
    """

    def text(value: object) -> str | None:
        """Formats a value for display.

        Args:
            value: Value.

        Returns:
            Text or ``None``.
        """
        if value is None:
            return None
        if isinstance(value, decimal.Decimal):
            return decimals.fmt(value)
        return str(value)

    return risk.RiskCheckResult(
        check=check,
        domain=domain,
        outcome=outcome,
        message=message,
        code=code,
        observed=text(observed),
        limit=text(limit),
    )


def mode_allowed(ctx: context.RiskContext) -> risk.RiskCheckResult:  # noqa: PLR0911
    """Checks the order mode against account, flags and consent.

    LIVE needs the global gate and an AVAILABLE adapter. Strategy orders
    also need an enabled AUTO_LIVE deployment with valid consent; manual
    orders (ticket, chart, quick trade) need a valid account LIVE arming,
    except risk-reducing orders, which stay allowed (WARN) while disarmed
    like under a kill switch. Emergency flattening is never manual.

    Args:
        ctx: Risk context.

    Returns:
        Result.
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
    """Checks that the caller's tenant owns the account.

    Args:
        ctx: Risk context.

    Returns:
        Result.
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
        Result.
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
        Result.
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
        Result.
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
    or demo data with a warning.

    Args:
        ctx: Risk context.

    Returns:
        Result.
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
        Result.
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
    """Checks that limit and trigger prices sit on the tick grid.

    Args:
        ctx: Risk context.

    Returns:
        Result.
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
        Result.
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
        Result.
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
    """Checks that the limit price is within a band around the mark.

    Args:
        ctx: Risk context.

    Returns:
        Result.
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
        Result.
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
    long_only_sell = (
        ctx.security
        and not caps.short_selling
        and req.side is enums.OrderSide.SELL
    )
    if req.reduce_only and not caps.native_reduce_only and not long_only_sell:
        # A long-only securities account cannot open a short: the broker
        # rejects uncovered sells, so a reduce-only sell stays reducing.
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
        Result.
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


def exposure_limits(ctx: context.RiskContext) -> risk.RiskCheckResult:  # noqa: PLR0911
    """Checks gross, position and open-position limits.

    Args:
        ctx: Risk context.

    Returns:
        Result.
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
        Result.
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
        Result.
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
        Result.
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


def decision_quality(ctx: context.RiskContext) -> risk.RiskCheckResult:  # noqa: PLR0911
    """Checks deployment, instrument allow-list and signal age.

    Args:
        ctx: Risk context.

    Returns:
        Result.
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
        Result.
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
        Result.
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

    Risk-reducing orders stay allowed (and audited).

    Args:
        ctx: Risk context.

    Returns:
        Result.
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
        Result.
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
# crypto instruments (except that DAY and outside-RTH need an exchange).
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
        Result.
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


def market_session(ctx: context.RiskContext) -> risk.RiskCheckResult:  # noqa: PLR0911, PLR0912
    """Applies exchange-session rules from the instrument's calendar.

    Market orders while the exchange is closed are rejected, or queue as
    DAY orders for the next open when configuration and the broker allow
    it. IOC/FOK need an open session; resting limit orders wait for it.

    Args:
        ctx: Risk context.

    Returns:
        Result.
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


def size_board_lot(ctx: context.RiskContext) -> risk.RiskCheckResult:  # noqa: PLR0911
    """Checks board lots and intraday odd-lot rules (TWSE).

    Common orders are whole board lots (1000 shares); intraday odd-lot
    orders are 1-999 shares, limit and ROD (DAY) only.

    Args:
        ctx: Risk context.

    Returns:
        Result.
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
    """Checks limit prices against the daily limits (TWSE +-10%).

    Args:
        ctx: Risk context.

    Returns:
        Result.
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


def account_cash(ctx: context.RiskContext) -> risk.RiskCheckResult:  # noqa: PLR0911
    """Checks cash buying power and currency for securities (no margin).

    Args:
        ctx: Risk context.

    Returns:
        Result.
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
        Result.
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
        Result.
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
    """Registered check.

    Attributes:
        fn: Check function.
        pause_on_fail: A FAIL pauses trading instead of blocking.
        reject_on_fail: A FAIL rejects the order as invalid.
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
"""Ordered, mandatory check pipeline (deny by default)."""
