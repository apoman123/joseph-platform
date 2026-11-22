"""Ports of the alerts application layer."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from joseph.alerts.domain import model


class SubscriptionRepository(Protocol):
    """Store of alert subscriptions."""

    def list_for_tenant(self, tenant_id: str) -> Sequence[model.Subscription]:
        """Lists a tenant's subscriptions.

        Args:
            tenant_id: Owning tenant.

        Returns:
            Subscriptions.
        """
        ...

    def upsert(self, subscription: model.Subscription) -> None:
        """Creates or replaces a subscription.

        Args:
            subscription: Subscription.
        """
        ...


class OutboxRepository(Protocol):
    """Durable at-least-once delivery outbox."""

    def enqueue(self, record: model.DeliveryRecord) -> bool:
        """Adds a delivery unless its idempotency key exists.

        Args:
            record: Pending delivery.

        Returns:
            True when added, False for a duplicate.
        """
        ...

    def due(
        self, now_ms: int, limit: int, lease_ms: int
    ) -> Sequence[model.DeliveryRecord]:
        """Claims pending deliveries whose next attempt is due.

        Claimed records are leased (their next attempt is pushed back by
        ``lease_ms``) so concurrent workers do not pick them up twice.

        Args:
            now_ms: Current time.
            limit: Maximum records.
            lease_ms: Lease duration.

        Returns:
            Due records.
        """
        ...

    def mark_delivered(
        self, delivery_id: str, now_ms: int, attempts: int
    ) -> None:
        """Marks a delivery as done.

        Args:
            delivery_id: Delivery id.
            now_ms: Delivery time.
            attempts: Attempts made including this one.
        """
        ...

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
            error: Redacted error.
            next_attempt_ms: When to retry.
            dead: Move to the dead-letter state instead of retrying.
        """
        ...

    def get(self, delivery_id: str) -> model.DeliveryRecord | None:
        """Fetches one delivery.

        Args:
            delivery_id: Delivery id.

        Returns:
            Record or ``None``.
        """
        ...

    def dead_letters(self, limit: int) -> Sequence[model.DeliveryRecord]:
        """Lists dead-lettered deliveries.

        Args:
            limit: Maximum records.

        Returns:
            Records.
        """
        ...


class ChannelSender(Protocol):
    """Delivers a record on one channel."""

    channel: model.Channel

    async def send(self, record: model.DeliveryRecord) -> None:
        """Delivers a record.

        Args:
            record: Delivery.

        Raises:
            model.DeliveryError: On failure (with a redacted message).
        """
        ...
