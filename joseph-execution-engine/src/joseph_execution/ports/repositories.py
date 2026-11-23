"""Persistence ports. One ``UnitOfWork`` is one ACID transaction."""

from __future__ import annotations

import dataclasses
import decimal
import types
from collections.abc import Callable
from collections.abc import Mapping
from collections.abc import Sequence
from typing import Protocol

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
    """An intent with its risk decision as persisted.

    Attributes:
        intent: Immutable intent.
        decision: Risk decision taken when it was created.
        body_hash: Hash of the request body (idempotency comparison).
    """

    intent: intents.OrderIntent
    decision: risk.RiskDecision
    body_hash: str


@dataclasses.dataclass(frozen=True, slots=True)
class OutboxItem:
    """Pending side effect toward a venue.

    Attributes:
        outbox_id: Row id.
        aggregate_id: Order id.
        event_key: Unique key within the aggregate (e.g. ``submit``).
        kind: ``SUBMIT``, ``RECONCILE`` or ``CANCEL``.
        status: ``PENDING``, ``DONE``, ``MANUAL`` or ``FAILED``.
        attempts: Attempts so far.
        next_attempt_ms: Earliest next attempt.
        last_error: Last error text.
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
    """Accounts."""

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
    """Encrypted credential envelopes."""

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
            envelope: Sealed fields.
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

        Args:
            account_id: Account.

        Returns:
            Rows deleted.
        """
        ...


class IntentRepository(Protocol):
    """Order intents (unique per tenant and idempotency key)."""

    def add(self, stored: StoredIntent) -> None:
        """Inserts an intent.

        Args:
            stored: Intent with decision.
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
            deployment_id: Deployment (``None`` for manual intents).

        Returns:
            Count.
        """
        ...


class OrderRepository(Protocol):
    """Orders (unique per account and client order id)."""

    def add(self, order: orders.Order) -> None:
        """Inserts an order.

        Args:
            order: Order.
        """
        ...

    def save(self, order: orders.Order, expected_version: int) -> None:
        """Updates an order with optimistic concurrency.

        Args:
            order: Mutated order.
            expected_version: Version the caller loaded.
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

    def list(  # noqa: PLR0917
        self,
        tenant_id: str | None = None,
        account_id: str | None = None,
        states: Sequence[enums.OrderState] | None = None,
        intent_id: str | None = None,
        parent_order_id: str | None = None,
        limit: int = 500,
    ) -> list[orders.Order]:
        """Lists orders, newest first.

        Args:
            tenant_id: Tenant filter (``None`` only for system workers).
            account_id: Account filter.
            states: State filter.
            intent_id: Intent filter.
            parent_order_id: Parent filter.
            limit: Maximum rows.

        Returns:
            Orders.
        """
        ...


class FillRepository(Protocol):
    """Fills (unique per account and venue fill id)."""

    def add(self, fill: orders.Fill, tenant_id: str) -> bool:
        """Inserts a fill.

        Args:
            fill: Fill.
            tenant_id: Tenant.

        Returns:
            False when the venue fill id was already recorded.
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
        """Sums realized PnL of fills since a time.

        Args:
            account_id: Account.
            since_ms: Lower bound.

        Returns:
            Realized PnL.
        """
        ...


class PositionRepository(Protocol):
    """Positions."""

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
            tenant_id: Tenant filter.
            account_id: Account filter.
            include_flat: Include zero positions.

        Returns:
            Positions.
        """
        ...


class ReservationRepository(Protocol):
    """Margin reservations for working orders."""

    def add(  # noqa: PLR0917
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
        """
        ...

    def release_for_order(self, order_id: str, now_ms: int) -> int:
        """Releases the reservation of an order.

        Args:
            order_id: Order.
            now_ms: Time.

        Returns:
            Rows released.
        """
        ...

    def active_total(
        self, account_id: str, mode: enums.OrderMode | None = None
    ) -> decimal.Decimal:
        """Sums active reservations.

        Args:
            account_id: Account.
            mode: Optional mode filter.

        Returns:
            Total (never negative).
        """
        ...

    def active_for_order(self, order_id: str) -> decimal.Decimal | None:
        """Returns the active reservation of an order.

        Args:
            order_id: Order.

        Returns:
            Amount or ``None``.
        """
        ...


class OutboxRepository(Protocol):
    """Transactional outbox for venue side effects."""

    def add(
        self, aggregate_id: str, event_key: str, kind: str, now_ms: int
    ) -> bool:
        """Adds an item (idempotent on aggregate and key).

        Args:
            aggregate_id: Order id.
            event_key: Key.
            kind: Kind.
            now_ms: Time.

        Returns:
            False if it already existed.
        """
        ...

    def due(self, now_ms: int, limit: int = 50) -> list[OutboxItem]:
        """Lists pending items whose next attempt is due.

        Args:
            now_ms: Time.
            limit: Maximum.

        Returns:
            Items.
        """
        ...

    def update(  # noqa: PLR0917
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
            attempts: Attempts.
            next_attempt_ms: Next attempt.
            last_error: Error text.
            kind: Optional new kind.
        """
        ...

    def for_aggregate(self, aggregate_id: str) -> list[OutboxItem]:
        """Lists items of an aggregate.

        Args:
            aggregate_id: Order id.

        Returns:
            Items.
        """
        ...


class AuditRepository(Protocol):
    """Append-only audit store."""

    def last_hash(self, tenant_id: str) -> str:
        """Returns the latest chain hash of a tenant.

        Args:
            tenant_id: Tenant.

        Returns:
            Hash or the genesis hash.
        """
        ...

    def append(self, event: audit.AuditEvent) -> None:
        """Appends a record.

        Args:
            event: Record.
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
    """Kill switch states."""

    def upsert(self, switch: kill_switch.KillSwitch) -> None:
        """Saves a switch.

        Args:
            switch: State.
        """
        ...

    def list(self, tenant_id: str) -> list[kill_switch.KillSwitch]:
        """Lists a tenant's switches.

        Args:
            tenant_id: Tenant.

        Returns:
            Switches.
        """
        ...


class RiskLimitRepository(Protocol):
    """User-configured risk limits per account."""

    def get(self, tenant_id: str, account_id: str) -> dict[str, str] | None:
        """Loads limits.

        Args:
            tenant_id: Tenant.
            account_id: Account.

        Returns:
            Limits as decimal strings, or ``None``.
        """
        ...

    def put(
        self,
        tenant_id: str,
        account_id: str,
        limits: Mapping[str, str],
        now_ms: int,
    ) -> None:
        """Stores limits.

        Args:
            tenant_id: Tenant.
            account_id: Account.
            limits: Limits.
            now_ms: Time.
        """
        ...


class DeploymentRepository(Protocol):
    """Strategy deployments."""

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
            strategy_version_id: Optional filter.

        Returns:
            Deployments.
        """
        ...


class LiveArmingRepository(Protocol):
    """Current LIVE arming of each account (one row per tenant account)."""

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
    """Transaction boundary exposing every repository."""

    @property
    def accounts(self) -> AccountRepository:
        """Returns the accounts repository."""
        ...

    @property
    def credentials(self) -> CredentialRepository:
        """Returns the credentials repository."""
        ...

    @property
    def intents(self) -> IntentRepository:
        """Returns the intents repository."""
        ...

    @property
    def orders(self) -> OrderRepository:
        """Returns the orders repository."""
        ...

    @property
    def fills(self) -> FillRepository:
        """Returns the fills repository."""
        ...

    @property
    def positions(self) -> PositionRepository:
        """Returns the positions repository."""
        ...

    @property
    def reservations(self) -> ReservationRepository:
        """Returns the reservations repository."""
        ...

    @property
    def outbox(self) -> OutboxRepository:
        """Returns the outbox repository."""
        ...

    @property
    def audit(self) -> AuditRepository:
        """Returns the audit repository."""
        ...

    @property
    def kill_switches(self) -> KillSwitchRepository:
        """Returns the kill switches repository."""
        ...

    @property
    def risk_limits(self) -> RiskLimitRepository:
        """Returns the risk limits repository."""
        ...

    @property
    def deployments(self) -> DeploymentRepository:
        """Returns the deployments repository."""
        ...

    @property
    def live_arming(self) -> LiveArmingRepository:
        """Returns the account LIVE arming repository."""
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
        """Commits on success, rolls back on error.

        Args:
            exc_type: Exception type.
            exc: Exception.
            tb: Traceback.
        """
        ...


UnitOfWorkFactory = Callable[[], UnitOfWork]
"""Creates a new unit of work (transaction)."""
