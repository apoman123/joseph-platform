"""Individual pre-trade checks (V5 §8 risk table).

Each check is a pure function of a ``RiskContext`` returning one
``RiskCheckResult``. Missing inputs produce UNKNOWN, never PASS.
"""

from __future__ import annotations

import dataclasses
import decimal
from collections.abc import Callable

from joseph_execution.domain import decimals
from joseph_execution.domain import enums
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
        if not decimals.is_multiple(price, inst.tick_size):
            return _result(
                name,
                Domain.SIZE,
                Outcome.FAIL,
                "Price is not a multiple of the tick size.",
                "INVALID_TICK_SIZE",
                price,
                inst.tick_size,
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
    if req.reduce_only and not caps.native_reduce_only:
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
    CheckSpec(account_state_known),
    CheckSpec(account_margin),
    CheckSpec(market_status),
    CheckSpec(data_quote_fresh),
    CheckSpec(size_quantity, reject_on_fail=True),
    CheckSpec(size_tick, reject_on_fail=True),
    CheckSpec(size_min_notional, reject_on_fail=True),
    CheckSpec(size_leverage, reject_on_fail=True),
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
)
"""Ordered, mandatory check pipeline (deny by default)."""
