# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

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
from joseph_execution.ports import trading


@dataclasses.dataclass(frozen=True, slots=True)
class Valuation:
    """Equity and exposure of one account in one mode.

    Attributes:
        equity: Equity, ``None`` when unknown.
        available_margin: Free margin after positions and reservations,
            ``None`` when unknown.
        gross_notional: Gross position notional at marks, ``None`` when a
            mark is missing or equity is unknown.
        positions: Open positions of the account in this mode.
        marks: Mark price per instrument (``None`` when unavailable).
        stale: True when any mark is missing or stale, or equity is
            unknown.
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
        Quote, or ``None`` when there is none or the gateway raises
        ``ExecutionError``.
    """
    try:
        return await gateway.quote(instrument_id)
    except errors.ExecutionError:
        return None


async def value_account(  # noqa: PLR0917 - OMS, accounts, intake and tests pass these inputs positionally.
    uow_factory: repositories.UnitOfWorkFactory,
    gateway: market_port.MarketDataGateway,
    account: accounts.Account,
    mode: enums.OrderMode,
    now_ms: int,
    max_quote_age_ms: int,
    venue: trading.VenueAccountSnapshot | None = None,
) -> Valuation:
    """Values an account from local positions and marks.

    Accounts with a starting equity (paper and shadow) are valued as
    starting equity + realized + unrealized profit and loss (PnL) at
    marks; free margin is that equity less position margin and active
    reservations. A position without a mark adds no unrealized PnL and
    makes the valuation stale. Venue accounts use the venue's balance
    snapshot when one is given (LIVE risk checks) and are otherwise
    unknown, which the Risk Gateway treats as a block.

    Args:
        uow_factory: Unit of work factory.
        gateway: Market gateway.
        account: Account.
        mode: Order mode whose positions and reservations count.
        now_ms: Current time, epoch milliseconds.
        max_quote_age_ms: Oldest acceptable mark, in milliseconds.
        venue: Venue balances of a venue account, if fetched.

    Returns:
        Valuation; equity, free margin and gross notional are ``None``
        (and ``stale`` is True) for a venue account without ``venue``.
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
        if venue is None:
            return Valuation(None, None, None, open_positions, marks, True)
        return _venue_valuation(venue, reserved, open_positions, marks, stale)
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


def _venue_valuation(
    venue: trading.VenueAccountSnapshot,
    reserved: decimal.Decimal,
    open_positions: tuple[positions.Position, ...],
    marks: dict[str, decimal.Decimal | None],
    stale: bool,
) -> Valuation:
    """Values a venue account from the venue's own balances.

    Equity and free margin come from the venue (which already accounts for
    resting orders); all active local reservations are subtracted as well,
    so the estimate errs on the safe side. Gross notional uses local
    positions at marks.

    Args:
        venue: Venue balance snapshot.
        reserved: Active local margin reservations.
        open_positions: Local open positions.
        marks: Marks by instrument.
        stale: Whether any mark is already missing or stale.

    Returns:
        Valuation; gross notional is ``None`` and ``stale`` True when a
        mark is missing.
    """
    gross: decimal.Decimal | None = decimals.ZERO
    for pos in open_positions:
        mark = marks.get(pos.instrument_id)
        if mark is None or gross is None:
            gross = None
            stale = True
            continue
        gross += pos.quantity * mark
    return Valuation(
        venue.equity,
        venue.available_margin - reserved,
        gross,
        open_positions,
        marks,
        stale,
    )
