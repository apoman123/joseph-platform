# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""SQLAlchemy Core alert outbox and subscription store."""

from __future__ import annotations

import datetime
import json
from collections.abc import Mapping
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa

from joseph.alerts.domain import model

METADATA = sa.MetaData()
"""Schema of the alerts context."""

OUTBOX = sa.Table(
    "alert_outbox",
    METADATA,
    sa.Column("delivery_id", sa.String(64), primary_key=True),
    sa.Column("idempotency_key", sa.String(512), nullable=False, unique=True),
    sa.Column("tenant_id", sa.String(128), nullable=False),
    sa.Column("channel", sa.String(16), nullable=False),
    sa.Column("target", sa.String(512), nullable=False),
    sa.Column("payload_json", sa.Text, nullable=False),
    sa.Column("status", sa.String(16), nullable=False),
    sa.Column("attempts", sa.Integer, nullable=False),
    sa.Column("next_attempt_ms", sa.BigInteger, nullable=False),
    sa.Column("last_error", sa.Text, nullable=True),
    sa.Column("created_at_ms", sa.BigInteger, nullable=False),
    sa.Column("delivered_at_ms", sa.BigInteger, nullable=True),
    sa.Index("ix_alert_outbox_due", "status", "next_attempt_ms"),
)
"""One row per delivery of a signal to a channel and target."""

SUBSCRIPTIONS = sa.Table(
    "alert_subscriptions",
    METADATA,
    sa.Column("subscription_id", sa.String(64), primary_key=True),
    sa.Column("tenant_id", sa.String(128), nullable=False, index=True),
    sa.Column("body_json", sa.Text, nullable=False),
)
"""One row per subscription; the body is the whole subscription as JSON."""


def create_schema(engine: sa.Engine) -> None:
    """Creates the alert tables if missing.

    Args:
        engine: SQLAlchemy engine.
    """
    METADATA.create_all(engine)


def _to_record(row: sa.Row[tuple[object, ...]]) -> model.DeliveryRecord:
    """Maps an outbox row.

    Args:
        row: Outbox row.

    Returns:
        Delivery record.
    """
    data = row._mapping
    delivered = data["delivered_at_ms"]
    error = data["last_error"]
    return model.DeliveryRecord(
        delivery_id=str(data["delivery_id"]),
        idempotency_key=str(data["idempotency_key"]),
        tenant_id=str(data["tenant_id"]),
        channel=model.Channel(str(data["channel"])),
        target=str(data["target"]),
        payload=json.loads(str(data["payload_json"])),
        created_at_ms=int(data["created_at_ms"]),
        status=model.DeliveryStatus(str(data["status"])),
        attempts=int(data["attempts"]),
        next_attempt_ms=int(data["next_attempt_ms"]),
        last_error=None if error is None else str(error),
        delivered_at_ms=None if delivered is None else int(delivered),
    )


class SqlOutbox:
    """``OutboxRepository`` backed by SQLAlchemy Core.

    Attributes:
        engine: SQLAlchemy engine.
    """

    def __init__(self, engine: sa.Engine) -> None:
        """Initializes the outbox.

        Args:
            engine: SQLAlchemy engine.
        """
        self.engine = engine

    def enqueue(self, record: model.DeliveryRecord) -> bool:
        """Adds a delivery unless its idempotency key exists.

        Args:
            record: Pending delivery.

        Returns:
            True when added, False when the idempotency key or delivery id
            already exists.
        """
        try:
            with self.engine.begin() as conn:
                conn.execute(
                    OUTBOX.insert().values(
                        delivery_id=record.delivery_id,
                        idempotency_key=record.idempotency_key,
                        tenant_id=record.tenant_id,
                        channel=record.channel.value,
                        target=record.target,
                        payload_json=json.dumps(
                            dict(record.payload), sort_keys=True
                        ),
                        status=record.status.value,
                        attempts=record.attempts,
                        next_attempt_ms=record.next_attempt_ms,
                        last_error=record.last_error,
                        created_at_ms=record.created_at_ms,
                        delivered_at_ms=record.delivered_at_ms,
                    )
                )
        except sa.exc.IntegrityError:
            return False
        return True

    def due(
        self, now_ms: int, limit: int, lease_ms: int
    ) -> Sequence[model.DeliveryRecord]:
        """Claims due deliveries inside one transaction.

        Each lease update matches the ``next_attempt_ms`` that was read, so
        it does not extend a lease another worker took meanwhile. The record
        is returned either way: delivery stays at least once.

        Args:
            now_ms: Current time in UTC epoch ms.
            limit: Maximum records.
            lease_ms: Lease duration in ms.

        Returns:
            Claimed records as read before the lease, oldest due first.
        """
        query = (
            sa.select(OUTBOX)
            .where(
                OUTBOX.c.status == model.DeliveryStatus.PENDING.value,
                OUTBOX.c.next_attempt_ms <= now_ms,
            )
            .order_by(
                OUTBOX.c.next_attempt_ms,
                OUTBOX.c.created_at_ms,
                OUTBOX.c.delivery_id,
            )
            .limit(limit)
        )
        with self.engine.begin() as conn:
            records = [_to_record(row) for row in conn.execute(query)]
            for record in records:
                conn.execute(
                    OUTBOX.update()
                    .where(
                        OUTBOX.c.delivery_id == record.delivery_id,
                        OUTBOX.c.next_attempt_ms == record.next_attempt_ms,
                    )
                    .values(next_attempt_ms=now_ms + lease_ms)
                )
        return records

    def mark_delivered(
        self, delivery_id: str, now_ms: int, attempts: int
    ) -> None:
        """Marks a delivery as done.

        Args:
            delivery_id: Delivery id.
            now_ms: Delivery time in UTC epoch ms.
            attempts: Attempts made including this one.
        """
        with self.engine.begin() as conn:
            conn.execute(
                OUTBOX.update()
                .where(OUTBOX.c.delivery_id == delivery_id)
                .values(
                    status=model.DeliveryStatus.DELIVERED.value,
                    attempts=attempts,
                    delivered_at_ms=now_ms,
                    last_error=None,
                )
            )

    def mark_failed(
        self,
        delivery_id: str,
        *,
        attempts: int,
        error: str,
        next_attempt_ms: int,
        dead: bool,
    ) -> None:
        """Records a failed attempt.

        Args:
            delivery_id: Delivery id.
            attempts: Attempts made including this one.
            error: Redacted error; the first 2,000 characters are stored.
            next_attempt_ms: Retry time in UTC epoch ms.
            dead: Dead-letter the delivery.
        """
        status = (
            model.DeliveryStatus.DEAD if dead else model.DeliveryStatus.PENDING
        )
        with self.engine.begin() as conn:
            conn.execute(
                OUTBOX.update()
                .where(OUTBOX.c.delivery_id == delivery_id)
                .values(
                    status=status.value,
                    attempts=attempts,
                    last_error=error[:2000],
                    next_attempt_ms=next_attempt_ms,
                )
            )

    def get(self, delivery_id: str) -> model.DeliveryRecord | None:
        """Fetches one delivery.

        Args:
            delivery_id: Delivery id.

        Returns:
            The record, or ``None`` for an unknown id.
        """
        with self.engine.connect() as conn:
            row = conn.execute(
                sa.select(OUTBOX).where(OUTBOX.c.delivery_id == delivery_id)
            ).first()
        return None if row is None else _to_record(row)

    def dead_letters(self, limit: int) -> Sequence[model.DeliveryRecord]:
        """Lists dead deliveries.

        Args:
            limit: Maximum records.

        Returns:
            Up to ``limit`` dead records, oldest first.
        """
        query = (
            sa.select(OUTBOX)
            .where(OUTBOX.c.status == model.DeliveryStatus.DEAD.value)
            .order_by(OUTBOX.c.created_at_ms)
            .limit(limit)
        )
        with self.engine.connect() as conn:
            return [_to_record(row) for row in conn.execute(query)]


def _quiet_json(quiet: model.QuietHours | None) -> dict[str, object] | None:
    """Converts quiet hours to their stored JSON object.

    Args:
        quiet: Quiet hours, or ``None``.

    Returns:
        The JSON object (ISO times), or ``None`` without quiet hours.
    """
    if quiet is None:
        return None
    return {
        "start": quiet.start.isoformat(),
        "end": quiet.end.isoformat(),
        "tz": quiet.tz,
        "bypass_severities": sorted(quiet.bypass_severities),
    }


def _subscription_json(subscription: model.Subscription) -> str:
    """Serializes a subscription.

    Args:
        subscription: Subscription.

    Returns:
        JSON text with sorted keys and sets as sorted lists.
    """
    return json.dumps(
        {
            "subscription_id": subscription.subscription_id,
            "tenant_id": subscription.tenant_id,
            "channel": subscription.channel.value,
            "target": subscription.target,
            "kinds": sorted(subscription.kinds),
            "min_severity": subscription.min_severity,
            "strategy_ids": sorted(subscription.strategy_ids),
            "enabled": subscription.enabled,
            "quiet_hours": _quiet_json(subscription.quiet_hours),
        },
        sort_keys=True,
    )


def _quiet_from_json(
    quiet: Mapping[str, Any] | None,
) -> model.QuietHours | None:
    """Restores quiet hours from their stored JSON object.

    Args:
        quiet: Stored object, or ``None``.

    Returns:
        Quiet hours, or ``None`` without them.
    """
    if quiet is None:
        return None
    return model.QuietHours(
        start=datetime.time.fromisoformat(quiet["start"]),
        end=datetime.time.fromisoformat(quiet["end"]),
        tz=quiet.get("tz", "UTC"),
        bypass_severities=frozenset(quiet.get("bypass_severities", [])),
    )


def _subscription_from_json(text: str) -> model.Subscription:
    """Parses a stored subscription.

    Fields missing from older rows take the model defaults.

    Args:
        text: JSON text.

    Returns:
        Subscription.
    """
    data = json.loads(text)
    return model.Subscription(
        subscription_id=data["subscription_id"],
        tenant_id=data["tenant_id"],
        channel=model.Channel(data["channel"]),
        target=data["target"],
        kinds=frozenset(data.get("kinds", [])),
        min_severity=data.get("min_severity", "INFO"),
        strategy_ids=frozenset(data.get("strategy_ids", [])),
        enabled=bool(data.get("enabled", True)),
        quiet_hours=_quiet_from_json(data.get("quiet_hours")),
    )


class SqlSubscriptionRepository:
    """``SubscriptionRepository`` backed by SQLAlchemy Core.

    Attributes:
        engine: SQLAlchemy engine.
    """

    def __init__(self, engine: sa.Engine) -> None:
        """Initializes the repository.

        Args:
            engine: SQLAlchemy engine.
        """
        self.engine = engine

    def list_for_tenant(self, tenant_id: str) -> Sequence[model.Subscription]:
        """Lists a tenant's subscriptions.

        Args:
            tenant_id: Owning tenant.

        Returns:
            The tenant's subscriptions in no particular order.
        """
        query = sa.select(SUBSCRIPTIONS.c.body_json).where(
            SUBSCRIPTIONS.c.tenant_id == tenant_id
        )
        with self.engine.connect() as conn:
            return [
                _subscription_from_json(str(row[0]))
                for row in conn.execute(query)
            ]

    def upsert(self, subscription: model.Subscription) -> None:
        """Creates or replaces a subscription by its id.

        Updates first and inserts when no row matched, in one transaction.

        Args:
            subscription: Subscription.
        """
        body = _subscription_json(subscription)
        with self.engine.begin() as conn:
            updated = conn.execute(
                SUBSCRIPTIONS.update()
                .where(
                    SUBSCRIPTIONS.c.subscription_id
                    == subscription.subscription_id
                )
                .values(tenant_id=subscription.tenant_id, body_json=body)
            )
            if updated.rowcount == 0:
                conn.execute(
                    SUBSCRIPTIONS.insert().values(
                        subscription_id=subscription.subscription_id,
                        tenant_id=subscription.tenant_id,
                        body_json=body,
                    )
                )
