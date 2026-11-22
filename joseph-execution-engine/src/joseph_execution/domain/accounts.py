"""Accounts and negotiated trading capabilities."""

from __future__ import annotations

import dataclasses
import decimal

from joseph_execution.domain import enums


@dataclasses.dataclass(frozen=True, slots=True)
class TradingCapabilities:
    """What a venue adapter can do for an account (runtime negotiated).

    Attributes:
        venue: Venue code.
        status: Trading availability.
        order_types: Supported order types.
        time_in_force: Supported time-in-force values.
        native_reduce_only: Venue-native reduce-only.
        native_stop_loss: Venue-held stop-loss triggers.
        native_take_profit: Venue-held take-profit orders.
        native_oco: Venue-native one-cancels-other.
        amend: Order amendment supported.
        batch: Batch submission supported.
        position_modes: ``ONE_WAY`` and/or ``HEDGE``.
        margin_modes: Supported margin modes.
        client_order_id: Client order ids supported.
        query_by_client_id: Orders can be queried by client id.
        notes: Caveats shown to users.
    """

    venue: str
    status: enums.TradingStatus
    order_types: tuple[enums.OrderType, ...]
    time_in_force: tuple[enums.TimeInForce, ...]
    native_reduce_only: bool
    native_stop_loss: bool
    native_take_profit: bool
    native_oco: bool
    amend: bool
    batch: bool
    position_modes: tuple[str, ...]
    margin_modes: tuple[enums.MarginMode, ...]
    client_order_id: bool
    query_by_client_id: bool
    notes: tuple[str, ...] = ()

    def to_contract(self) -> dict[str, object]:
        """Serializes to the contract ``TradingCapabilities``.

        Returns:
            JSON-compatible dict.
        """
        return {
            "venue": self.venue,
            "status": self.status.value,
            "order_types": [t.value for t in self.order_types],
            "time_in_force": [t.value for t in self.time_in_force],
            "native_reduce_only": self.native_reduce_only,
            "native_stop_loss": self.native_stop_loss,
            "native_take_profit": self.native_take_profit,
            "native_oco": self.native_oco,
            "amend": self.amend,
            "batch": self.batch,
            "position_modes": list(self.position_modes),
            "margin_modes": [m.value for m in self.margin_modes],
            "client_order_id": self.client_order_id,
            "query_by_client_id": self.query_by_client_id,
            "notes": list(self.notes),
        }


@dataclasses.dataclass(frozen=True, slots=True)
class Account:
    """An account the tenant trades (paper or venue).

    Attributes:
        account_id: Identifier.
        tenant_id: Owning tenant.
        venue: Venue code (``mock`` for paper).
        label: Display label.
        status: Connection status.
        modes: Order modes permitted for this account.
        currency: Settlement currency.
        created_at_ms: Creation time.
        starting_equity: Paper starting equity (``None`` for venue).
        is_paper: True for the simulated paper account.
        credential_id: Encrypted credential reference, if any.
        withdrawal_disabled: User attested the key cannot withdraw.
    """

    account_id: str
    tenant_id: str
    venue: str
    label: str
    status: enums.AccountStatus
    modes: tuple[enums.OrderMode, ...]
    currency: str
    created_at_ms: int
    starting_equity: decimal.Decimal | None = None
    is_paper: bool = False
    credential_id: str | None = None
    withdrawal_disabled: bool = False


def paper_account_id(tenant_id: str) -> str:
    """Returns the deterministic paper account id of a tenant.

    Args:
        tenant_id: Tenant.

    Returns:
        ``acct-paper-<tenant_id>``.
    """
    return f"acct-paper-{tenant_id}"
