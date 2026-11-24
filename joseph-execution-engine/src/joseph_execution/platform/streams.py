# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Tenant-scoped user stream hub with sequence numbers and replay.

Each tenant has its own monotonic sequence and bounded replay ring. A
client resuming with ``last_sequence`` older than the ring receives
``snapshot_required`` and must refetch REST snapshots.
"""

from __future__ import annotations

import asyncio
import collections
import dataclasses
from collections.abc import Mapping

from joseph_execution.domain import ids
from joseph_execution.ports import services


@dataclasses.dataclass(slots=True)
class Subscription:
    """One connected client.

    Attributes:
        tenant_id: Tenant whose events it may receive.
        queue: Outgoing envelopes (unbounded).
        channels: Channel filter (empty = all).
        account_id: Optional account filter; events without an account
            always pass it.
    """

    tenant_id: str
    queue: asyncio.Queue[dict[str, object]]
    channels: set[str] = dataclasses.field(default_factory=set)
    account_id: str | None = None

    def wants(
        self, envelope: Mapping[str, object], account_id: str | None
    ) -> bool:
        """Checks the channel/account filters.

        Args:
            envelope: Envelope.
            account_id: Event account.

        Returns:
            True when the event should be delivered.
        """
        if self.channels and envelope.get("channel") not in self.channels:
            # Kill switch changes reach every client of the tenant, whatever
            # channels it subscribed to.
            return envelope.get("type") in ("risk.kill_switch",)
        return not (
            self.account_id and account_id and (account_id != self.account_id)
        )


class UserStreamHub:
    """In-process publisher implementing ``EventPublisher``.

    State lives in this process only: sequences and replay rings restart
    from zero with the process.
    """

    def __init__(self, clock: services.Clock, retention: int = 1000) -> None:
        """Initializes the hub.

        Args:
            clock: Time source.
            retention: Replay ring size per tenant, in envelopes.
        """
        self._clock = clock
        self._retention = retention
        self._sequence: dict[str, int] = {}
        self._rings: dict[
            str, collections.deque[tuple[dict[str, object], str | None]]
        ] = {}
        self._subscribers: list[Subscription] = []

    def envelope(
        self,
        tenant_id: str,
        event_type: str,
        channel: str | None,
        payload: Mapping[str, object],
        advance: bool = True,
    ) -> dict[str, object]:
        """Builds a contract ``StreamEnvelope``.

        Args:
            tenant_id: Tenant.
            event_type: Type.
            channel: Channel.
            payload: Payload.
            advance: Consume a sequence number. Control envelopes
                (heartbeats, acknowledgements, errors) pass False and carry
                the current sequence.

        Returns:
            Envelope.
        """
        seq = self._sequence.get(tenant_id, 0)
        if advance:
            seq += 1
            self._sequence[tenant_id] = seq
        return {
            "type": event_type,
            "event_id": ids.uuid7(),
            "schema_version": 1,
            "sequence": seq,
            "event_ts_ms": self._clock.now_ms(),
            "correlation_id": None,
            "channel": channel,
            "payload": dict(payload),
        }

    def publish(
        self,
        tenant_id: str,
        event_type: str,
        channel: str,
        payload: Mapping[str, object],
        account_id: str | None = None,
    ) -> None:
        """Publishes to the tenant's subscribers only.

        The envelope takes the tenant's next sequence number and is kept in
        the replay ring, so clients that reconnect can catch up.

        Args:
            tenant_id: Tenant.
            event_type: Type.
            channel: Channel.
            payload: Payload.
            account_id: Related account.
        """
        envelope = self.envelope(tenant_id, event_type, channel, payload)
        ring = self._rings.setdefault(
            tenant_id, collections.deque(maxlen=self._retention)
        )
        ring.append((envelope, account_id))
        for sub in list(self._subscribers):
            if sub.tenant_id == tenant_id and sub.wants(envelope, account_id):
                sub.queue.put_nowait(envelope)

    def subscribe(self, tenant_id: str) -> Subscription:
        """Registers a subscriber.

        Args:
            tenant_id: Tenant.

        Returns:
            Subscription.
        """
        sub = Subscription(tenant_id=tenant_id, queue=asyncio.Queue())
        self._subscribers.append(sub)
        return sub

    def unsubscribe(self, sub: Subscription) -> None:
        """Removes a subscriber.

        Args:
            sub: Subscription.
        """
        if sub in self._subscribers:
            self._subscribers.remove(sub)

    def replay(
        self, sub: Subscription, last_sequence: int
    ) -> list[dict[str, object]] | None:
        """Returns missed envelopes after ``last_sequence``.

        Args:
            sub: Subscription.
            last_sequence: Last sequence the client processed.

        Returns:
            The retained envelopes after ``last_sequence`` that pass the
            subscription's filters (empty when the client is up to date),
            or ``None`` when the gap exceeds retention and the client must
            refetch snapshots.
        """
        ring = self._rings.get(sub.tenant_id, collections.deque())
        current = self._sequence.get(sub.tenant_id, 0)
        if last_sequence >= current:
            return []
        oldest = int(str(ring[0][0]["sequence"])) if ring else current + 1
        if last_sequence + 1 < oldest:
            return None
        return [
            env
            for env, account in ring
            if int(str(env["sequence"])) > last_sequence
            and sub.wants(env, account)
        ]

    def current_sequence(self, tenant_id: str) -> int:
        """Returns the tenant's latest sequence.

        Args:
            tenant_id: Tenant.

        Returns:
            Sequence, 0 before the first event.
        """
        return self._sequence.get(tenant_id, 0)
