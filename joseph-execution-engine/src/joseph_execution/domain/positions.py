# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""One-way netted positions derived from fills.

PnL (profit and loss) is price difference times base quantity, so it is in
the instrument's quote currency.
"""

from __future__ import annotations

import dataclasses
import decimal

from joseph_execution.domain import decimals
from joseph_execution.domain import enums
from joseph_execution.domain import ids


def position_id(account_id: str, instrument_id: str, mode: str) -> str:
    """Returns the deterministic id of a position.

    Args:
        account_id: Account.
        instrument_id: Instrument.
        mode: Order mode value; each mode has its own position.

    Returns:
        Stable id like ``pos_3f1c2a9b0d4e5f60``.
    """
    return ids.stable_id("pos", account_id, instrument_id, mode)


@dataclasses.dataclass(slots=True)
class Position:
    """A net position; ``signed_qty`` > 0 is long, < 0 short.

    Mutable: ``apply_fill`` updates it in place.

    Attributes:
        position_id: Deterministic id (``position_id``).
        tenant_id: Owning tenant.
        account_id: Account.
        instrument_id: Instrument.
        mode: Order mode (positions never mix modes).
        signed_qty: Signed base quantity.
        entry_price: Average entry price of the open quantity; ``None``
            when flat.
        realized_pnl: Cumulative realized PnL net of fees.
        fees_paid: Cumulative fees.
        leverage: Leverage of the latest order that opened or increased it.
        updated_at_ms: Last change, UTC epoch milliseconds; never moves
            backwards.
        unknown: True when local state disagrees with the venue.
    """

    position_id: str
    tenant_id: str
    account_id: str
    instrument_id: str
    mode: enums.OrderMode
    signed_qty: decimal.Decimal = decimals.ZERO
    entry_price: decimal.Decimal | None = None
    realized_pnl: decimal.Decimal = decimals.ZERO
    fees_paid: decimal.Decimal = decimals.ZERO
    leverage: decimal.Decimal = decimals.ONE
    updated_at_ms: int = 0
    unknown: bool = False

    @property
    def side(self) -> str:
        """The side label: ``long``, ``short`` or ``flat``."""
        if self.signed_qty > decimals.ZERO:
            return "long"
        if self.signed_qty < decimals.ZERO:
            return "short"
        return "flat"

    @property
    def quantity(self) -> decimal.Decimal:
        """The absolute position size in base units."""
        return abs(self.signed_qty)

    def apply_fill(  # noqa: PLR0917 - one fill's side, quantity, price, fee and time; callers pass them positionally.
        self,
        side: enums.OrderSide,
        quantity: decimal.Decimal,
        price: decimal.Decimal,
        fee: decimal.Decimal,
        now_ms: int,
        leverage: decimal.Decimal | None = None,
    ) -> decimal.Decimal:
        """Nets a fill into the position, in place.

        A fill on the position's side (or into a flat position) increases
        it at the size-weighted average entry price. An opposite fill
        reduces it and realizes PnL on the closed quantity at the old entry
        price; any remainder past zero opens the other side at the fill
        price.

        Args:
            side: Fill side.
            quantity: Base quantity (positive).
            price: Fill price.
            fee: Fee charged for the fill (positive = paid).
            now_ms: Fill time.
            leverage: Leverage of the order, recorded when the fill opens or
                increases the position; ``None`` keeps the current value.

        Returns:
            PnL realized by this fill, net of its fee.
        """
        delta = quantity if side is enums.OrderSide.BUY else -quantity
        realized = -fee
        current = self.signed_qty
        if current == decimals.ZERO or (current > 0) == (delta > 0):
            cost = (self.entry_price or decimals.ZERO) * abs(current)
            new_qty = current + delta
            self.entry_price = (cost + price * quantity) / abs(new_qty)
            self.signed_qty = new_qty
            if leverage is not None:
                self.leverage = leverage
        else:
            closing = min(abs(current), quantity)
            entry = self.entry_price or price
            direction = decimals.ONE if current > 0 else -decimals.ONE
            realized += (price - entry) * closing * direction
            remainder = quantity - closing
            new_qty = current + delta
            self.signed_qty = new_qty
            if new_qty == decimals.ZERO:
                self.entry_price = None
            elif remainder > decimals.ZERO:
                self.entry_price = price
                if leverage is not None:
                    self.leverage = leverage
        self.realized_pnl += realized
        self.fees_paid += fee
        self.updated_at_ms = max(self.updated_at_ms, now_ms)
        return realized

    def unrealized_pnl(
        self, mark: decimal.Decimal | None
    ) -> decimal.Decimal | None:
        """Computes unrealized PnL at ``mark``.

        Args:
            mark: Mark price or ``None``.

        Returns:
            Unrealized PnL; zero for a flat position, ``None`` for an open
            one without a mark or entry price.
        """
        if mark is None or self.entry_price is None:
            return None if self.signed_qty else decimals.ZERO
        return (mark - self.entry_price) * self.signed_qty

    def margin(self) -> decimal.Decimal:
        """Returns the margin in use: entry notional / leverage, 0 when flat."""
        if self.entry_price is None:
            return decimals.ZERO
        return self.entry_price * self.quantity / self.leverage

    def to_contract(
        self, mark: decimal.Decimal | None, now_ms: int, stale: bool
    ) -> dict[str, object]:
        """Serializes to the contract ``Position``.

        Args:
            mark: Mark price for unrealized PnL, if known.
            now_ms: Snapshot time.
            stale: Whether the mark is stale; an ``unknown`` position is
                reported stale either way.

        Returns:
            JSON-compatible dict.
        """
        return {
            "position_id": self.position_id,
            "account_id": self.account_id,
            "instrument_id": self.instrument_id,
            "side": self.side,
            "quantity_base": decimals.fmt(self.quantity),
            "entry_price": decimals.fmt_optional(self.entry_price),
            "mark_price": decimals.fmt_optional(mark),
            "unrealized_pnl": decimals.fmt_optional(self.unrealized_pnl(mark)),
            "realized_pnl": decimals.fmt(self.realized_pnl),
            # Liquidation price and funding are not tracked locally.
            "liquidation_price": None,
            "margin": decimals.fmt(self.margin()),
            "leverage": decimals.fmt(self.leverage),
            "funding_accrued": None,
            "mode": self.mode.value,
            "stale": stale or self.unknown,
            "as_of_ms": now_ms,
        }
