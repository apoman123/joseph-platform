# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Ports of the alerts application layer."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from joseph.alerts.domain import model


class SubscriptionRepository(Protocol):
    """A store of alert subscriptions."""

    def list_for_tenant(self, tenant_id: str) -> Sequence[model.Subscription]:
        """Lists a tenant's subscriptions.

        Args:
            tenant_id: Owning tenant.

        Returns:
            Every subscription of the tenant, enabled or not, in no
            particular order; empty when there are none.
        """
        ...

    def upsert(self, subscription: model.Subscription) -> None:
        """Creates or replaces a subscription by its id.

        Args:
            subscription: Subscription.
        """
        ...


class OutboxRepository(Protocol):
    """A durable at-least-once delivery outbox."""

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
            now_ms: Current time in UTC epoch ms.
            limit: Maximum records.
            lease_ms: Lease duration in ms.

        Returns:
            Due records as read before the lease, oldest due first.
        """
        ...

    def mark_delivered(
        self, delivery_id: str, now_ms: int, attempts: int
    ) -> None:
        """Marks a delivery as done.

        Args:
            delivery_id: Delivery id.
            now_ms: Delivery time in UTC epoch ms.
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
            next_attempt_ms: When to retry, in UTC epoch ms.
            dead: Move to the dead-letter state instead of retrying.
        """
        ...

    def get(self, delivery_id: str) -> model.DeliveryRecord | None:
        """Fetches one delivery.

        Args:
            delivery_id: Delivery id.

        Returns:
            The record, or ``None`` for an unknown id.
        """
        ...

    def dead_letters(self, limit: int) -> Sequence[model.DeliveryRecord]:
        """Lists dead-lettered deliveries.

        Args:
            limit: Maximum records.

        Returns:
            Up to ``limit`` dead records, oldest first.
        """
        ...


class ChannelSender(Protocol):
    """A sender that delivers outbox records on one channel.

    Attributes:
        channel: The channel this sender serves.
    """

    channel: model.Channel

    async def send(self, record: model.DeliveryRecord) -> None:
        """Delivers a record.

        Args:
            record: Delivery.

        Raises:
            model.DeliveryError: On failure, with a redacted message and
                whether a retry may succeed.
        """
        ...
