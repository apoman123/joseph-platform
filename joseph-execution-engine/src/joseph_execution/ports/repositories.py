# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Persistence ports. One ``UnitOfWork`` is one ACID transaction.

Repositories are reached through a unit of work and are valid only inside
its ``with`` block. Times are UTC epoch milliseconds.
"""

from __future__ import annotations

import dataclasses
import decimal
import types
from collections.abc import Callable
from collections.abc import Mapping
from collections.abc import Sequence
from typing import Protocol
from typing import TypeAlias

from joseph_execution.domain import accounts
from joseph_execution.domain import audit
from joseph_execution.domain import deployments
from joseph_execution.domain import enums
from joseph_execution.domain import intents
from joseph_execution.domain import kill_switch
from joseph_execution.domain import orders
from joseph_execution.domain import positions
from joseph_execution.domain import risk


@dataclasses.dataclass(frozen=True, slots=True)
class StoredIntent:
    """An intent with its risk decision, as persisted.

    Attributes:
        intent: Immutable intent.
        decision: Risk decision taken when it was created.
        body_hash: ``OrderIntentRequest.body_hash`` of the request, compared
            when an idempotency key is reused.
    """

    intent: intents.OrderIntent
    decision: risk.RiskDecision
    body_hash: str


@dataclasses.dataclass(frozen=True, slots=True)
class OutboxItem:
    """A side effect toward a venue, recorded with the change that needs it.

    Attributes:
        outbox_id: Row id; increases in insertion order.
        aggregate_id: Order id.
        event_key: Unique key within the aggregate (e.g. ``submit``).
        kind: ``SUBMIT`` or ``RECONCILE``; the dispatcher reconciles
            every kind other than ``SUBMIT``.
        status: ``PENDING``, ``DONE``, ``MANUAL`` or ``FAILED``; only
            ``PENDING`` items are dispatched.
        attempts: Attempts so far.
        next_attempt_ms: Earliest time of the next attempt.
        last_error: Last error text, if any.
    """

    outbox_id: int
    aggregate_id: str
    event_key: str
    kind: str
    status: str
    attempts: int
    next_attempt_ms: int
    last_error: str | None


class AccountRepository(Protocol):
    """The store of accounts."""

    def get(self, account_id: str) -> accounts.Account | None:
        """Loads an account.

        Args:
            account_id: Account id.

        Returns:
            Account or ``None``.
        """
        ...

    def list_for_tenant(self, tenant_id: str) -> list[accounts.Account]:
        """Lists a tenant's accounts.

        Args:
            tenant_id: Tenant.

        Returns:
            Accounts.
        """
        ...

    def add(self, account: accounts.Account) -> None:
        """Inserts an account.

        Args:
            account: Account.
        """
        ...

    def save(self, account: accounts.Account) -> None:
        """Updates an account.

        Args:
            account: Account.
        """
        ...


class CredentialRepository(Protocol):
    """The store of encrypted credential envelopes (never plaintext)."""

    def add(
        self,
        credential_id: str,
        tenant_id: str,
        account_id: str,
        envelope: Mapping[str, str],
        created_at_ms: int,
    ) -> None:
        """Stores an envelope.

        Args:
            credential_id: Id.
            tenant_id: Tenant.
            account_id: Account.
            envelope: Fields from ``CredentialVault.seal``.
            created_at_ms: Time.
        """
        ...

    def get(self, credential_id: str) -> Mapping[str, str] | None:
        """Loads an envelope.

        Args:
            credential_id: Id.

        Returns:
            Envelope or ``None``.
        """
        ...

    def delete_for_account(self, account_id: str) -> int:
        """Crypto-shreds all envelopes of an account.

        Each envelope carries its own wrapped data key, so deleting it
        leaves nothing to decrypt.

        Args:
            account_id: Account.

        Returns:
            Rows deleted.
        """
        ...


class IntentRepository(Protocol):
    """The store of order intents (unique per tenant and idempotency key)."""

    def add(self, stored: StoredIntent) -> None:
        """Inserts an intent.

        Args:
            stored: Intent with decision.

        Raises:
            ExecutionError: With ``IDEMPOTENCY_CONFLICT`` when the tenant
                already has an intent with the same idempotency key; the
                OMS then replays the stored one.
        """
        ...

    def get(self, intent_id: str) -> StoredIntent | None:
        """Loads an intent.

        Args:
            intent_id: Intent id.

        Returns:
            Stored intent or ``None``.
        """
        ...

    def find_by_idempotency(
        self, tenant_id: str, key: str
    ) -> StoredIntent | None:
        """Finds an intent by idempotency key.

        Args:
            tenant_id: Tenant.
            key: Idempotency key.

        Returns:
            Stored intent or ``None``.
        """
        ...

    def count_for_signal(
        self, tenant_id: str, signal_id: str, deployment_id: str | None
    ) -> int:
        """Counts intents created for a signal and deployment.

        Args:
            tenant_id: Tenant.
            signal_id: Signal.
            deployment_id: Deployment; ``None`` counts intents without one.

        Returns:
            Count.
        """
        ...


class OrderRepository(Protocol):
    """The store of orders (unique per account and client order id)."""

    def add(self, order: orders.Order) -> None:
        """Inserts an order.

        Args:
            order: Order.

        Raises:
            ExecutionError: With ``DUPLICATE_INTENT`` when the account
                already has an order with the same client order id.
        """
        ...

    def save(self, order: orders.Order, expected_version: int) -> None:
        """Updates an order with optimistic concurrency.

        The order's own ``version`` was bumped by its mutation; the row is
        written only if it still holds ``expected_version``.

        Args:
            order: Mutated order.
            expected_version: Version the caller loaded, before mutating.

        Raises:
            ExecutionError: With ``CONFLICT`` when the stored row changed
                since it was loaded.
        """
        ...

    def get(self, order_id: str) -> orders.Order | None:
        """Loads an order.

        Args:
            order_id: Order id.

        Returns:
            Order or ``None``.
        """
        ...

    def get_by_client_id(
        self, account_id: str, client_order_id: str
    ) -> orders.Order | None:
        """Loads an order by client id.

        Args:
            account_id: Account.
            client_order_id: Client id.

        Returns:
            Order or ``None``.
        """
        ...

    def list(  # noqa: PLR0917 - optional query filters mirrored by every implementation; callers pass them by keyword.
        self,
        tenant_id: str | None = None,
        account_id: str | None = None,
        states: Sequence[enums.OrderState] | None = None,
        intent_id: str | None = None,
        parent_order_id: str | None = None,
        limit: int = 500,
    ) -> list[orders.Order]:
        """Lists orders, newest first.

        Every filter left ``None`` matches all orders.

        Args:
            tenant_id: Tenant filter (``None`` only for system workers).
            account_id: Account filter.
            states: State filter; empty also matches every state.
            intent_id: Intent filter.
            parent_order_id: Parent filter (protective child orders).
            limit: Maximum rows.

        Returns:
            Orders, newest first by creation time.
        """
        ...


class FillRepository(Protocol):
    """The store of fills (unique per account and venue fill id)."""

    def add(self, fill: orders.Fill, tenant_id: str) -> bool:
        """Inserts a fill.

        Args:
            fill: Fill.
            tenant_id: Tenant.

        Returns:
            True when inserted; False when the venue fill id was already
            recorded (nothing changes).
        """
        ...

    def list(
        self,
        tenant_id: str,
        account_id: str | None = None,
        order_id: str | None = None,
        limit: int = 500,
    ) -> list[orders.Fill]:
        """Lists fills, newest first.

        Args:
            tenant_id: Tenant.
            account_id: Account filter.
            order_id: Order filter.
            limit: Maximum rows.

        Returns:
            Fills.
        """
        ...

    def realized_since(self, account_id: str, since_ms: int) -> decimal.Decimal:
        """Sums realized PnL (profit and loss) of fills since a time.

        Args:
            account_id: Account.
            since_ms: Earliest fill time included.

        Returns:
            Realized PnL of the account's fills at or after ``since_ms``;
            zero when there are none.
        """
        ...


class PositionRepository(Protocol):
    """The store of positions (one per account, instrument and mode)."""

    def get(
        self, account_id: str, instrument_id: str, mode: enums.OrderMode
    ) -> positions.Position | None:
        """Loads a position.

        Args:
            account_id: Account.
            instrument_id: Instrument.
            mode: Mode.

        Returns:
            Position or ``None``.
        """
        ...

    def get_by_id(self, position_id: str) -> positions.Position | None:
        """Loads a position by id.

        Args:
            position_id: Id.

        Returns:
            Position or ``None``.
        """
        ...

    def save(self, position: positions.Position) -> None:
        """Upserts a position.

        Args:
            position: Position.
        """
        ...

    def list(
        self,
        tenant_id: str | None = None,
        account_id: str | None = None,
        include_flat: bool = False,
    ) -> list[positions.Position]:
        """Lists positions.

        Args:
            tenant_id: Tenant filter (``None`` only for system workers).
            account_id: Account filter.
            include_flat: Include zero positions.

        Returns:
            Positions.
        """
        ...


class ReservationRepository(Protocol):
    """The store of margin reservations for working orders."""

    def add(  # noqa: PLR0917 - one reservation row's fields; the OMS passes them positionally.
        self,
        reservation_id: str,
        tenant_id: str,
        account_id: str,
        intent_id: str,
        order_id: str,
        amount: decimal.Decimal,
        created_at_ms: int,
        mode: enums.OrderMode,
    ) -> None:
        """Creates an active reservation.

        Args:
            reservation_id: Id.
            tenant_id: Tenant.
            account_id: Account.
            intent_id: Intent.
            order_id: Order.
            amount: Reserved margin (>= 0).
            created_at_ms: Time.
            mode: Order mode the margin belongs to.

        Raises:
            ValueError: If ``amount`` is negative.
        """
        ...

    def release_for_order(self, order_id: str, now_ms: int) -> int:
        """Releases the reservation of an order.

        Args:
            order_id: Order.
            now_ms: Time.

        Returns:
            Rows released; 0 when the order had no active reservation.
        """
        ...

    def active_total(
        self, account_id: str, mode: enums.OrderMode | None = None
    ) -> decimal.Decimal:
        """Sums active reservations.

        Args:
            account_id: Account.
            mode: Mode filter; ``None`` sums every mode.

        Returns:
            Total (never negative).
        """
        ...

    def active_for_order(self, order_id: str) -> decimal.Decimal | None:
        """Returns the active reservation of an order.

        Args:
            order_id: Order.

        Returns:
            Amount, or ``None`` when the order has no active reservation.
        """
        ...


class OutboxRepository(Protocol):
    """The transactional outbox of venue side effects.

    Items are written in the same transaction as the state change that
    needs them and dispatched afterwards, so a crash between the two never
    loses a side effect.
    """

    def add(
        self, aggregate_id: str, event_key: str, kind: str, now_ms: int
    ) -> bool:
        """Adds a pending item (idempotent on aggregate and key).

        Args:
            aggregate_id: Order id.
            event_key: Key, unique within the aggregate.
            kind: ``OutboxItem.kind``.
            now_ms: Time; the item is due at once.

        Returns:
            True when added; False if the item already existed.
        """
        ...

    def due(self, now_ms: int, limit: int = 50) -> list[OutboxItem]:
        """Lists pending items whose next attempt is due.

        Args:
            now_ms: Time.
            limit: Maximum items.

        Returns:
            ``PENDING`` items with ``next_attempt_ms <= now_ms``, oldest
            first.
        """
        ...

    def update(  # noqa: PLR0917 - the item's new status, attempts, schedule and error; the OMS passes them positionally.
        self,
        outbox_id: int,
        status: str,
        attempts: int,
        next_attempt_ms: int,
        last_error: str | None,
        kind: str | None = None,
    ) -> None:
        """Updates an item.

        Args:
            outbox_id: Row id.
            status: New status.
            attempts: Attempts so far.
            next_attempt_ms: Earliest time of the next attempt.
            last_error: Error text, or ``None``.
            kind: New kind, or ``None`` to keep it.
        """
        ...

    def for_aggregate(self, aggregate_id: str) -> list[OutboxItem]:
        """Lists items of an aggregate.

        Args:
            aggregate_id: Order id.

        Returns:
            Items, oldest first.
        """
        ...


class AuditRepository(Protocol):
    """The append-only audit store (one hash chain per tenant)."""

    def last_hash(self, tenant_id: str) -> str:
        """Returns the latest chain hash of a tenant.

        Args:
            tenant_id: Tenant.

        Returns:
            Hash of the tenant's last record, or ``audit.GENESIS_HASH``
            when it has none.
        """
        ...

    def append(self, event: audit.AuditEvent) -> None:
        """Appends a record; records are never updated or deleted.

        Args:
            event: Record chained to ``last_hash`` in the same unit of work.
        """
        ...

    def chain(self, tenant_id: str) -> list[audit.AuditEvent]:
        """Returns the whole tenant chain in append order.

        Args:
            tenant_id: Tenant.

        Returns:
            Records.
        """
        ...

    def list(
        self, tenant_id: str, account_id: str | None = None, limit: int = 200
    ) -> list[audit.AuditEvent]:
        """Lists records, newest first.

        Args:
            tenant_id: Tenant.
            account_id: Account filter.
            limit: Maximum rows.

        Returns:
            Records.
        """
        ...


class KillSwitchRepository(Protocol):
    """The store of kill switch states."""

    def upsert(self, switch: kill_switch.KillSwitch) -> None:
        """Saves a switch, replacing the one of the same scope and target.

        Args:
            switch: State.
        """
        ...

    def list(self, tenant_id: str) -> list[kill_switch.KillSwitch]:
        """Lists a tenant's switches.

        Args:
            tenant_id: Tenant.

        Returns:
            The tenant's switches, engaged or not.
        """
        ...


class RiskLimitRepository(Protocol):
    """The store of user-configured risk limits per account."""

    def get(self, tenant_id: str, account_id: str) -> dict[str, str] | None:
        """Loads limits.

        Args:
            tenant_id: Tenant.
            account_id: Account.

        Returns:
            Stored limits by name, as decimal strings, or ``None`` when the
            account has none.
        """
        ...

    def put(
        self,
        tenant_id: str,
        account_id: str,
        limits: Mapping[str, str],
        now_ms: int,
    ) -> None:
        """Stores limits, replacing the account's previous ones.

        Args:
            tenant_id: Tenant.
            account_id: Account.
            limits: Limits by name, as decimal strings.
            now_ms: Time.
        """
        ...


class DeploymentRepository(Protocol):
    """The store of strategy deployments."""

    def add(self, deployment: deployments.Deployment) -> None:
        """Inserts a deployment.

        Args:
            deployment: Deployment.
        """
        ...

    def save(self, deployment: deployments.Deployment) -> None:
        """Updates a deployment.

        Args:
            deployment: Deployment.
        """
        ...

    def get(self, deployment_id: str) -> deployments.Deployment | None:
        """Loads a deployment.

        Args:
            deployment_id: Id.

        Returns:
            Deployment or ``None``.
        """
        ...

    def list(
        self, tenant_id: str, strategy_version_id: str | None = None
    ) -> list[deployments.Deployment]:
        """Lists deployments.

        Args:
            tenant_id: Tenant.
            strategy_version_id: Strategy version filter; ``None`` lists
                all of the tenant's deployments.

        Returns:
            Deployments.
        """
        ...


class LiveArmingRepository(Protocol):
    """The store of each account's current LIVE arming (one per account)."""

    def get(
        self, tenant_id: str, account_id: str
    ) -> accounts.LiveArming | None:
        """Loads the current arming of an account.

        Args:
            tenant_id: Tenant (foreign tenants never see a record).
            account_id: Account.

        Returns:
            Arming (possibly expired or revoked) or ``None``.
        """
        ...

    def put(self, arming: accounts.LiveArming) -> None:
        """Stores an arming, replacing the account's previous one.

        Args:
            arming: Arming.
        """
        ...


class UnitOfWork(Protocol):
    """A transaction boundary exposing every repository.

    Use one per ``with`` block; work done inside commits together or not at
    all.
    """

    @property
    def accounts(self) -> AccountRepository:
        """The accounts repository."""
        ...

    @property
    def credentials(self) -> CredentialRepository:
        """The credentials repository."""
        ...

    @property
    def intents(self) -> IntentRepository:
        """The intents repository."""
        ...

    @property
    def orders(self) -> OrderRepository:
        """The orders repository."""
        ...

    @property
    def fills(self) -> FillRepository:
        """The fills repository."""
        ...

    @property
    def positions(self) -> PositionRepository:
        """The positions repository."""
        ...

    @property
    def reservations(self) -> ReservationRepository:
        """The reservations repository."""
        ...

    @property
    def outbox(self) -> OutboxRepository:
        """The outbox repository."""
        ...

    @property
    def audit(self) -> AuditRepository:
        """The audit repository."""
        ...

    @property
    def kill_switches(self) -> KillSwitchRepository:
        """The kill switches repository."""
        ...

    @property
    def risk_limits(self) -> RiskLimitRepository:
        """The risk limits repository."""
        ...

    @property
    def deployments(self) -> DeploymentRepository:
        """The deployments repository."""
        ...

    @property
    def live_arming(self) -> LiveArmingRepository:
        """The account LIVE arming repository."""
        ...

    def __enter__(self) -> UnitOfWork:
        """Begins the transaction."""
        ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: types.TracebackType | None,
    ) -> None:
        """Commits on success, rolls back on error; never suppresses it.

        Args:
            exc_type: Exception type.
            exc: Exception.
            tb: Traceback.
        """
        ...


UnitOfWorkFactory: TypeAlias = Callable[[], UnitOfWork]
"""Creates a new unit of work (transaction)."""
