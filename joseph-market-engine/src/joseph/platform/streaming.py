# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Resumable publish/subscribe hub behind the market WebSocket.

Every published envelope receives a hub-wide monotonic ``sequence`` and is
kept in a bounded retention ring. Clients reconnect with ``last_sequence``;
the hub replays newer envelopes for their channels or answers
``snapshot_required`` when the ring no longer covers the gap. Sequences
live in memory and start again from 0 when the engine restarts, so a
``last_sequence`` newer than the newest buffered sequence means the server
restarted and requires a snapshot, like one older than the ring.
Tenant-scoped envelopes (signals) are delivered only to subscribers of the
same tenant.

Channel keys:
    ``candles:{instrument_id}:{timeframe}``, ``feed_health``, ``signals``.
"""

from __future__ import annotations

import asyncio
import collections
import dataclasses
from collections.abc import Awaitable
from collections.abc import Callable
from collections.abc import Iterable
from collections.abc import Mapping
from typing import TypeAlias

from joseph.domain import clock as clock_lib
from joseph.domain import ids

# Coroutine function awaited with a channel key when the channel gains its
# first or loses its last subscriber.
ChannelHook: TypeAlias = Callable[[str], Awaitable[None]]

# Channels whose envelopes must carry a tenant id and reach only that
# tenant's subscribers.
TENANT_SCOPED_CHANNELS = frozenset({"signals"})


def channel_key(spec: Mapping[str, object]) -> str:
    """Builds the hub key for a contract ``ChannelSpec``.

    Args:
        spec: Mapping with ``channel`` and optional ``instrument_id`` and
            ``timeframe``.

    Returns:
        Hub channel key.

    Raises:
        ValueError: If a series channel lacks instrument or timeframe.
    """
    channel = str(spec.get("channel", ""))
    if channel == "candles":
        instrument = spec.get("instrument_id")
        timeframe = spec.get("timeframe")
        if not instrument or not timeframe:
            raise ValueError(f"{channel} requires instrument_id and timeframe")
        return f"{channel}:{instrument}:{timeframe}"
    if channel in ("feed_health", "signals"):
        return channel
    raise ValueError(f"unsupported market channel: {channel!r}")


def candle_channel(
    instrument_id: str, timeframe: str, feed_mode: str = "AUTO"
) -> str:
    """Builds the candle channel key, keeping feed modes apart.

    ``AUTO`` uses the plain contract key; other modes append the mode so a
    polled or replayed series is never mixed into a live channel.

    Args:
        instrument_id: Canonical instrument id.
        timeframe: Timeframe value.
        feed_mode: Contract ``FeedMode`` value.

    Returns:
        Channel key.
    """
    key = f"candles:{instrument_id}:{timeframe}"
    return key if feed_mode == "AUTO" else f"{key}:{feed_mode}"


@dataclasses.dataclass(frozen=True, slots=True)
class Envelope:
    """Contract ``StreamEnvelope``.

    Attributes:
        type: Event type such as ``candle.updated``.
        sequence: Hub-wide monotonic sequence (0 for control messages).
        event_ts_ms: Hub clock time when the envelope was built, epoch
            milliseconds.
        payload: JSON-compatible body.
        channel: Channel key or ``None`` for control messages.
        tenant_id: Tenant that may receive the envelope, or ``None`` for
            every subscriber of the channel.
        event_id: Unique id (UUIDv7).
        correlation_id: Optional correlation id.
    """

    type: str
    sequence: int
    event_ts_ms: int
    payload: Mapping[str, object]
    channel: str | None = None
    tenant_id: str | None = None
    event_id: str = dataclasses.field(default_factory=ids.uuid7)
    correlation_id: str | None = None

    def to_contract(self) -> dict[str, object]:
        """Serializes to the contract ``StreamEnvelope``.

        Returns:
            JSON-compatible dict (the tenant id is never exposed).
        """
        return {
            "type": self.type,
            "event_id": self.event_id,
            "schema_version": 1,
            "sequence": self.sequence,
            "event_ts_ms": self.event_ts_ms,
            "correlation_id": self.correlation_id,
            "channel": self.channel,
            "payload": dict(self.payload),
        }


class Subscription:
    """One client's live subscription.

    Attributes:
        channels: Subscribed channel keys.
        tenant_id: Authenticated tenant, or ``None`` for anonymous.
        queue: Pending envelopes.
        overflowed: Set when the client fell behind and must resnapshot.
    """

    def __init__(self, tenant_id: str | None, max_queue: int) -> None:
        """Initializes the subscription.

        Args:
            tenant_id: Authenticated tenant or ``None``.
            max_queue: Queue bound before the client is marked overflowed.
        """
        self.channels: set[str] = set()
        self.tenant_id = tenant_id
        self.queue: asyncio.Queue[Envelope] = asyncio.Queue(maxsize=max_queue)
        self.overflowed = False

    def accepts(self, envelope: Envelope) -> bool:
        """Checks channel membership and tenant visibility.

        Args:
            envelope: Candidate envelope.

        Returns:
            True when the envelope may be delivered.
        """
        if envelope.channel not in self.channels:
            return False
        if envelope.tenant_id is not None:
            return envelope.tenant_id == self.tenant_id
        return True

    def offer(self, envelope: Envelope) -> None:
        """Queues an envelope or marks the subscription overflowed.

        Never blocks. Once overflowed, every envelope is dropped until the
        consumer clears ``overflowed`` (after telling the client to
        resnapshot).

        Args:
            envelope: Envelope to deliver.
        """
        if self.overflowed:
            return
        try:
            self.queue.put_nowait(envelope)
        except asyncio.QueueFull:
            self.overflowed = True


class StreamHub:
    """Sequence-numbered fan-out with a retention ring.

    The hub holds no locks: use it from the event loop thread only.
    """

    def __init__(
        self,
        clock: clock_lib.Clock,
        retention: int = 2_000,
        max_queue: int = 1_000,
    ) -> None:
        """Initializes the hub.

        Args:
            clock: Time source.
            retention: Envelopes retained for resume, across all channels.
            max_queue: Per-subscriber queue bound; a subscriber with a full
                queue is marked overflowed.
        """
        self._clock = clock
        self._ring: collections.deque[Envelope] = collections.deque(
            maxlen=retention
        )
        self._sequence = 0
        self._subscriptions: set[Subscription] = set()
        self._max_queue = max_queue
        self._channel_counts: collections.Counter[str] = collections.Counter()
        self._on_open: list[ChannelHook] = []
        self._on_close: list[ChannelHook] = []

    @property
    def last_sequence(self) -> int:
        """The most recently assigned sequence (0 before any publish)."""
        return self._sequence

    def add_channel_hooks(
        self,
        on_open: ChannelHook | None = None,
        on_close: ChannelHook | None = None,
    ) -> None:
        """Registers callbacks for the first/last subscriber of a channel.

        Args:
            on_open: Awaited when a channel gains its first subscriber.
            on_close: Awaited when a channel loses its last subscriber.
        """
        if on_open is not None:
            self._on_open.append(on_open)
        if on_close is not None:
            self._on_close.append(on_close)

    def publish(
        self,
        channel: str,
        event_type: str,
        payload: Mapping[str, object],
        tenant_id: str | None = None,
        correlation_id: str | None = None,
    ) -> Envelope:
        """Publishes an envelope to a channel.

        Publishing never waits for subscribers: each matching subscriber's
        queue receives the envelope or the subscriber is marked overflowed.

        Args:
            channel: Channel key (see module docstring).
            event_type: Contract ``StreamEventType``.
            payload: JSON-compatible body.
            tenant_id: Tenant allowed to receive the envelope; required for
                tenant-scoped channels, and restricts delivery on any
                channel.
            correlation_id: Optional correlation id.

        Returns:
            The sequenced envelope.

        Raises:
            ValueError: If a tenant-scoped channel lacks a tenant id.
        """
        if channel in TENANT_SCOPED_CHANNELS and tenant_id is None:
            raise ValueError(f"channel {channel!r} requires tenant_id")
        self._sequence += 1
        envelope = Envelope(
            type=event_type,
            sequence=self._sequence,
            event_ts_ms=self._clock.now_ms(),
            payload=payload,
            channel=channel,
            tenant_id=tenant_id,
            correlation_id=correlation_id,
        )
        self._ring.append(envelope)
        for subscription in list(self._subscriptions):
            if subscription.accepts(envelope):
                subscription.offer(envelope)
        return envelope

    def open(self, tenant_id: str | None) -> Subscription:
        """Opens a subscription with no channels.

        Args:
            tenant_id: Authenticated tenant or ``None``.

        Returns:
            The subscription.
        """
        subscription = Subscription(tenant_id, self._max_queue)
        self._subscriptions.add(subscription)
        return subscription

    async def add_channels(
        self, subscription: Subscription, channels: Iterable[str]
    ) -> None:
        """Adds channels to a subscription, firing open hooks.

        Args:
            subscription: Target subscription.
            channels: Channel keys.
        """
        for channel in channels:
            if channel in subscription.channels:
                continue
            subscription.channels.add(channel)
            self._channel_counts[channel] += 1
            if self._channel_counts[channel] == 1:
                for hook in self._on_open:
                    await hook(channel)

    async def remove_channels(
        self, subscription: Subscription, channels: Iterable[str]
    ) -> None:
        """Removes channels from a subscription, firing close hooks.

        Args:
            subscription: Target subscription.
            channels: Channel keys.
        """
        for channel in list(channels):
            if channel not in subscription.channels:
                continue
            subscription.channels.discard(channel)
            self._channel_counts[channel] -= 1
            if self._channel_counts[channel] <= 0:
                del self._channel_counts[channel]
                for hook in self._on_close:
                    await hook(channel)

    async def close(self, subscription: Subscription) -> None:
        """Closes a subscription and releases its channels.

        Args:
            subscription: Subscription to close.
        """
        await self.remove_channels(subscription, list(subscription.channels))
        self._subscriptions.discard(subscription)

    def replay(
        self, subscription: Subscription, last_sequence: int
    ) -> list[Envelope] | None:
        """Returns retained envelopes newer than ``last_sequence``.

        A ``last_sequence`` newer than the newest buffered sequence means the
        server restarted and requires a snapshot: the client's sequence
        belongs to an earlier run, whose envelopes are gone.

        Args:
            subscription: Subscription whose channels/tenant filter apply.
            last_sequence: Last sequence the client processed.

        Returns:
            Envelopes to resend (empty when the client is up to date), or
            ``None`` when the client must refetch REST snapshots: the
            retention ring no longer covers the gap, or ``last_sequence``
            is newer than the hub's newest sequence (the server restarted).
        """
        if last_sequence > self._sequence:
            return None
        if last_sequence == self._sequence:
            return []
        oldest = self._ring[0].sequence if self._ring else self._sequence + 1
        if last_sequence + 1 < oldest:
            return None
        return [
            envelope
            for envelope in self._ring
            if envelope.sequence > last_sequence
            and subscription.accepts(envelope)
        ]

    def active_channels(self) -> set[str]:
        """Returns channels with at least one subscriber.

        Returns:
            Channel keys.
        """
        return set(self._channel_counts)

    def control(
        self, event_type: str, payload: Mapping[str, object]
    ) -> Envelope:
        """Builds an unsequenced control envelope.

        Control messages (``subscribed``, ``heartbeat``, ``error``,
        ``snapshot_required`` and initial snapshots) carry ``sequence=0``;
        clients advance their resume point only from sequenced envelopes.

        Args:
            event_type: Contract event type.
            payload: Body.

        Returns:
            Envelope with ``sequence`` 0.
        """
        return Envelope(
            type=event_type,
            sequence=0,
            event_ts_ms=self._clock.now_ms(),
            payload=payload,
        )
