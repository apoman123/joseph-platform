"""Risk inputs specific to exchange-listed securities.

Gathers what the securities checks consult: the exchange sessions around
now (market engine calendar), the reference price of daily limits and the
cash buying power. Paper and shadow accounts value cash from local
positions; live broker accounts take it from the broker's account summary,
less Joseph's own open-order reservations (conservative double counting is
preferred to overspending).
"""

from __future__ import annotations

import dataclasses
import decimal

from joseph_execution.domain import accounts
from joseph_execution.domain import decimals
from joseph_execution.domain import enums
from joseph_execution.domain import equities
from joseph_execution.domain import errors
from joseph_execution.domain import market
from joseph_execution.oms import valuation
from joseph_execution.ports import market as market_port
from joseph_execution.ports import repositories
from joseph_execution.ports import trading

_DAY_MS = 86_400_000
SESSION_LOOKBACK_MS = 5 * _DAY_MS
"""Sessions fetched before now (covers the session in progress)."""

SESSION_LOOKAHEAD_MS = 15 * _DAY_MS
"""Sessions fetched after now (covers Lunar New Year closures)."""


@dataclasses.dataclass(frozen=True, slots=True)
class EquityInputs:
    """Securities inputs of one risk evaluation.

    Attributes:
        sessions: Sessions around now, ``None`` when unknown.
        previous_close: Reference price, ``None`` when unknown or unused.
        cash: Cash buying power, ``None`` when unknown.
        equity: Account equity for the context.
        available_margin: Free funds for the context.
        gross_notional: Gross position notional for the context.
    """

    sessions: tuple[market.TradingSession, ...] | None
    previous_close: decimal.Decimal | None
    cash: equities.CashBalance | None
    equity: decimal.Decimal | None
    available_margin: decimal.Decimal | None
    gross_notional: decimal.Decimal | None


async def sessions_around(
    gateway: market_port.MarketDataGateway, calendar: str, now_ms: int
) -> tuple[market.TradingSession, ...] | None:
    """Fetches the sessions of a calendar around a time.

    Args:
        gateway: Market gateway.
        calendar: Calendar id.
        now_ms: Time.

    Returns:
        Sessions, or ``None`` when the calendar is unavailable.
    """
    try:
        sessions = await gateway.sessions(
            calendar,
            now_ms - SESSION_LOOKBACK_MS,
            now_ms + SESSION_LOOKAHEAD_MS,
        )
    except errors.ExecutionError:
        return None
    return None if sessions is None else tuple(sessions)


async def _previous_close(
    gateway: market_port.MarketDataGateway, instrument_id: str
) -> decimal.Decimal | None:
    """Fetches the reference price, mapping failures to ``None``.

    Args:
        gateway: Market gateway.
        instrument_id: Instrument.

    Returns:
        Previous close or ``None``.
    """
    try:
        return await gateway.previous_close(instrument_id)
    except errors.ExecutionError:
        return None


def _unrealized(val: valuation.Valuation) -> decimal.Decimal:
    """Sums the known unrealized PnL of a valuation.

    Args:
        val: Valuation.

    Returns:
        Unrealized PnL of positions with a mark.
    """
    total = decimals.ZERO
    for pos in val.positions:
        pnl = pos.unrealized_pnl(val.marks.get(pos.instrument_id))
        if pnl is not None:
            total += pnl
    return total


def _gross(val: valuation.Valuation) -> decimal.Decimal | None:
    """Computes gross notional from positions and marks.

    Args:
        val: Valuation.

    Returns:
        Gross notional, ``None`` when a mark is missing.
    """
    gross = decimals.ZERO
    for pos in val.positions:
        mark = val.marks.get(pos.instrument_id)
        if mark is None:
            return None
        gross += pos.quantity * mark
    return gross


async def gather(  # noqa: PLR0917
    gateway: market_port.MarketDataGateway,
    uow_factory: repositories.UnitOfWorkFactory,
    adapter: trading.TradingAdapter,
    account: accounts.Account,
    mode: enums.OrderMode,
    instrument: market.InstrumentMeta | None,
    val: valuation.Valuation,
    now_ms: int,
) -> EquityInputs:
    """Gathers the securities inputs of a risk evaluation.

    Args:
        gateway: Market gateway.
        uow_factory: Unit of work factory.
        adapter: Adapter resolved for the account and mode.
        account: Account.
        mode: Order mode.
        instrument: Instrument metadata, if known.
        val: Local valuation of the account in this mode.
        now_ms: Evaluation time.

    Returns:
        Inputs; unknown values are ``None`` (checks then fail closed).
    """
    sessions = None
    previous = None
    if instrument is not None:
        sessions = await sessions_around(gateway, instrument.calendar, now_ms)
        if instrument.price_limit_pct is not None:
            previous = await _previous_close(gateway, instrument.instrument_id)
    if mode is not enums.OrderMode.LIVE:
        cash = (
            None
            if val.available_margin is None
            else equities.CashBalance(
                currency=account.currency,
                available=val.available_margin - _unrealized(val),
            )
        )
        return EquityInputs(
            sessions,
            previous,
            cash,
            val.equity,
            val.available_margin,
            val.gross_notional,
        )
    gross = _gross(val)
    try:
        snapshot = await adapter.account_snapshot(account.account_id)
    except errors.ExecutionError:
        return EquityInputs(sessions, previous, None, None, None, gross)
    with uow_factory() as uow:
        reserved = uow.reservations.active_total(account.account_id, mode)
    available = snapshot.available_margin - reserved
    cash = equities.CashBalance(
        currency=snapshot.currency,
        available=available,
        settled=snapshot.settled_cash,
        unsettled=snapshot.unsettled_cash,
        source=adapter.name,
    )
    return EquityInputs(
        sessions, previous, cash, snapshot.equity, available, gross
    )
