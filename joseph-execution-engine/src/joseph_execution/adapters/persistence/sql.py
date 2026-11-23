"""SQLAlchemy Core implementation of the persistence ports."""

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
    another connection's commit; because commits run on the event loop, that
    froze every request. WAL lets readers and the single writer proceed
    concurrently, and ``busy_timeout`` makes writers wait instead of failing.

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
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute(f"PRAGMA busy_timeout={_SQLITE_BUSY_TIMEOUT_MS}")
        cursor.close()


def create_engine(url: str) -> sa.Engine:
    """Creates an engine and the schema.

    In-memory SQLite uses a single shared connection so every unit of work
    sees the same database.

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
    """Creates SQL units of work; serializes them for SQLite."""

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
        """Returns the active connection."""
        return self._uow.connection


class SqlAccounts(_Repo):
    """Accounts table."""

    @staticmethod
    def _row(row: sa.Row[object]) -> accounts.Account:
        """Maps a row.

        Args:
            row: Row.

        Returns:
            Account.
        """
        m = row._mapping
        return accounts.Account(
            account_id=m["account_id"],
            tenant_id=m["tenant_id"],
            venue=m["venue"],
            label=m["label"],
            status=enums.AccountStatus(m["status"]),
            modes=tuple(enums.OrderMode(x) for x in json.loads(m["modes"])),
            currency=m["currency"],
            created_at_ms=m["created_at_ms"],
            starting_equity=_opt_d(m["starting_equity"]),
            is_paper=bool(m["is_paper"]),
            credential_id=m["credential_id"],
            withdrawal_disabled=bool(m["withdrawal_disabled"]),
        )

    @staticmethod
    def _values(account: accounts.Account) -> dict[str, object]:
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
        return None if row is None else self._row(row)

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
        return [self._row(r) for r in rows]

    def add(self, account: accounts.Account) -> None:
        """Inserts an account.

        Args:
            account: Account.
        """
        self.conn.execute(
            schema.accounts.insert().values(self._values(account))
        )

    def save(self, account: accounts.Account) -> None:
        """Updates an account.

        Args:
            account: Account.
        """
        self.conn.execute(
            schema.accounts.update()
            .where(schema.accounts.c.account_id == account.account_id)
            .values(self._values(account))
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


class SqlIntents(_Repo):
    """Order intents."""

    @staticmethod
    def _row(row: sa.Row[object]) -> repositories.StoredIntent:
        """Maps a row.

        Args:
            row: Row.

        Returns:
            Stored intent.
        """
        m = row._mapping
        request = intents.OrderIntentRequest.from_contract(
            json.loads(m["request"])
        )
        intent = intents.OrderIntent(
            intent_id=m["intent_id"],
            tenant_id=m["tenant_id"],
            user_id=m["user_id"],
            idempotency_key=m["idempotency_key"],
            client_order_id=m["client_order_id"],
            created_at_ms=m["created_at_ms"],
            request=request,
            deployment_id=m["deployment_id"],
        )
        return repositories.StoredIntent(
            intent=intent,
            decision=risk.RiskDecision.from_contract(json.loads(m["decision"])),
            body_hash=m["body_hash"],
        )

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
        return None if row is None else self._row(row)

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
        return None if row is None else self._row(row)

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


class SqlOrders(_Repo):
    """Orders."""

    @staticmethod
    def _row(row: sa.Row[object]) -> orders.Order:
        """Maps a row.

        Args:
            row: Row.

        Returns:
            Order.
        """
        m = row._mapping
        basis = m["trigger_basis"]
        return orders.Order(
            order_id=m["order_id"],
            intent_id=m["intent_id"],
            tenant_id=m["tenant_id"],
            account_id=m["account_id"],
            instrument_id=m["instrument_id"],
            client_order_id=m["client_order_id"],
            mode=enums.OrderMode(m["mode"]),
            role=enums.OrderRole(m["role"]),
            side=enums.OrderSide(m["side"]),
            order_type=enums.OrderType(m["order_type"]),
            time_in_force=enums.TimeInForce(m["time_in_force"]),
            quantity_base=_D(m["quantity_base"]),
            reduce_only=bool(m["reduce_only"]),
            state=enums.OrderState(m["state"]),
            created_at_ms=m["created_at_ms"],
            updated_at_ms=m["updated_at_ms"],
            version=m["version"],
            filled_base=_D(m["filled_base"]),
            avg_fill_price=_opt_d(m["avg_fill_price"]),
            limit_price=_opt_d(m["limit_price"]),
            trigger_price=_opt_d(m["trigger_price"]),
            trigger_basis=None if basis is None else enums.PriceBasis(basis),
            exchange_order_id=m["exchange_order_id"],
            parent_order_id=m["parent_order_id"],
            state_reason=m["state_reason"],
            strategy_version_id=m["strategy_version_id"],
            signal_id=m["signal_id"],
            submit_attempts=m["submit_attempts"],
            manual_intervention=bool(m["manual_intervention"]),
            fill_ids=set(json.loads(m["fill_ids"])),
        )

    @staticmethod
    def _values(order: orders.Order) -> dict[str, object]:
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

    def add(self, order: orders.Order) -> None:
        """Inserts an order.

        Args:
            order: Order.

        Raises:
            ExecutionError: ``DUPLICATE_INTENT`` on a duplicate client id.
        """
        try:
            self.conn.execute(
                schema.orders.insert().values(self._values(order))
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
            .values(self._values(order))
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
        return None if row is None else self._row(row)

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
        return None if row is None else self._row(row)

    def list(  # noqa: PLR0917
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
        return [self._row(r) for r in self.conn.execute(query.limit(limit))]


class SqlFills(_Repo):
    """Fills."""

    @staticmethod
    def _row(row: sa.Row[object]) -> orders.Fill:
        """Maps a row.

        Args:
            row: Row.

        Returns:
            Fill.
        """
        m = row._mapping
        return orders.Fill(
            fill_id=m["fill_id"],
            order_id=m["order_id"],
            account_id=m["account_id"],
            instrument_id=m["instrument_id"],
            exchange_fill_id=m["exchange_fill_id"],
            side=enums.OrderSide(m["side"]),
            price=_D(m["price"]),
            quantity_base=_D(m["quantity_base"]),
            fee=_D(m["fee"]),
            fee_asset=m["fee_asset"],
            liquidity=m["liquidity"],
            ts_ms=m["ts_ms"],
            mode=enums.OrderMode(m["mode"]),
            realized_pnl=_D(m["realized_pnl"]),
        )

    def add(self, fill: orders.Fill, tenant_id: str) -> bool:
        """Inserts a fill unless the venue fill id is known.

        Args:
            fill: Fill.
            tenant_id: Tenant.

        Returns:
            False for duplicates.
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
        return [self._row(r) for r in self.conn.execute(query)]

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


class SqlPositions(_Repo):
    """Positions."""

    @staticmethod
    def _row(row: sa.Row[object]) -> positions.Position:
        """Maps a row.

        Args:
            row: Row.

        Returns:
            Position.
        """
        m = row._mapping
        return positions.Position(
            position_id=m["position_id"],
            tenant_id=m["tenant_id"],
            account_id=m["account_id"],
            instrument_id=m["instrument_id"],
            mode=enums.OrderMode(m["mode"]),
            signed_qty=_D(m["signed_qty"]),
            entry_price=_opt_d(m["entry_price"]),
            realized_pnl=_D(m["realized_pnl"]),
            fees_paid=_D(m["fees_paid"]),
            leverage=_D(m["leverage"]),
            updated_at_ms=m["updated_at_ms"],
            unknown=bool(m["unknown"]),
        )

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
        return None if row is None else self._row(row)

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
        result = [self._row(r) for r in self.conn.execute(query)]
        if include_flat:
            return result
        return [p for p in result if p.signed_qty != decimals.ZERO]


class SqlReservations(_Repo):
    """Reservations."""

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


class SqlOutbox(_Repo):
    """Outbox."""

    @staticmethod
    def _row(row: sa.Row[object]) -> repositories.OutboxItem:
        """Maps a row.

        Args:
            row: Row.

        Returns:
            Item.
        """
        m = row._mapping
        return repositories.OutboxItem(
            outbox_id=m["outbox_id"],
            aggregate_id=m["aggregate_id"],
            event_key=m["event_key"],
            kind=m["kind"],
            status=m["status"],
            attempts=m["attempts"],
            next_attempt_ms=m["next_attempt_ms"],
            last_error=m["last_error"],
        )

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
            False if it existed.
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
        return [self._row(r) for r in rows]

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
        return [self._row(r) for r in rows]


class SqlAudit(_Repo):
    """Append-only audit store."""

    @staticmethod
    def _row(row: sa.Row[object]) -> audit.AuditEvent:
        """Maps a row.

        Args:
            row: Row.

        Returns:
            Audit event.
        """
        m = row._mapping
        return audit.AuditEvent(
            event_id=m["event_id"],
            tenant_id=m["tenant_id"],
            actor=m["actor"],
            action=m["action"],
            entity_type=m["entity_type"],
            entity_id=m["entity_id"],
            trace_id=m["trace_id"],
            ts_ms=m["ts_ms"],
            payload=json.loads(m["payload"]),
            prev_hash=m["prev_hash"],
            hash=m["hash"],
            account_id=m["account_id"],
        )

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
        return [self._row(r) for r in rows]

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
        return [self._row(r) for r in self.conn.execute(query)]


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
            m = row._mapping
            result.append(
                kill_switch.KillSwitch(
                    tenant_id=m["tenant_id"],
                    scope=enums.KillSwitchScope(m["scope"]),
                    target_id=m["target_id"],
                    engaged=bool(m["engaged"]),
                    cancel_open_orders=bool(m["cancel_open_orders"]),
                    flatten_positions=bool(m["flatten_positions"]),
                    reason=m["reason"],
                    actor=m["actor"],
                    changed_at_ms=m["changed_at_ms"],
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


class SqlDeployments(_Repo):
    """Deployments."""

    @staticmethod
    def _row(row: sa.Row[object]) -> deployments.Deployment:
        """Maps a row.

        Args:
            row: Row.

        Returns:
            Deployment.
        """
        m = row._mapping
        consent_raw = m["live_consent"]
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
            deployment_id=m["deployment_id"],
            tenant_id=m["tenant_id"],
            strategy_version_id=m["strategy_version_id"],
            account_id=m["account_id"],
            mode=enums.DeploymentMode(m["mode"]),
            state=enums.DeploymentState(m["state"]),
            limits=deployments.DeploymentLimits.from_contract(
                json.loads(m["limits"])
            ),
            sizing=deployments.SizingModel.from_contract(
                json.loads(m["sizing"])
            ),
            created_at_ms=m["created_at_ms"],
            updated_at_ms=m["updated_at_ms"],
            live_consent=consent,
        )

    @staticmethod
    def _values(deployment: deployments.Deployment) -> dict[str, object]:
        """Maps to column values.

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

    def add(self, deployment: deployments.Deployment) -> None:
        """Inserts.

        Args:
            deployment: Deployment.
        """
        self.conn.execute(
            schema.deployments.insert().values(self._values(deployment))
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
            .values(self._values(deployment))
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
        return None if row is None else self._row(row)

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
        return [self._row(r) for r in self.conn.execute(query)]


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
        m = row._mapping
        return accounts.LiveArming(
            tenant_id=m["tenant_id"],
            account_id=m["account_id"],
            granted_at_ms=m["granted_at_ms"],
            expires_at_ms=m["expires_at_ms"],
            granted_by=m["granted_by"],
            acknowledgement=m["acknowledgement"],
            scope_hash=m["scope_hash"],
            revoked_at_ms=m["revoked_at_ms"],
            revoked_by=m["revoked_by"],
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
        """Returns the active connection.

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
