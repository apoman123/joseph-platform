"""Account valuation used by risk checks and account summaries."""

from __future__ import annotations

import dataclasses
import decimal

from joseph_execution.domain import accounts
from joseph_execution.domain import decimals
from joseph_execution.domain import enums
from joseph_execution.domain import errors
from joseph_execution.domain import market
from joseph_execution.domain import positions
from joseph_execution.ports import market as market_port
from joseph_execution.ports import repositories


@dataclasses.dataclass(frozen=True, slots=True)
class Valuation:
    """Equity and exposure of one account in one mode.

    Attributes:
        equity: Equity, ``None`` when unknown.
        available_margin: Free margin after positions and reservations.
        gross_notional: Gross position notional at marks.
        positions: Open positions.
        marks: Mark price per instrument (``None`` when unavailable).
        stale: True when any mark is missing or stale.
    """

    equity: decimal.Decimal | None
    available_margin: decimal.Decimal | None
    gross_notional: decimal.Decimal | None
    positions: tuple[positions.Position, ...]
    marks: dict[str, decimal.Decimal | None]
    stale: bool


async def quote_or_none(
    gateway: market_port.MarketDataGateway, instrument_id: str
) -> market.MarketQuote | None:
    """Fetches a quote, mapping failures to ``None``.

    Args:
        gateway: Market gateway.
        instrument_id: Instrument.

    Returns:
        Quote or ``None``.
    """
    try:
        return await gateway.quote(instrument_id)
    except errors.ExecutionError:
        return None


async def value_account(  # noqa: PLR0917
    uow_factory: repositories.UnitOfWorkFactory,
    gateway: market_port.MarketDataGateway,
    account: accounts.Account,
    mode: enums.OrderMode,
    now_ms: int,
    max_quote_age_ms: int,
) -> Valuation:
    """Values a paper/shadow account from local positions and marks.

    Venue accounts return unknown equity (their balances come from the
    venue adapter, which is not available in this build).

    Args:
        uow_factory: Unit of work factory.
        gateway: Market gateway.
        account: Account.
        mode: Order mode.
        now_ms: Current time.
        max_quote_age_ms: Freshness budget for marks.

    Returns:
        Valuation.
    """
    with uow_factory() as uow:
        open_positions = tuple(
            p
            for p in uow.positions.list(account_id=account.account_id)
            if p.mode is mode
        )
        realized = sum(
            (
                p.realized_pnl
                for p in uow.positions.list(
                    account_id=account.account_id, include_flat=True
                )
                if p.mode is mode
            ),
            decimals.ZERO,
        )
        reserved = uow.reservations.active_total(account.account_id, mode)
    marks: dict[str, decimal.Decimal | None] = {}
    stale = False
    for pos in open_positions:
        quote = await quote_or_none(gateway, pos.instrument_id)
        mark = None if quote is None else quote.reference_price
        if quote is None or quote.age_ms(now_ms) > max_quote_age_ms:
            stale = True
        marks[pos.instrument_id] = mark
    if account.starting_equity is None:
        return Valuation(None, None, None, open_positions, marks, True)
    unrealized = decimals.ZERO
    gross: decimal.Decimal | None = decimals.ZERO
    for pos in open_positions:
        mark = marks.get(pos.instrument_id)
        pnl = pos.unrealized_pnl(mark)
        if pnl is None or mark is None:
            gross = None
            stale = True
            continue
        unrealized += pnl
        if gross is not None:
            gross += pos.quantity * mark
    equity = account.starting_equity + realized + unrealized
    used = sum((p.margin() for p in open_positions), decimals.ZERO)
    available = equity - used - reserved
    return Valuation(equity, available, gross, open_positions, marks, stale)
