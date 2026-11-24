# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Accounts, negotiated trading capabilities and LIVE arming.

All times are UTC epoch milliseconds.
"""

from __future__ import annotations

import dataclasses
import decimal

from joseph_execution.domain import enums
from joseph_execution.domain import ids

LIVE_ARM_DEFAULT_MS = 8 * 3_600_000
"""Default lifetime (8 hours) of an account LIVE arming, in milliseconds.

Also the default of the configurable maximum lifetime.
"""

LIVE_ARM_CEILING_MS = 24 * 3_600_000
"""Hard ceiling (24 hours) of an arming lifetime; config cannot exceed it."""

LIVE_ARM_ACKNOWLEDGEMENT = (
    "Manual LIVE orders on this account use real money until the arming "
    "expires or is revoked."
)
"""Acknowledgement text recorded with every arming."""


@dataclasses.dataclass(frozen=True, slots=True)
class TradingCapabilities:
    """What a venue adapter can do for an account, negotiated at runtime.

    Attributes:
        venue: Venue code.
        status: Trading availability; LIVE orders need ``AVAILABLE``.
        order_types: Supported order types.
        time_in_force: Supported time-in-force values.
        native_reduce_only: Venue-native reduce-only orders.
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
        tradable_venues: Exchange venues a broker (or paper account)
            routes securities to; empty for crypto venues, which trade
            their own instruments.
        cash_account: Securities trade from cash (no leverage or margin).
        short_selling: Securities may be sold short.
        outside_rth: Orders may carry an outside-regular-hours flag.
        odd_lot: Intraday odd-lot (below one board lot) orders.
        queue_market_when_closed: DAY market orders placed while the
            exchange is closed queue for the next open.
        simulation: The account is a broker paper/simulation environment.
        currencies: Settlement currencies of the account.
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
    tradable_venues: tuple[str, ...] = ()
    cash_account: bool = False
    short_selling: bool = False
    outside_rth: bool = False
    odd_lot: bool = False
    queue_market_when_closed: bool = False
    simulation: bool = False
    currencies: tuple[str, ...] = ()

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
            "tradable_venues": list(self.tradable_venues),
            "cash_account": self.cash_account,
            "short_selling": self.short_selling,
            "outside_rth": self.outside_rth,
            "odd_lot": self.odd_lot,
            "queue_market_when_closed": self.queue_market_when_closed,
            "simulation": self.simulation,
            "currencies": list(self.currencies),
        }


@dataclasses.dataclass(frozen=True, slots=True)
class Account:
    """An account the tenant trades (paper, venue or broker).

    Attributes:
        account_id: Identifier.
        tenant_id: Owning tenant.
        venue: Venue or broker code (``mock`` for paper accounts).
        label: Display label.
        status: Connection status.
        modes: Order modes permitted for this account; LIVE orders need
            ``LIVE`` here (paper accounts are created without it).
        currency: Settlement currency.
        created_at_ms: Creation time.
        starting_equity: Paper starting equity (``None`` for venue
            accounts).
        is_paper: True for the simulated paper accounts.
        credential_id: Reference to the encrypted credential, if any.
        withdrawal_disabled: The user attested the key cannot withdraw.
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


def paper_account_id(tenant_id: str, currency: str = "USD") -> str:
    """Returns the deterministic paper account id of a tenant.

    Args:
        tenant_id: Tenant.
        currency: Paper account currency; ``USD`` is the default account.

    Returns:
        ``acct-paper-<tenant_id>`` for USD, else
        ``acct-<currency>-paper-<tenant_id>`` (e.g. ``acct-twd-paper-t1``).
    """
    if currency == "USD":
        return f"acct-paper-{tenant_id}"
    return f"acct-{currency.lower()}-paper-{tenant_id}"


def paper_currency(account_id: str) -> str:
    """Returns the currency encoded in a paper account id.

    Args:
        account_id: Paper account id from ``paper_account_id``.

    Returns:
        ``USD`` for ``acct-paper-<tenant>``, else the upper-cased
        ``<currency>`` of ``acct-<currency>-paper-<tenant>`` (e.g. ``TWD``).

    Raises:
        ValueError: When ``account_id`` is not a paper account id.
    """
    head, separator, tenant = account_id.partition("-paper-")
    if separator and tenant:
        if head == "acct":
            return "USD"
        code = head.removeprefix("acct-")
        if head.startswith("acct-") and code.isascii() and code.isalpha():
            return code.upper()
    raise ValueError(f"{account_id!r} is not a paper account id")


def live_arming_scope_hash(
    account: Account, granted_by: str, expires_at_ms: int
) -> str:
    """Hashes the scope that a LIVE arming covers.

    The arming is bound to one account of one tenant, its venue and stored
    credential, the granting actor and the expiry, so a record cannot be
    replayed onto another account or stretched in storage, and replacing
    the account's credential voids it.

    Args:
        account: Armed account.
        granted_by: Granting actor.
        expires_at_ms: Expiry (exclusive).

    Returns:
        ``sha256:<hex>`` scope hash.
    """
    scope = {
        "tenant_id": account.tenant_id,
        "account_id": account.account_id,
        "venue": account.venue,
        "credential_id": account.credential_id,
        "granted_by": granted_by,
        "expires_at_ms": expires_at_ms,
    }
    return "sha256:" + ids.sha256_hex(ids.canonical_json(scope))


@dataclasses.dataclass(frozen=True, slots=True)
class LiveArming:
    """Server-side, time-limited arming of manual LIVE orders on an account.

    Granted with a step-up code (the same verification as AUTO_LIVE
    deployment consent); the Risk Gateway refuses manual LIVE orders that
    would add exposure unless the arming's ``state`` is ``ARMED``.
    Risk-reducing orders pass without it.

    Attributes:
        tenant_id: Owning tenant.
        account_id: Armed account.
        granted_at_ms: When it was granted.
        expires_at_ms: When it lapses; it is expired from this instant on.
        granted_by: Granting actor.
        acknowledgement: Risk acknowledgement the actor accepted.
        scope_hash: ``live_arming_scope_hash`` binding the record to its
            account, credential, granting actor and expiry.
        revoked_at_ms: When it was revoked, if it was.
        revoked_by: Revoking actor (or ``system``), if revoked.
    """

    tenant_id: str
    account_id: str
    granted_at_ms: int
    expires_at_ms: int
    granted_by: str
    acknowledgement: str
    scope_hash: str
    revoked_at_ms: int | None = None
    revoked_by: str | None = None

    @classmethod
    def grant(
        cls,
        account: Account,
        granted_by: str,
        now_ms: int,
        duration_ms: int,
    ) -> LiveArming:
        """Creates an arming for an account.

        The caller has already verified the step-up code and the
        acknowledgement.

        Args:
            account: Account to arm.
            granted_by: Granting actor.
            now_ms: Grant time.
            duration_ms: Lifetime in milliseconds; the caller enforces the
                configured maximum.

        Returns:
            New, unrevoked arming expiring at ``now_ms + duration_ms``.
        """
        expires = now_ms + duration_ms
        return cls(
            tenant_id=account.tenant_id,
            account_id=account.account_id,
            granted_at_ms=now_ms,
            expires_at_ms=expires,
            granted_by=granted_by,
            acknowledgement=LIVE_ARM_ACKNOWLEDGEMENT,
            scope_hash=live_arming_scope_hash(account, granted_by, expires),
        )

    def state(self, account: Account, now_ms: int) -> str:
        """Describes the arming at a time.

        Revocation is checked first, then expiry, then scope: the arming
        must belong to ``account``'s tenant and id, and its scope hash must
        match the account as it is now (same venue and credential).

        Args:
            account: The account the arming is checked against.
            now_ms: Current time.

        Returns:
            ``REVOKED``, ``EXPIRED`` (``now_ms >= expires_at_ms``),
            ``OUT_OF_SCOPE`` or, when every check passes, ``ARMED``.
        """
        if self.revoked_at_ms is not None:
            return "REVOKED"
        if now_ms >= self.expires_at_ms:
            return "EXPIRED"
        expected = live_arming_scope_hash(
            account, self.granted_by, self.expires_at_ms
        )
        if (
            account.tenant_id != self.tenant_id
            or account.account_id != self.account_id
            or expected != self.scope_hash
        ):
            return "OUT_OF_SCOPE"
        return "ARMED"

    def valid_for(self, account: Account, now_ms: int) -> bool:
        """Checks that the arming is unrevoked, unexpired and in scope.

        Args:
            account: Account of the order.
            now_ms: Current time.

        Returns:
            True only when ``state`` is ``ARMED``.
        """
        return self.state(account, now_ms) == "ARMED"

    def revoke(self, actor: str, now_ms: int) -> LiveArming:
        """Returns a revoked copy (a revoked arming stays revoked).

        Args:
            actor: Revoking actor.
            now_ms: Revocation time.

        Returns:
            A copy revoked at ``now_ms`` by ``actor``; ``self`` unchanged
            when it was already revoked (the first revocation is kept).
        """
        if self.revoked_at_ms is not None:
            return self
        return dataclasses.replace(self, revoked_at_ms=now_ms, revoked_by=actor)

    def to_contract(self) -> dict[str, object]:
        """Serializes for audit payloads and storage.

        Returns:
            JSON-compatible dict.
        """
        return {
            "tenant_id": self.tenant_id,
            "account_id": self.account_id,
            "granted_at_ms": self.granted_at_ms,
            "expires_at_ms": self.expires_at_ms,
            "granted_by": self.granted_by,
            "acknowledgement": self.acknowledgement,
            "scope_hash": self.scope_hash,
            "revoked_at_ms": self.revoked_at_ms,
            "revoked_by": self.revoked_by,
        }
