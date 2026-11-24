# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""SQLAlchemy Core persistence primitives: engine, outbox and inbox.

The transactional outbox stores events in the same transaction as the state
change that produced them; a relay delivers them at least once. The inbox
records processed idempotency keys so consumers can ignore redeliveries.
SQLite is used for development and tests; PostgreSQL works unchanged via
``JOSEPH_DATABASE_URL``.
"""

from __future__ import annotations

import dataclasses
import json
import pathlib
from collections.abc import Sequence

import sqlalchemy as sa

from joseph.domain import events

METADATA = sa.MetaData()

OUTBOX = sa.Table(
    "outbox",
    METADATA,
    sa.Column("seq", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("event_id", sa.String(64), nullable=False, unique=True),
    sa.Column("aggregate_id", sa.String(200), nullable=False),
    sa.Column("event_key", sa.String(200), nullable=False),
    sa.Column("event_type", sa.String(100), nullable=False),
    sa.Column("schema_version", sa.Integer, nullable=False),
    sa.Column("payload", sa.Text, nullable=False),
    sa.Column("ts_ms", sa.BigInteger, nullable=False),
    sa.Column("correlation_id", sa.String(64)),
    sa.Column("state", sa.String(16), nullable=False, default="PENDING"),
    sa.Column("attempts", sa.Integer, nullable=False, default=0),
    sa.Column("last_error", sa.String(500)),
    sa.UniqueConstraint("aggregate_id", "event_key", name="uq_outbox_key"),
)

INBOX = sa.Table(
    "inbox",
    METADATA,
    sa.Column("consumer", sa.String(100), primary_key=True),
    sa.Column("idempotency_key", sa.String(200), primary_key=True),
    sa.Column("processed_at_ms", sa.BigInteger, nullable=False),
)


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
    """Creates an engine and ensures platform tables exist.

    For file-based SQLite URLs the parent directory is created.

    Args:
        url: SQLAlchemy URL such as ``sqlite:///./var/market.db``.

    Returns:
        Configured engine.
    """
    kwargs: dict[str, object] = {"future": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if url in ("sqlite://", "sqlite:///:memory:"):
            kwargs["poolclass"] = sa.pool.StaticPool
        else:
            path = url.split("///", 1)[-1]
            pathlib.Path(path).parent.mkdir(parents=True, exist_ok=True)
    engine = sa.create_engine(url, **kwargs)
    if url.startswith("sqlite") and kwargs.get("poolclass") is None:
        _enable_sqlite_wal(engine)
    METADATA.create_all(engine)
    return engine


@dataclasses.dataclass(frozen=True, slots=True)
class OutboxRecord:
    """Stored outbox row.

    Attributes:
        seq: Monotonic row number.
        event: The stored event.
        aggregate_id: Owning aggregate.
        event_key: Logical key unique within the aggregate.
        state: ``PENDING``, ``DELIVERED`` or ``DEAD``.
        attempts: Delivery attempts so far.
    """

    seq: int
    event: events.DomainEvent
    aggregate_id: str
    event_key: str
    state: str
    attempts: int


class OutboxRepository:
    """Transactional outbox over SQLAlchemy Core."""

    def __init__(self, engine: sa.Engine, max_attempts: int = 8) -> None:
        """Initializes the repository.

        Args:
            engine: Engine with platform tables.
            max_attempts: Attempts before an event is moved to ``DEAD``.
        """
        self._engine = engine
        self._max_attempts = max_attempts

    def append(
        self,
        connection: sa.Connection,
        aggregate_id: str,
        event_key: str,
        event: events.DomainEvent,
    ) -> bool:
        """Appends an event inside the caller's transaction.

        Args:
            connection: Open connection within the business transaction.
            aggregate_id: Aggregate that produced the event.
            event_key: Logical key; duplicates are ignored.
            event: Event to store.

        Returns:
            True when inserted, False when the key already existed.
        """
        exists = connection.execute(
            sa.select(OUTBOX.c.seq).where(
                OUTBOX.c.aggregate_id == aggregate_id,
                OUTBOX.c.event_key == event_key,
            )
        ).first()
        if exists is not None:
            return False
        connection.execute(
            OUTBOX.insert().values(
                event_id=event.event_id,
                aggregate_id=aggregate_id,
                event_key=event_key,
                event_type=event.event_type,
                schema_version=event.schema_version,
                payload=json.dumps(dict(event.payload), sort_keys=True),
                ts_ms=event.ts_ms,
                correlation_id=event.correlation_id,
                state="PENDING",
                attempts=0,
            )
        )
        return True

    def pending(self, limit: int = 100) -> list[OutboxRecord]:
        """Lists undelivered events in insertion order.

        Args:
            limit: Maximum rows.

        Returns:
            Pending records.
        """
        with self._engine.connect() as connection:
            rows = connection.execute(
                sa.select(OUTBOX)
                .where(OUTBOX.c.state == "PENDING")
                .order_by(OUTBOX.c.seq)
                .limit(limit)
            ).mappings()
            return [self._to_record(row) for row in rows]

    def mark_delivered(self, seq: int) -> None:
        """Marks a record delivered.

        Args:
            seq: Row number.
        """
        with self._engine.begin() as connection:
            connection.execute(
                OUTBOX.update()
                .where(OUTBOX.c.seq == seq)
                .values(state="DELIVERED")
            )

    def mark_failed(self, seq: int, error: str) -> str:
        """Records a failed attempt, moving the row to DEAD when exhausted.

        Args:
            seq: Row number.
            error: Short failure description (never secrets).

        Returns:
            The new state.
        """
        with self._engine.begin() as connection:
            row = connection.execute(
                sa.select(OUTBOX.c.attempts).where(OUTBOX.c.seq == seq)
            ).one()
            attempts = int(row.attempts) + 1
            state = "DEAD" if attempts >= self._max_attempts else "PENDING"
            connection.execute(
                OUTBOX.update()
                .where(OUTBOX.c.seq == seq)
                .values(attempts=attempts, state=state, last_error=error[:500])
            )
            return state

    @staticmethod
    def _to_record(row: sa.RowMapping) -> OutboxRecord:
        """Maps a row to an ``OutboxRecord``.

        Args:
            row: Result row.

        Returns:
            The record.
        """
        event = events.DomainEvent(
            event_type=row["event_type"],
            payload=json.loads(row["payload"]),
            idempotency_key=f"{row['aggregate_id']}|{row['event_key']}",
            ts_ms=int(row["ts_ms"]),
            schema_version=int(row["schema_version"]),
            event_id=row["event_id"],
            correlation_id=row["correlation_id"],
        )
        return OutboxRecord(
            seq=int(row["seq"]),
            event=event,
            aggregate_id=row["aggregate_id"],
            event_key=row["event_key"],
            state=row["state"],
            attempts=int(row["attempts"]),
        )


class InboxRepository:
    """Idempotent-consumer inbox."""

    def __init__(self, engine: sa.Engine) -> None:
        """Initializes the repository.

        Args:
            engine: Engine with platform tables.
        """
        self._engine = engine

    def try_mark(
        self, consumer: str, idempotency_key: str, now_ms: int
    ) -> bool:
        """Records a key as processed unless already present.

        Args:
            consumer: Consumer name.
            idempotency_key: Event idempotency key.
            now_ms: Processing time.

        Returns:
            True on first processing, False for a duplicate.
        """
        with self._engine.begin() as connection:
            exists = connection.execute(
                sa.select(INBOX.c.consumer).where(
                    INBOX.c.consumer == consumer,
                    INBOX.c.idempotency_key == idempotency_key,
                )
            ).first()
            if exists is not None:
                return False
            connection.execute(
                INBOX.insert().values(
                    consumer=consumer,
                    idempotency_key=idempotency_key,
                    processed_at_ms=now_ms,
                )
            )
            return True

    def processed(self, consumer: str) -> Sequence[str]:
        """Lists processed keys for a consumer.

        Args:
            consumer: Consumer name.

        Returns:
            Keys in insertion order.
        """
        with self._engine.connect() as connection:
            rows = connection.execute(
                sa.select(INBOX.c.idempotency_key)
                .where(INBOX.c.consumer == consumer)
                .order_by(INBOX.c.processed_at_ms)
            )
            return [row.idempotency_key for row in rows]
