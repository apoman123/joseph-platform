# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""SQLAlchemy Core implementation of the persistence ports.

Decimals are stored as exact text and parsed back with ``decimals``; JSON
columns hold contract payloads. Persisted column names are part of the
schema and never change here.
"""

from __future__ import annotations

import decimal
import json
import pathlib
import threading
import types
from collections.abc import Mapping
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import exc as sa_exc
from sqlalchemy import pool as sa_pool

from joseph_execution.adapters.persistence import schema
from joseph_execution.domain import accounts
from joseph_execution.domain import audit
from joseph_execution.domain import decimals
from joseph_execution.domain import deployments
from joseph_execution.domain import enums
from joseph_execution.domain import errors
from joseph_execution.domain import intents
from joseph_execution.domain import kill_switch
from joseph_execution.domain import orders
from joseph_execution.domain import positions
from joseph_execution.domain import risk
from joseph_execution.ports import repositories

# Short alias: every row mapper parses several stored decimals.
_D = decimals.to_decimal


def _opt_d(value: object) -> decimal.Decimal | None:
    """Parses an optional stored decimal.

    Args:
        value: Stored text or ``None``.

    Returns:
        Decimal or ``None``.
    """
    return None if value is None else _D(value)


_SQLITE_BUSY_TIMEOUT_MS = 10_000
"""How long a SQLite writer waits for another writer before failing."""


def _enable_sqlite_wal(engine: sa.Engine) -> None:
    """Configures every file-based SQLite connection for concurrent access.

    The default rollback journal lets one connection's read transaction block
    another connection's commit, and commits run on the event loop, so a
    blocked commit freezes every request. WAL lets readers and the single
    writer proceed concurrently, and ``busy_timeout`` makes writers wait
    instead of failing.

    Args:
        engine: SQLite engine backed by a file.
    """

    @sa.event.listens_for(engine, "connect")
    def _on_connect(dbapi_connection: object, _record: object) -> None:
        """Applies the pragmas to a new DB-API connection.

        Args:
            dbapi_connection: Raw sqlite3 connection.
            _record: Pool record (unused).
        """
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]  # SQLAlchemy types the DB-API connection as object; sqlite3's has cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute(f"PRAGMA busy_timeout={_SQLITE_BUSY_TIMEOUT_MS}")
        cursor.close()


def create_engine(url: str) -> sa.Engine:
    """Creates an engine and the schema.

    In-memory SQLite uses a single shared connection so every unit of work
    sees the same database. File-based SQLite runs in WAL mode, and on
    SQLite the audit table gets triggers that make it append-only.

    Args:
        url: SQLAlchemy URL, e.g. ``sqlite:///./var/execution.db``.

    Returns:
        Engine with tables created.
    """
    if url.startswith("sqlite"):
        if url in ("sqlite://", "sqlite:///:memory:"):
            engine = sa.create_engine(
                "sqlite://",
                poolclass=sa_pool.StaticPool,
                connect_args={"check_same_thread": False},
            )
        else:
            path = url.removeprefix("sqlite:///")
            pathlib.Path(path).parent.mkdir(parents=True, exist_ok=True)
            engine = sa.create_engine(
                url, connect_args={"check_same_thread": False}
            )
            _enable_sqlite_wal(engine)
    else:
        engine = sa.create_engine(url, pool_pre_ping=True)
    schema.METADATA.create_all(engine)
    if engine.dialect.name == "sqlite":
        with engine.begin() as conn:
            for statement in schema.SQLITE_AUDIT_TRIGGERS:
                conn.exec_driver_sql(statement)
    return engine


class SqlUnitOfWorkFactory:
    """Creates SQL units of work that one process-wide lock serializes.

    The lock is used for every engine; it matters most for SQLite, which
    allows a single writer.
    """

    def __init__(self, engine: sa.Engine) -> None:
        """Initializes the factory.

        Args:
            engine: Engine from ``create_engine``.
        """
        self._engine = engine
        self._lock = threading.RLock()

    def __call__(self) -> SqlUnitOfWork:
        """Returns a new unit of work.

        Returns:
            Unit of work (use as a context manager).
        """
        return SqlUnitOfWork(self._engine, self._lock)


class _Repo:
    """Base class holding the transaction connection."""

    def __init__(self, uow: SqlUnitOfWork) -> None:
        """Initializes the repository.

        Args:
            uow: Owning unit of work.
        """
        self._uow = uow

    @property
    def conn(self) -> sa.Connection:
        """The owning unit of work's active connection."""
        return self._uow.connection


def _account_from_row(row: sa.Row[object]) -> accounts.Account:
    """Maps an ``accounts`` row to an account.

    Args:
        row: Row.

    Returns:
        Account.
    """
    columns = row._mapping
    return accounts.Account(
        account_id=columns["account_id"],
        tenant_id=columns["tenant_id"],
        venue=columns["venue"],
        label=columns["label"],
        status=enums.AccountStatus(columns["status"]),
        modes=tuple(enums.OrderMode(x) for x in json.loads(columns["modes"])),
        currency=columns["currency"],
        created_at_ms=columns["created_at_ms"],
        starting_equity=_opt_d(columns["starting_equity"]),
        is_paper=bool(columns["is_paper"]),
        credential_id=columns["credential_id"],
        withdrawal_disabled=bool(columns["withdrawal_disabled"]),
    )


def _account_values(account: accounts.Account) -> dict[str, object]:
    """Maps an account to column values.

    Args:
        account: Account.

    Returns:
        Values.
    """
    return {
        "account_id": account.account_id,
        "tenant_id": account.tenant_id,
        "venue": account.venue,
        "label": account.label,
        "status": account.status.value,
        "modes": json.dumps([m.value for m in account.modes]),
        "currency": account.currency,
        "created_at_ms": account.created_at_ms,
        "starting_equity": decimals.fmt_optional(account.starting_equity),
        "is_paper": account.is_paper,
        "credential_id": account.credential_id,
        "withdrawal_disabled": account.withdrawal_disabled,
    }


class SqlAccounts(_Repo):
    """Accounts table."""

    def get(self, account_id: str) -> accounts.Account | None:
        """Loads an account.

        Args:
            account_id: Id.

        Returns:
            Account or ``None``.
        """
        row = self.conn.execute(
            sa.select(schema.accounts).where(
                schema.accounts.c.account_id == account_id
            )
        ).first()
        return None if row is None else _account_from_row(row)

    def list_for_tenant(self, tenant_id: str) -> list[accounts.Account]:
        """Lists accounts.

        Args:
            tenant_id: Tenant.

        Returns:
            Accounts.
        """
        rows = self.conn.execute(
            sa.select(schema.accounts)
            .where(schema.accounts.c.tenant_id == tenant_id)
            .order_by(schema.accounts.c.created_at_ms)
        )
        return [_account_from_row(r) for r in rows]

    def add(self, account: accounts.Account) -> None:
        """Inserts an account.

        Args:
            account: Account.
        """
        self.conn.execute(
            schema.accounts.insert().values(_account_values(account))
        )

    def save(self, account: accounts.Account) -> None:
        """Updates an account.

        Args:
            account: Account.
        """
        self.conn.execute(
            schema.accounts.update()
            .where(schema.accounts.c.account_id == account.account_id)
            .values(_account_values(account))
        )


class SqlCredentials(_Repo):
    """Credential envelopes."""

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
        self.conn.execute(
            schema.credentials.insert().values(
                credential_id=credential_id,
                tenant_id=tenant_id,
                account_id=account_id,
                envelope=json.dumps(dict(envelope)),
                created_at_ms=created_at_ms,
            )
        )

    def get(self, credential_id: str) -> Mapping[str, str] | None:
        """Loads an envelope.

        Args:
            credential_id: Id.

        Returns:
            Envelope or ``None``.
        """
        row = self.conn.execute(
            sa.select(schema.credentials.c.envelope).where(
                schema.credentials.c.credential_id == credential_id
            )
        ).first()
        return None if row is None else dict(json.loads(row[0]))

    def delete_for_account(self, account_id: str) -> int:
        """Deletes envelopes of an account.

        Args:
            account_id: Account.

        Returns:
            Rows deleted.
        """
        result = self.conn.execute(
            schema.credentials.delete().where(
                schema.credentials.c.account_id == account_id
            )
        )
        return int(result.rowcount or 0)


def _intent_from_row(row: sa.Row[object]) -> repositories.StoredIntent:
    """Maps an ``order_intents`` row to a stored intent.

    Args:
        row: Row.

    Returns:
        Stored intent.
    """
    columns = row._mapping
    request = intents.OrderIntentRequest.from_contract(
        json.loads(columns["request"])
    )
    intent = intents.OrderIntent(
        intent_id=columns["intent_id"],
        tenant_id=columns["tenant_id"],
        user_id=columns["user_id"],
        idempotency_key=columns["idempotency_key"],
        client_order_id=columns["client_order_id"],
        created_at_ms=columns["created_at_ms"],
        request=request,
        deployment_id=columns["deployment_id"],
    )
    return repositories.StoredIntent(
        intent=intent,
        decision=risk.RiskDecision.from_contract(
            json.loads(columns["decision"])
        ),
        body_hash=columns["body_hash"],
    )


class SqlIntents(_Repo):
    """Order intents."""

    def add(self, stored: repositories.StoredIntent) -> None:
        """Inserts an intent.

        Args:
            stored: Intent.

        Raises:
            ExecutionError: ``IDEMPOTENCY_CONFLICT`` on a duplicate key.
        """
        intent = stored.intent
        try:
            self.conn.execute(
                schema.order_intents.insert().values(
                    intent_id=intent.intent_id,
                    tenant_id=intent.tenant_id,
                    user_id=intent.user_id,
                    account_id=intent.request.account_id,
                    idempotency_key=intent.idempotency_key,
                    client_order_id=intent.client_order_id,
                    body_hash=stored.body_hash,
                    request=json.dumps(intent.request.to_contract()),
                    decision=json.dumps(stored.decision.to_contract()),
                    signal_id=intent.request.signal_id,
                    deployment_id=intent.deployment_id,
                    created_at_ms=intent.created_at_ms,
                )
            )
        except sa_exc.IntegrityError as err:
            raise errors.ExecutionError(
                "IDEMPOTENCY_CONFLICT", "intent already exists"
            ) from err

    def get(self, intent_id: str) -> repositories.StoredIntent | None:
        """Loads an intent.

        Args:
            intent_id: Id.

        Returns:
            Stored intent or ``None``.
        """
        row = self.conn.execute(
            sa.select(schema.order_intents).where(
                schema.order_intents.c.intent_id == intent_id
            )
        ).first()
        return None if row is None else _intent_from_row(row)

    def find_by_idempotency(
        self, tenant_id: str, key: str
    ) -> repositories.StoredIntent | None:
        """Finds an intent by idempotency key.

        Args:
            tenant_id: Tenant.
            key: Key.

        Returns:
            Stored intent or ``None``.
        """
        row = self.conn.execute(
            sa.select(schema.order_intents).where(
                schema.order_intents.c.tenant_id == tenant_id,
                schema.order_intents.c.idempotency_key == key,
            )
        ).first()
        return None if row is None else _intent_from_row(row)

    def count_for_signal(
        self, tenant_id: str, signal_id: str, deployment_id: str | None
    ) -> int:
        """Counts intents of a signal and deployment.

        Args:
            tenant_id: Tenant.
            signal_id: Signal.
            deployment_id: Deployment or ``None``.

        Returns:
            Count.
        """
        c = schema.order_intents.c
        query = sa.select(sa.func.count()).where(
            c.tenant_id == tenant_id, c.signal_id == signal_id
        )
        if deployment_id is None:
            query = query.where(c.deployment_id.is_(None))
        else:
            query = query.where(c.deployment_id == deployment_id)
        return int(self.conn.execute(query).scalar_one())


def _order_from_row(row: sa.Row[object]) -> orders.Order:
    """Maps an ``orders`` row to an order.

    Args:
        row: Row.

    Returns:
        Order.
    """
    columns = row._mapping
    basis = columns["trigger_basis"]
    return orders.Order(
        order_id=columns["order_id"],
        intent_id=columns["intent_id"],
        tenant_id=columns["tenant_id"],
        account_id=columns["account_id"],
        instrument_id=columns["instrument_id"],
        client_order_id=columns["client_order_id"],
        mode=enums.OrderMode(columns["mode"]),
        role=enums.OrderRole(columns["role"]),
        side=enums.OrderSide(columns["side"]),
        order_type=enums.OrderType(columns["order_type"]),
        time_in_force=enums.TimeInForce(columns["time_in_force"]),
        quantity_base=_D(columns["quantity_base"]),
        reduce_only=bool(columns["reduce_only"]),
        state=enums.OrderState(columns["state"]),
        created_at_ms=columns["created_at_ms"],
        updated_at_ms=columns["updated_at_ms"],
        version=columns["version"],
        filled_base=_D(columns["filled_base"]),
        avg_fill_price=_opt_d(columns["avg_fill_price"]),
        limit_price=_opt_d(columns["limit_price"]),
        trigger_price=_opt_d(columns["trigger_price"]),
        trigger_basis=None if basis is None else enums.PriceBasis(basis),
        exchange_order_id=columns["exchange_order_id"],
        parent_order_id=columns["parent_order_id"],
        state_reason=columns["state_reason"],
        strategy_version_id=columns["strategy_version_id"],
        signal_id=columns["signal_id"],
        submit_attempts=columns["submit_attempts"],
        manual_intervention=bool(columns["manual_intervention"]),
        fill_ids=set(json.loads(columns["fill_ids"])),
    )


def _order_values(order: orders.Order) -> dict[str, object]:
    """Maps an order to column values.

    Args:
        order: Order.

    Returns:
        Values.
    """
    return {
        "order_id": order.order_id,
        "intent_id": order.intent_id,
        "tenant_id": order.tenant_id,
        "account_id": order.account_id,
        "instrument_id": order.instrument_id,
        "client_order_id": order.client_order_id,
        "exchange_order_id": order.exchange_order_id,
        "parent_order_id": order.parent_order_id,
        "mode": order.mode.value,
        "role": order.role.value,
        "side": order.side.value,
        "order_type": order.order_type.value,
        "time_in_force": order.time_in_force.value,
        "quantity_base": decimals.fmt(order.quantity_base),
        "filled_base": decimals.fmt(order.filled_base),
        "avg_fill_price": decimals.fmt_optional(order.avg_fill_price),
        "limit_price": decimals.fmt_optional(order.limit_price),
        "trigger_price": decimals.fmt_optional(order.trigger_price),
        "trigger_basis": None
        if order.trigger_basis is None
        else order.trigger_basis.value,
        "reduce_only": order.reduce_only,
        "state": order.state.value,
        "state_reason": order.state_reason,
        "strategy_version_id": order.strategy_version_id,
        "signal_id": order.signal_id,
        "created_at_ms": order.created_at_ms,
        "updated_at_ms": order.updated_at_ms,
        "version": order.version,
        "submit_attempts": order.submit_attempts,
        "manual_intervention": order.manual_intervention,
        "fill_ids": json.dumps(sorted(order.fill_ids)),
    }


class SqlOrders(_Repo):
    """Orders."""

    def add(self, order: orders.Order) -> None:
        """Inserts an order.

        Args:
            order: Order.

        Raises:
            ExecutionError: ``DUPLICATE_INTENT`` on a duplicate client id.
        """
        try:
            self.conn.execute(
                schema.orders.insert().values(_order_values(order))
            )
        except sa_exc.IntegrityError as err:
            raise errors.ExecutionError(
                "DUPLICATE_INTENT", "client order id already used"
            ) from err

    def save(self, order: orders.Order, expected_version: int) -> None:
        """Updates with optimistic concurrency.

        Args:
            order: Order.
            expected_version: Loaded version.

        Raises:
            ExecutionError: ``CONFLICT`` when the row changed meanwhile.
        """
        result = self.conn.execute(
            schema.orders.update()
            .where(
                schema.orders.c.order_id == order.order_id,
                schema.orders.c.version == expected_version,
            )
            .values(_order_values(order))
        )
        if not result.rowcount:
            raise errors.ExecutionError(
                "CONFLICT", f"order {order.order_id} changed concurrently"
            )

    def get(self, order_id: str) -> orders.Order | None:
        """Loads an order.

        Args:
            order_id: Id.

        Returns:
            Order or ``None``.
        """
        row = self.conn.execute(
            sa.select(schema.orders).where(schema.orders.c.order_id == order_id)
        ).first()
        return None if row is None else _order_from_row(row)

    def get_by_client_id(
        self, account_id: str, client_order_id: str
    ) -> orders.Order | None:
        """Loads by client id.

        Args:
            account_id: Account.
            client_order_id: Client id.

        Returns:
            Order or ``None``.
        """
        row = self.conn.execute(
            sa.select(schema.orders).where(
                schema.orders.c.account_id == account_id,
                schema.orders.c.client_order_id == client_order_id,
            )
        ).first()
        return None if row is None else _order_from_row(row)

    def list(  # noqa: PLR0917 - implements the OrderRepository port signature
        self,
        tenant_id: str | None = None,
        account_id: str | None = None,
        states: Sequence[enums.OrderState] | None = None,
        intent_id: str | None = None,
        parent_order_id: str | None = None,
        limit: int = 500,
    ) -> list[orders.Order]:
        """Lists orders newest first.

        Args:
            tenant_id: Tenant.
            account_id: Account.
            states: States.
            intent_id: Intent.
            parent_order_id: Parent.
            limit: Maximum.

        Returns:
            Orders.
        """
        c = schema.orders.c
        query = sa.select(schema.orders)
        if tenant_id is not None:
            query = query.where(c.tenant_id == tenant_id)
        if account_id is not None:
            query = query.where(c.account_id == account_id)
        if states:
            query = query.where(c.state.in_([s.value for s in states]))
        if intent_id is not None:
            query = query.where(c.intent_id == intent_id)
        if parent_order_id is not None:
            query = query.where(c.parent_order_id == parent_order_id)
        query = query.order_by(c.created_at_ms.desc(), c.order_id.desc())
        return [
            _order_from_row(r) for r in self.conn.execute(query.limit(limit))
        ]


def _fill_from_row(row: sa.Row[object]) -> orders.Fill:
    """Maps a ``fills`` row to a fill.

    Args:
        row: Row.

    Returns:
        Fill.
    """
    columns = row._mapping
    return orders.Fill(
        fill_id=columns["fill_id"],
        order_id=columns["order_id"],
        account_id=columns["account_id"],
        instrument_id=columns["instrument_id"],
        exchange_fill_id=columns["exchange_fill_id"],
        side=enums.OrderSide(columns["side"]),
        price=_D(columns["price"]),
        quantity_base=_D(columns["quantity_base"]),
        fee=_D(columns["fee"]),
        fee_asset=columns["fee_asset"],
        liquidity=columns["liquidity"],
        ts_ms=columns["ts_ms"],
        mode=enums.OrderMode(columns["mode"]),
        realized_pnl=_D(columns["realized_pnl"]),
    )


class SqlFills(_Repo):
    """Fills."""

    def add(self, fill: orders.Fill, tenant_id: str) -> bool:
        """Inserts a fill unless the venue fill id is known.

        Args:
            fill: Fill.
            tenant_id: Tenant.

        Returns:
            True when inserted; False when the account already has a fill
            with this venue fill id.
        """
        exists = self.conn.execute(
            sa.select(schema.fills.c.fill_id).where(
                schema.fills.c.account_id == fill.account_id,
                schema.fills.c.exchange_fill_id == fill.exchange_fill_id,
            )
        ).first()
        if exists is not None:
            return False
        self.conn.execute(
            schema.fills.insert().values(
                fill_id=fill.fill_id,
                order_id=fill.order_id,
                tenant_id=tenant_id,
                account_id=fill.account_id,
                instrument_id=fill.instrument_id,
                exchange_fill_id=fill.exchange_fill_id,
                side=fill.side.value,
                price=decimals.fmt(fill.price),
                quantity_base=decimals.fmt(fill.quantity_base),
                fee=decimals.fmt(fill.fee),
                fee_asset=fill.fee_asset,
                liquidity=fill.liquidity,
                ts_ms=fill.ts_ms,
                mode=fill.mode.value,
                realized_pnl=decimals.fmt(fill.realized_pnl),
            )
        )
        return True

    def list(
        self,
        tenant_id: str,
        account_id: str | None = None,
        order_id: str | None = None,
        limit: int = 500,
    ) -> list[orders.Fill]:
        """Lists fills newest first.

        Args:
            tenant_id: Tenant.
            account_id: Account.
            order_id: Order.
            limit: Maximum.

        Returns:
            Fills.
        """
        c = schema.fills.c
        query = sa.select(schema.fills).where(c.tenant_id == tenant_id)
        if account_id is not None:
            query = query.where(c.account_id == account_id)
        if order_id is not None:
            query = query.where(c.order_id == order_id)
        query = query.order_by(c.ts_ms.desc(), c.fill_id.desc()).limit(limit)
        return [_fill_from_row(r) for r in self.conn.execute(query)]

    def realized_since(self, account_id: str, since_ms: int) -> decimal.Decimal:
        """Sums realized PnL since a time.

        Args:
            account_id: Account.
            since_ms: Lower bound.

        Returns:
            Sum.
        """
        rows = self.conn.execute(
            sa.select(schema.fills.c.realized_pnl).where(
                schema.fills.c.account_id == account_id,
                schema.fills.c.ts_ms >= since_ms,
            )
        )
        return sum((_D(r[0]) for r in rows), decimals.ZERO)


def _position_from_row(row: sa.Row[object]) -> positions.Position:
    """Maps a ``positions`` row to a position.

    Args:
        row: Row.

    Returns:
        Position.
    """
    columns = row._mapping
    return positions.Position(
        position_id=columns["position_id"],
        tenant_id=columns["tenant_id"],
        account_id=columns["account_id"],
        instrument_id=columns["instrument_id"],
        mode=enums.OrderMode(columns["mode"]),
        signed_qty=_D(columns["signed_qty"]),
        entry_price=_opt_d(columns["entry_price"]),
        realized_pnl=_D(columns["realized_pnl"]),
        fees_paid=_D(columns["fees_paid"]),
        leverage=_D(columns["leverage"]),
        updated_at_ms=columns["updated_at_ms"],
        unknown=bool(columns["unknown"]),
    )


class SqlPositions(_Repo):
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
        return self.get_by_id(
            positions.position_id(account_id, instrument_id, mode.value)
        )

    def get_by_id(self, position_id: str) -> positions.Position | None:
        """Loads by id.

        Args:
            position_id: Id.

        Returns:
            Position or ``None``.
        """
        row = self.conn.execute(
            sa.select(schema.positions).where(
                schema.positions.c.position_id == position_id
            )
        ).first()
        return None if row is None else _position_from_row(row)

    def save(self, position: positions.Position) -> None:
        """Upserts a position.

        Args:
            position: Position.
        """
        values = {
            "position_id": position.position_id,
            "tenant_id": position.tenant_id,
            "account_id": position.account_id,
            "instrument_id": position.instrument_id,
            "mode": position.mode.value,
            "signed_qty": decimals.fmt(position.signed_qty),
            "entry_price": decimals.fmt_optional(position.entry_price),
            "realized_pnl": decimals.fmt(position.realized_pnl),
            "fees_paid": decimals.fmt(position.fees_paid),
            "leverage": decimals.fmt(position.leverage),
            "updated_at_ms": position.updated_at_ms,
            "unknown": position.unknown,
        }
        if self.get_by_id(position.position_id) is None:
            self.conn.execute(schema.positions.insert().values(values))
        else:
            self.conn.execute(
                schema.positions.update()
                .where(schema.positions.c.position_id == position.position_id)
                .values(values)
            )

    def list(
        self,
        tenant_id: str | None = None,
        account_id: str | None = None,
        include_flat: bool = False,
    ) -> list[positions.Position]:
        """Lists positions.

        Args:
            tenant_id: Tenant.
            account_id: Account.
            include_flat: Include flat rows.

        Returns:
            Positions.
        """
        c = schema.positions.c
        query = sa.select(schema.positions)
        if tenant_id is not None:
            query = query.where(c.tenant_id == tenant_id)
        if account_id is not None:
            query = query.where(c.account_id == account_id)
        result = [_position_from_row(r) for r in self.conn.execute(query)]
        if include_flat:
            return result
        return [p for p in result if p.signed_qty != decimals.ZERO]


class SqlReservations(_Repo):
    """Reservations."""

    def add(  # noqa: PLR0917 - implements the ReservationRepository port signature
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
        """Creates a reservation.

        Args:
            reservation_id: Id.
            tenant_id: Tenant.
            account_id: Account.
            intent_id: Intent.
            order_id: Order.
            amount: Amount.
            created_at_ms: Time.
            mode: Order mode.

        Raises:
            ValueError: If ``amount`` is negative.
        """
        if amount < decimals.ZERO:
            raise ValueError("reservation amount must be non-negative")
        self.conn.execute(
            schema.reservations.insert().values(
                reservation_id=reservation_id,
                tenant_id=tenant_id,
                account_id=account_id,
                intent_id=intent_id,
                order_id=order_id,
                amount=decimals.fmt(amount),
                mode=mode.value,
                status="ACTIVE",
                created_at_ms=created_at_ms,
                released_at_ms=None,
            )
        )

    def release_for_order(self, order_id: str, now_ms: int) -> int:
        """Releases active reservations of an order.

        Args:
            order_id: Order.
            now_ms: Time.

        Returns:
            Rows released.
        """
        result = self.conn.execute(
            schema.reservations.update()
            .where(
                schema.reservations.c.order_id == order_id,
                schema.reservations.c.status == "ACTIVE",
            )
            .values(status="RELEASED", released_at_ms=now_ms)
        )
        return int(result.rowcount or 0)

    def active_total(
        self, account_id: str, mode: enums.OrderMode | None = None
    ) -> decimal.Decimal:
        """Sums active reservations.

        Args:
            account_id: Account.
            mode: Optional mode filter.

        Returns:
            Total.
        """
        c = schema.reservations.c
        query = sa.select(c.amount).where(
            c.account_id == account_id, c.status == "ACTIVE"
        )
        if mode is not None:
            query = query.where(c.mode == mode.value)
        rows = self.conn.execute(query)
        return sum((_D(r[0]) for r in rows), decimals.ZERO)

    def active_for_order(self, order_id: str) -> decimal.Decimal | None:
        """Returns the active reservation of an order.

        Args:
            order_id: Order.

        Returns:
            Amount or ``None``.
        """
        row = self.conn.execute(
            sa.select(schema.reservations.c.amount).where(
                schema.reservations.c.order_id == order_id,
                schema.reservations.c.status == "ACTIVE",
            )
        ).first()
        return None if row is None else _D(row[0])


def _outbox_item_from_row(row: sa.Row[object]) -> repositories.OutboxItem:
    """Maps an ``outbox`` row to an outbox item.

    Args:
        row: Row.

    Returns:
        Item.
    """
    columns = row._mapping
    return repositories.OutboxItem(
        outbox_id=columns["outbox_id"],
        aggregate_id=columns["aggregate_id"],
        event_key=columns["event_key"],
        kind=columns["kind"],
        status=columns["status"],
        attempts=columns["attempts"],
        next_attempt_ms=columns["next_attempt_ms"],
        last_error=columns["last_error"],
    )


class SqlOutbox(_Repo):
    """Outbox."""

    def add(
        self, aggregate_id: str, event_key: str, kind: str, now_ms: int
    ) -> bool:
        """Adds an item idempotently.

        Args:
            aggregate_id: Order id.
            event_key: Key.
            kind: Kind.
            now_ms: Time.

        Returns:
            True when added; False when the aggregate already has an item
            with this key.
        """
        exists = self.conn.execute(
            sa.select(schema.outbox.c.outbox_id).where(
                schema.outbox.c.aggregate_id == aggregate_id,
                schema.outbox.c.event_key == event_key,
            )
        ).first()
        if exists is not None:
            return False
        self.conn.execute(
            schema.outbox.insert().values(
                aggregate_id=aggregate_id,
                event_key=event_key,
                kind=kind,
                status="PENDING",
                attempts=0,
                next_attempt_ms=now_ms,
                last_error=None,
                created_at_ms=now_ms,
            )
        )
        return True

    def due(
        self, now_ms: int, limit: int = 50
    ) -> list[repositories.OutboxItem]:
        """Lists due pending items.

        Args:
            now_ms: Time.
            limit: Maximum.

        Returns:
            Items.
        """
        c = schema.outbox.c
        rows = self.conn.execute(
            sa.select(schema.outbox)
            .where(c.status == "PENDING", c.next_attempt_ms <= now_ms)
            .order_by(c.outbox_id)
            .limit(limit)
        )
        return [_outbox_item_from_row(r) for r in rows]

    def update(  # noqa: PLR0917 - implements the OutboxRepository port signature
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
            outbox_id: Id.
            status: Status.
            attempts: Attempts.
            next_attempt_ms: Next attempt.
            last_error: Error.
            kind: Optional new kind.
        """
        values: dict[str, object] = {
            "status": status,
            "attempts": attempts,
            "next_attempt_ms": next_attempt_ms,
            "last_error": last_error,
        }
        if kind is not None:
            values["kind"] = kind
        self.conn.execute(
            schema.outbox.update()
            .where(schema.outbox.c.outbox_id == outbox_id)
            .values(values)
        )

    def for_aggregate(self, aggregate_id: str) -> list[repositories.OutboxItem]:
        """Lists items of an aggregate.

        Args:
            aggregate_id: Order id.

        Returns:
            Items.
        """
        rows = self.conn.execute(
            sa.select(schema.outbox)
            .where(schema.outbox.c.aggregate_id == aggregate_id)
            .order_by(schema.outbox.c.outbox_id)
        )
        return [_outbox_item_from_row(r) for r in rows]


def _audit_event_from_row(row: sa.Row[object]) -> audit.AuditEvent:
    """Maps an ``audit_events`` row to an audit event.

    Args:
        row: Row.

    Returns:
        Audit event.
    """
    columns = row._mapping
    return audit.AuditEvent(
        event_id=columns["event_id"],
        tenant_id=columns["tenant_id"],
        actor=columns["actor"],
        action=columns["action"],
        entity_type=columns["entity_type"],
        entity_id=columns["entity_id"],
        trace_id=columns["trace_id"],
        ts_ms=columns["ts_ms"],
        payload=json.loads(columns["payload"]),
        prev_hash=columns["prev_hash"],
        hash=columns["hash"],
        account_id=columns["account_id"],
    )


class SqlAudit(_Repo):
    """Append-only audit store."""

    def last_hash(self, tenant_id: str) -> str:
        """Returns the tenant's latest hash.

        Args:
            tenant_id: Tenant.

        Returns:
            Hash or genesis.
        """
        row = self.conn.execute(
            sa.select(schema.audit_events.c.hash)
            .where(schema.audit_events.c.tenant_id == tenant_id)
            .order_by(schema.audit_events.c.seq.desc())
            .limit(1)
        ).first()
        return audit.GENESIS_HASH if row is None else str(row[0])

    def append(self, event: audit.AuditEvent) -> None:
        """Appends a record.

        Args:
            event: Record.
        """
        self.conn.execute(
            schema.audit_events.insert().values(
                event_id=event.event_id,
                tenant_id=event.tenant_id,
                account_id=event.account_id,
                actor=event.actor,
                action=event.action,
                entity_type=event.entity_type,
                entity_id=event.entity_id,
                trace_id=event.trace_id,
                ts_ms=event.ts_ms,
                payload=json.dumps(dict(event.payload), sort_keys=True),
                prev_hash=event.prev_hash,
                hash=event.hash,
            )
        )

    def chain(self, tenant_id: str) -> list[audit.AuditEvent]:
        """Returns the tenant chain in order.

        Args:
            tenant_id: Tenant.

        Returns:
            Records.
        """
        rows = self.conn.execute(
            sa.select(schema.audit_events)
            .where(schema.audit_events.c.tenant_id == tenant_id)
            .order_by(schema.audit_events.c.seq)
        )
        return [_audit_event_from_row(r) for r in rows]

    def list(
        self, tenant_id: str, account_id: str | None = None, limit: int = 200
    ) -> list[audit.AuditEvent]:
        """Lists records newest first.

        Args:
            tenant_id: Tenant.
            account_id: Account.
            limit: Maximum.

        Returns:
            Records.
        """
        c = schema.audit_events.c
        query = sa.select(schema.audit_events).where(c.tenant_id == tenant_id)
        if account_id is not None:
            query = query.where(c.account_id == account_id)
        query = query.order_by(c.seq.desc()).limit(limit)
        return [_audit_event_from_row(r) for r in self.conn.execute(query)]


class SqlKillSwitches(_Repo):
    """Kill switches."""

    def upsert(self, switch: kill_switch.KillSwitch) -> None:
        """Saves a switch.

        Args:
            switch: State.
        """
        key = switch.target_id or ""
        c = schema.kill_switches.c
        self.conn.execute(
            schema.kill_switches.delete().where(
                c.tenant_id == switch.tenant_id,
                c.scope == switch.scope.value,
                c.target_key == key,
            )
        )
        self.conn.execute(
            schema.kill_switches.insert().values(
                tenant_id=switch.tenant_id,
                scope=switch.scope.value,
                target_key=key,
                target_id=switch.target_id,
                engaged=switch.engaged,
                cancel_open_orders=switch.cancel_open_orders,
                flatten_positions=switch.flatten_positions,
                reason=switch.reason,
                actor=switch.actor,
                changed_at_ms=switch.changed_at_ms,
            )
        )

    def list(self, tenant_id: str) -> list[kill_switch.KillSwitch]:
        """Lists switches.

        Args:
            tenant_id: Tenant.

        Returns:
            Switches.
        """
        rows = self.conn.execute(
            sa.select(schema.kill_switches).where(
                schema.kill_switches.c.tenant_id == tenant_id
            )
        )
        result = []
        for row in rows:
            columns = row._mapping
            result.append(
                kill_switch.KillSwitch(
                    tenant_id=columns["tenant_id"],
                    scope=enums.KillSwitchScope(columns["scope"]),
                    target_id=columns["target_id"],
                    engaged=bool(columns["engaged"]),
                    cancel_open_orders=bool(columns["cancel_open_orders"]),
                    flatten_positions=bool(columns["flatten_positions"]),
                    reason=columns["reason"],
                    actor=columns["actor"],
                    changed_at_ms=columns["changed_at_ms"],
                )
            )
        return result


class SqlRiskLimits(_Repo):
    """User risk limits."""

    def get(self, tenant_id: str, account_id: str) -> dict[str, str] | None:
        """Loads limits.

        Args:
            tenant_id: Tenant.
            account_id: Account.

        Returns:
            Limits or ``None``.
        """
        row = self.conn.execute(
            sa.select(schema.risk_limits.c.limits).where(
                schema.risk_limits.c.tenant_id == tenant_id,
                schema.risk_limits.c.account_id == account_id,
            )
        ).first()
        return None if row is None else dict(json.loads(row[0]))

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
        c = schema.risk_limits.c
        self.conn.execute(
            schema.risk_limits.delete().where(
                c.tenant_id == tenant_id, c.account_id == account_id
            )
        )
        self.conn.execute(
            schema.risk_limits.insert().values(
                tenant_id=tenant_id,
                account_id=account_id,
                limits=json.dumps(dict(limits)),
                updated_at_ms=now_ms,
            )
        )


def _deployment_from_row(row: sa.Row[object]) -> deployments.Deployment:
    """Maps a ``deployments`` row to a deployment.

    Args:
        row: Row.

    Returns:
        Deployment.
    """
    columns = row._mapping
    consent_raw = columns["live_consent"]
    consent = None
    if consent_raw:
        body = json.loads(consent_raw)
        consent = deployments.LiveConsent(
            consented_at_ms=int(body["consented_at_ms"]),
            expires_at_ms=int(body["expires_at_ms"]),
            scope_hash=str(body["scope_hash"]),
            actor=str(body["actor"]),
        )
    return deployments.Deployment(
        deployment_id=columns["deployment_id"],
        tenant_id=columns["tenant_id"],
        strategy_version_id=columns["strategy_version_id"],
        account_id=columns["account_id"],
        mode=enums.DeploymentMode(columns["mode"]),
        state=enums.DeploymentState(columns["state"]),
        limits=deployments.DeploymentLimits.from_contract(
            json.loads(columns["limits"])
        ),
        sizing=deployments.SizingModel.from_contract(
            json.loads(columns["sizing"])
        ),
        created_at_ms=columns["created_at_ms"],
        updated_at_ms=columns["updated_at_ms"],
        live_consent=consent,
    )


def _deployment_values(deployment: deployments.Deployment) -> dict[str, object]:
    """Maps a deployment to column values.

    Args:
        deployment: Deployment.

    Returns:
        Values.
    """
    return {
        "deployment_id": deployment.deployment_id,
        "tenant_id": deployment.tenant_id,
        "strategy_version_id": deployment.strategy_version_id,
        "account_id": deployment.account_id,
        "mode": deployment.mode.value,
        "state": deployment.state.value,
        "limits": json.dumps(deployment.limits.to_contract()),
        "sizing": json.dumps(deployment.sizing.to_contract()),
        "live_consent": None
        if deployment.live_consent is None
        else json.dumps(deployment.live_consent.to_contract()),
        "created_at_ms": deployment.created_at_ms,
        "updated_at_ms": deployment.updated_at_ms,
    }


class SqlDeployments(_Repo):
    """Deployments."""

    def add(self, deployment: deployments.Deployment) -> None:
        """Inserts.

        Args:
            deployment: Deployment.
        """
        self.conn.execute(
            schema.deployments.insert().values(_deployment_values(deployment))
        )

    def save(self, deployment: deployments.Deployment) -> None:
        """Updates.

        Args:
            deployment: Deployment.
        """
        self.conn.execute(
            schema.deployments.update()
            .where(
                schema.deployments.c.deployment_id == deployment.deployment_id
            )
            .values(_deployment_values(deployment))
        )

    def get(self, deployment_id: str) -> deployments.Deployment | None:
        """Loads.

        Args:
            deployment_id: Id.

        Returns:
            Deployment or ``None``.
        """
        row = self.conn.execute(
            sa.select(schema.deployments).where(
                schema.deployments.c.deployment_id == deployment_id
            )
        ).first()
        return None if row is None else _deployment_from_row(row)

    def list(
        self, tenant_id: str, strategy_version_id: str | None = None
    ) -> list[deployments.Deployment]:
        """Lists.

        Args:
            tenant_id: Tenant.
            strategy_version_id: Filter.

        Returns:
            Deployments.
        """
        c = schema.deployments.c
        query = sa.select(schema.deployments).where(c.tenant_id == tenant_id)
        if strategy_version_id is not None:
            query = query.where(c.strategy_version_id == strategy_version_id)
        query = query.order_by(c.created_at_ms)
        return [_deployment_from_row(r) for r in self.conn.execute(query)]


class SqlLiveArming(_Repo):
    """Account LIVE arming (one current row per tenant account)."""

    def get(
        self, tenant_id: str, account_id: str
    ) -> accounts.LiveArming | None:
        """Loads the current arming of an account.

        Args:
            tenant_id: Tenant.
            account_id: Account.

        Returns:
            Arming or ``None``.
        """
        c = schema.account_live_arming.c
        row = self.conn.execute(
            sa.select(schema.account_live_arming).where(
                c.tenant_id == tenant_id, c.account_id == account_id
            )
        ).first()
        if row is None:
            return None
        columns = row._mapping
        return accounts.LiveArming(
            tenant_id=columns["tenant_id"],
            account_id=columns["account_id"],
            granted_at_ms=columns["granted_at_ms"],
            expires_at_ms=columns["expires_at_ms"],
            granted_by=columns["granted_by"],
            acknowledgement=columns["acknowledgement"],
            scope_hash=columns["scope_hash"],
            revoked_at_ms=columns["revoked_at_ms"],
            revoked_by=columns["revoked_by"],
        )

    def put(self, arming: accounts.LiveArming) -> None:
        """Stores an arming, replacing the previous one.

        Args:
            arming: Arming.
        """
        c = schema.account_live_arming.c
        self.conn.execute(
            schema.account_live_arming.delete().where(
                c.tenant_id == arming.tenant_id,
                c.account_id == arming.account_id,
            )
        )
        self.conn.execute(
            schema.account_live_arming.insert().values(arming.to_contract())
        )


class SqlUnitOfWork:
    """One database transaction exposing every repository.

    Attributes:
        accounts: Accounts repository.
        credentials: Credentials repository.
        intents: Intents repository.
        orders: Orders repository.
        fills: Fills repository.
        positions: Positions repository.
        reservations: Reservations repository.
        outbox: Outbox repository.
        audit: Audit repository.
        kill_switches: Kill switch repository.
        risk_limits: Risk limit repository.
        deployments: Deployment repository.
        live_arming: Account LIVE arming repository.
    """

    def __init__(self, engine: sa.Engine, lock: threading.RLock) -> None:
        """Initializes the unit of work.

        Args:
            engine: Engine.
            lock: Serialization lock.
        """
        self._engine = engine
        self._lock = lock
        self._conn: sa.Connection | None = None
        self._tx: sa.RootTransaction | None = None
        self.accounts = SqlAccounts(self)
        self.credentials = SqlCredentials(self)
        self.intents = SqlIntents(self)
        self.orders = SqlOrders(self)
        self.fills = SqlFills(self)
        self.positions = SqlPositions(self)
        self.reservations = SqlReservations(self)
        self.outbox = SqlOutbox(self)
        self.audit = SqlAudit(self)
        self.kill_switches = SqlKillSwitches(self)
        self.risk_limits = SqlRiskLimits(self)
        self.deployments = SqlDeployments(self)
        self.live_arming = SqlLiveArming(self)

    @property
    def connection(self) -> sa.Connection:
        """The active connection.

        Raises:
            RuntimeError: Outside a ``with`` block.
        """
        if self._conn is None:
            raise RuntimeError("unit of work is not active")
        return self._conn

    def __enter__(self) -> SqlUnitOfWork:
        """Begins the transaction.

        Returns:
            Self.
        """
        self._lock.acquire()
        # Release the lock if no transaction opens, or it would stay held.
        try:
            self._conn = self._engine.connect()
            self._tx = self._conn.begin()
        except Exception:
            self._lock.release()
            raise
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: types.TracebackType | None,
    ) -> None:
        """Commits or rolls back.

        Args:
            exc_type: Exception type.
            exc: Exception.
            tb: Traceback.
        """
        try:
            if self._tx is not None:
                if exc_type is None:
                    self._tx.commit()
                else:
                    self._tx.rollback()
        finally:
            if self._conn is not None:
                self._conn.close()
            self._conn = None
            self._tx = None
            self._lock.release()
