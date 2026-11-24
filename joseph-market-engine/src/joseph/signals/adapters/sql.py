# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""SQLAlchemy Core signal repository with a unique dedupe key."""

from __future__ import annotations

import json
from collections.abc import Sequence

import sqlalchemy as sa

from joseph.signals.app import ports
from joseph.signals.domain import signal as signal_domain

METADATA = sa.MetaData()
"""Schema of the signals context."""

SIGNALS = sa.Table(
    "signals",
    METADATA,
    sa.Column("signal_id", sa.String(64), primary_key=True),
    sa.Column("tenant_id", sa.String(128), nullable=False),
    sa.Column("dedupe_key", sa.String(512), nullable=False),
    sa.Column("strategy_id", sa.String(64), nullable=False),
    sa.Column("strategy_version_id", sa.String(80), nullable=False),
    sa.Column("instrument_id", sa.String(96), nullable=False),
    sa.Column("kind", sa.String(32), nullable=False),
    sa.Column("bar_close_ms", sa.BigInteger, nullable=False),
    sa.Column("body_json", sa.Text, nullable=False),
    sa.UniqueConstraint("tenant_id", "dedupe_key", name="uq_signals_dedupe"),
    sa.Index("ix_signals_tenant_close", "tenant_id", "bar_close_ms"),
)
"""One row per signal: filter columns plus the contract body as JSON."""


def create_schema(engine: sa.Engine) -> None:
    """Creates the signals table if missing.

    Args:
        engine: SQLAlchemy engine.
    """
    METADATA.create_all(engine)


class SqlSignalRepository:
    """``SignalRepository`` backed by SQLAlchemy Core.

    Attributes:
        engine: SQLAlchemy engine.
    """

    def __init__(self, engine: sa.Engine) -> None:
        """Initializes the repository.

        Args:
            engine: SQLAlchemy engine.
        """
        self.engine = engine

    def add(self, signal: signal_domain.Signal) -> bool:
        """Stores a signal once per ``(tenant, dedupe_key)``.

        Args:
            signal: Signal.

        Returns:
            True when stored, False for a duplicate (same dedupe key or
            signal id).
        """
        try:
            with self.engine.begin() as conn:
                conn.execute(
                    SIGNALS.insert().values(
                        signal_id=signal.signal_id,
                        tenant_id=signal.tenant_id,
                        dedupe_key=signal.dedupe_key,
                        strategy_id=signal.strategy_id,
                        strategy_version_id=signal.strategy_version_id,
                        instrument_id=signal.instrument_id,
                        kind=signal.kind,
                        bar_close_ms=signal.bar_close_ms,
                        body_json=json.dumps(
                            signal.to_contract(), sort_keys=True
                        ),
                    )
                )
        except sa.exc.IntegrityError:
            return False
        return True

    def get(
        self, tenant_id: str, signal_id: str
    ) -> signal_domain.Signal | None:
        """Fetches one signal.

        Args:
            tenant_id: Owning tenant.
            signal_id: Signal id.

        Returns:
            The signal, or ``None`` when absent or owned by another tenant.
        """
        query = sa.select(SIGNALS.c.body_json).where(
            SIGNALS.c.tenant_id == tenant_id, SIGNALS.c.signal_id == signal_id
        )
        with self.engine.connect() as conn:
            row = conn.execute(query).first()
        if row is None:
            return None
        return signal_domain.Signal.from_contract(json.loads(str(row[0])))

    def query(
        self, tenant_id: str, filters: ports.SignalFilter
    ) -> Sequence[signal_domain.Signal]:
        """Lists matching signals newest first.

        Args:
            tenant_id: Owning tenant.
            filters: Filters.

        Returns:
            At most ``filters.limit`` signals, by bar close descending and
            then by id.
        """
        table = SIGNALS
        conditions = [table.c.tenant_id == tenant_id]
        if filters.strategy_id:
            conditions.append(table.c.strategy_id == filters.strategy_id)
        if filters.kinds:
            conditions.append(table.c.kind.in_(filters.kinds))
        if filters.instrument_id:
            conditions.append(table.c.instrument_id == filters.instrument_id)
        if filters.from_ms is not None:
            conditions.append(table.c.bar_close_ms >= filters.from_ms)
        if filters.to_ms is not None:
            conditions.append(table.c.bar_close_ms < filters.to_ms)
        query = (
            sa.select(table.c.body_json)
            .where(*conditions)
            .order_by(table.c.bar_close_ms.desc(), table.c.signal_id)
            .limit(filters.limit)
        )
        with self.engine.connect() as conn:
            return [
                signal_domain.Signal.from_contract(json.loads(str(row[0])))
                for row in conn.execute(query)
            ]
