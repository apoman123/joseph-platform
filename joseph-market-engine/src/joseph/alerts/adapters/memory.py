# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""In-memory alert repositories."""

from __future__ import annotations

import dataclasses
import threading
from collections.abc import Sequence

from joseph.alerts.domain import model


class InMemorySubscriptionRepository:
    """In-memory ``SubscriptionRepository``."""

    def __init__(self) -> None:
        """Initializes an empty store."""
        self._rows: dict[str, model.Subscription] = {}

    def list_for_tenant(self, tenant_id: str) -> Sequence[model.Subscription]:
        """Lists a tenant's subscriptions.

        Args:
            tenant_id: Owning tenant.

        Returns:
            Subscriptions.
        """
        return [s for s in self._rows.values() if s.tenant_id == tenant_id]

    def upsert(self, subscription: model.Subscription) -> None:
        """Creates or replaces a subscription.

        Args:
            subscription: Subscription.
        """
        self._rows[subscription.subscription_id] = subscription


class InMemoryOutbox:
    """Thread-safe in-memory ``OutboxRepository``."""

    def __init__(self) -> None:
        """Initializes an empty outbox."""
        self._lock = threading.Lock()
        self._rows: dict[str, model.DeliveryRecord] = {}
        self._keys: set[str] = set()

    def enqueue(self, record: model.DeliveryRecord) -> bool:
        """Adds a delivery unless its idempotency key exists.

        Args:
            record: Pending delivery.

        Returns:
            True when added.
        """
        with self._lock:
            if record.idempotency_key in self._keys:
                return False
            self._keys.add(record.idempotency_key)
            self._rows[record.delivery_id] = record
            return True

    def due(
        self, now_ms: int, limit: int, lease_ms: int
    ) -> Sequence[model.DeliveryRecord]:
        """Claims due pending deliveries.

        Args:
            now_ms: Current time.
            limit: Maximum records.
            lease_ms: Lease duration.

        Returns:
            Claimed records (as they were before leasing).
        """
        with self._lock:
            due = sorted(
                (
                    r
                    for r in self._rows.values()
                    if r.status is model.DeliveryStatus.PENDING
                    and r.next_attempt_ms <= now_ms
                ),
                key=lambda r: (
                    r.next_attempt_ms,
                    r.created_at_ms,
                    r.delivery_id,
                ),
            )[:limit]
            for record in due:
                self._rows[record.delivery_id] = dataclasses.replace(
                    record, next_attempt_ms=now_ms + lease_ms
                )
            return due

    def mark_delivered(
        self, delivery_id: str, now_ms: int, attempts: int
    ) -> None:
        """Marks a delivery as done.

        Args:
            delivery_id: Delivery id.
            now_ms: Delivery time.
            attempts: Attempts made.
        """
        with self._lock:
            record = self._rows[delivery_id]
            self._rows[delivery_id] = dataclasses.replace(
                record,
                status=model.DeliveryStatus.DELIVERED,
                attempts=attempts,
                delivered_at_ms=now_ms,
                last_error=None,
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
            attempts: Attempts made.
            error: Redacted error.
            next_attempt_ms: Retry time.
            dead: Dead-letter the delivery.
        """
        with self._lock:
            record = self._rows[delivery_id]
            self._rows[delivery_id] = dataclasses.replace(
                record,
                status=(
                    model.DeliveryStatus.DEAD
                    if dead
                    else model.DeliveryStatus.PENDING
                ),
                attempts=attempts,
                last_error=error,
                next_attempt_ms=next_attempt_ms,
            )

    def get(self, delivery_id: str) -> model.DeliveryRecord | None:
        """Fetches one delivery.

        Args:
            delivery_id: Delivery id.

        Returns:
            Record or ``None``.
        """
        with self._lock:
            return self._rows.get(delivery_id)

    def dead_letters(self, limit: int) -> Sequence[model.DeliveryRecord]:
        """Lists dead deliveries.

        Args:
            limit: Maximum records.

        Returns:
            Records.
        """
        with self._lock:
            return [
                r
                for r in self._rows.values()
                if r.status is model.DeliveryStatus.DEAD
            ][:limit]

    def all(self) -> Sequence[model.DeliveryRecord]:
        """Lists every delivery (diagnostics and tests).

        Returns:
            Records sorted by id.
        """
        with self._lock:
            return sorted(self._rows.values(), key=lambda r: r.delivery_id)
