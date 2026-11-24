# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Signal routing into the outbox and the outbox delivery worker.

``AlertDispatcher`` turns each newly recorded signal into durable outbox
records (one per channel and target); ``OutboxWorker`` delivers them later,
so a slow or failing channel never blocks signal recording.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import logging
from collections.abc import Callable
from collections.abc import Mapping
from collections.abc import Sequence
from typing import TypeAlias

from joseph.alerts.app import ports
from joseph.alerts.domain import model
from joseph.domain import clock as clock_port
from joseph.signals.domain import signal as signal_domain

_LOG = logging.getLogger(__name__)

ChannelPolicy: TypeAlias = Callable[[str, str], frozenset[str]]
"""``(tenant_id, strategy_version_id) -> allowed channel names``."""


def default_channels(
    tenant_id: str, strategy_version_id: str
) -> frozenset[str]:
    """Returns the default channel policy: UI only.

    Args:
        tenant_id: Tenant (unused).
        strategy_version_id: Strategy version (unused).

    Returns:
        ``{"UI"}``.
    """
    del tenant_id, strategy_version_id
    return frozenset({"UI"})


class AlertDispatcher:
    """Routes newly recorded signals into the durable outbox.

    Implements the strategy ``SignalPublisher`` port.

    Attributes:
        outbox: Delivery outbox.
        subscriptions: Subscription store.
    """

    def __init__(
        self,
        outbox: ports.OutboxRepository,
        subscriptions: ports.SubscriptionRepository,
        clock: clock_port.Clock,
        *,
        strategy_channels: ChannelPolicy = default_channels,
        ui_enabled: bool = True,
        forward_execution: bool = False,
    ) -> None:
        """Initializes the dispatcher.

        Args:
            outbox: Delivery outbox.
            subscriptions: Subscription store.
            clock: Time source.
            strategy_channels: Channel names each strategy version allows;
                a subscription on any other channel is suppressed.
            ui_enabled: Whether to enqueue the implicit UI delivery (target
                ``tenant:<tenant_id>``) when the strategy allows UI.
            forward_execution: Whether entries of PAPER/SHADOW/AUTO_LIVE
                strategies are forwarded to the execution engine.
        """
        self.outbox = outbox
        self.subscriptions = subscriptions
        self._clock = clock
        self._channels = strategy_channels
        self._ui_enabled = ui_enabled
        self._forward = forward_execution

    async def publish(self, signal: signal_domain.Signal) -> None:
        """Routes one signal (idempotent per channel and target).

        Args:
            signal: Newly recorded signal.
        """
        self.route(signal)

    def route(self, signal: signal_domain.Signal) -> model.RouteResult:
        """Routes a signal and enqueues its deliveries.

        Deliveries already in the outbox (same idempotency key) are skipped
        by the outbox; suppressed subscriptions are logged at INFO.

        Args:
            signal: Signal.

        Returns:
            Routing result, including deliveries that were duplicates.
        """
        now = self._clock.now_ms()
        ui_target: str | None = None
        if self._ui_enabled:
            ui_target = f"tenant:{signal.tenant_id}"
        result = model.route(
            signal,
            self.subscriptions.list_for_tenant(signal.tenant_id),
            self._channels(signal.tenant_id, signal.strategy_version_id),
            now,
            ui_target=ui_target,
            forward_execution=self._forward,
        )
        for record in result.deliveries:
            self.outbox.enqueue(record)
        for subscription_id, reason in result.suppressed:
            _LOG.info(
                "signal %s suppressed for %s: %s",
                signal.signal_id,
                subscription_id,
                reason,
            )
        return result


class FanoutPublisher:
    """A publisher that hands each signal to several publishers in order.

    Attributes:
        publishers: Downstream publishers.
    """

    def __init__(self, publishers: Sequence[object]) -> None:
        """Initializes the fan-out.

        Args:
            publishers: Objects with ``async publish(signal)``.
        """
        self.publishers = list(publishers)

    async def publish(self, signal: signal_domain.Signal) -> None:
        """Publishes to every downstream publisher.

        A failing publisher is logged and does not stop the others.

        Args:
            signal: Signal.
        """
        for publisher in self.publishers:
            try:
                await publisher.publish(signal)  # type: ignore[attr-defined]  # publishers are typed as object; each has async publish(signal)
            except Exception:  # noqa: BLE001 - isolation point: one failing publisher must not stop the others
                _LOG.exception("publisher %s failed", type(publisher).__name__)


@dataclasses.dataclass(frozen=True, slots=True)
class DeliveryStats:
    """The counts of one outbox worker pass.

    Attributes:
        delivered: Successful deliveries.
        retried: Failures scheduled for retry.
        dead: Deliveries moved to the dead-letter state.
    """

    delivered: int = 0
    retried: int = 0
    dead: int = 0


class OutboxWorker:
    """Delivers due outbox records with retries and a dead-letter state.

    ``start`` runs passes in a background task; ``stop`` asks the loop to end
    and waits for the pass in progress to finish. ``stop`` does not cancel
    the task, so a send in flight completes (each HTTP sender times out
    after 10 s).

    Attributes:
        outbox: Delivery outbox.
        max_attempts: Attempts after which a failing delivery is
            dead-lettered.
    """

    def __init__(
        self,
        outbox: ports.OutboxRepository,
        senders: Mapping[model.Channel, ports.ChannelSender],
        clock: clock_port.Clock,
        *,
        max_attempts: int = 6,
        base_backoff_ms: int = 2_000,
        batch_size: int = 50,
        lease_ms: int = 30_000,
    ) -> None:
        """Initializes the worker.

        Args:
            outbox: Delivery outbox.
            senders: Sender per channel; records of a channel without one
                are dead-lettered.
            clock: Time source.
            max_attempts: Attempts after which a failing delivery is
                dead-lettered.
            base_backoff_ms: First retry delay in ms; it doubles per attempt
                up to 5 minutes.
            batch_size: Maximum records claimed per pass.
            lease_ms: How long a claimed record stays hidden from other
                workers, in ms.
        """
        self.outbox = outbox
        self._senders = dict(senders)
        self._clock = clock
        self.max_attempts = max_attempts
        self._base_backoff_ms = base_backoff_ms
        self._batch_size = batch_size
        self._lease_ms = lease_ms
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()

    async def run_once(self) -> DeliveryStats:
        """Delivers every due record once.

        A ``DeliveryError`` is retried after the channel's ``Retry-After``
        or the exponential backoff, and dead-lettered when it is not
        retryable or the attempts are used up. Any other exception from a
        sender ends the pass; the records it had claimed become due again
        when their lease expires.

        Returns:
            Counts of this pass.
        """
        now = self._clock.now_ms()
        delivered = retried = dead = 0
        for record in self.outbox.due(now, self._batch_size, self._lease_ms):
            attempts = record.attempts + 1
            sender = self._senders.get(record.channel)
            if sender is None:
                self.outbox.mark_failed(
                    record.delivery_id,
                    attempts=attempts,
                    error=f"no sender configured for {record.channel.value}",
                    next_attempt_ms=now,
                    dead=True,
                )
                dead += 1
                continue
            try:
                await sender.send(record)
            except model.DeliveryError as err:
                is_dead = not err.retryable or attempts >= self.max_attempts
                delay = err.retry_after_ms or model.backoff_ms(
                    attempts, self._base_backoff_ms
                )
                self.outbox.mark_failed(
                    record.delivery_id,
                    attempts=attempts,
                    error=str(err),
                    next_attempt_ms=now + delay,
                    dead=is_dead,
                )
                dead += int(is_dead)
                retried += int(not is_dead)
                _LOG.warning(
                    "delivery %s on %s failed (attempt %d): %s",
                    record.delivery_id,
                    record.channel.value,
                    attempts,
                    err,
                )
                continue
            self.outbox.mark_delivered(record.delivery_id, now, attempts)
            delivered += 1
        return DeliveryStats(delivered=delivered, retried=retried, dead=dead)

    async def _loop(self, interval_s: float) -> None:
        """Runs passes until ``stop`` sets the stopping event.

        Args:
            interval_s: Pause between passes in seconds; a stop request
                ends the pause early.
        """
        while not self._stopping.is_set():
            try:
                await self.run_once()
            except Exception:  # noqa: BLE001 - isolation point: a failed pass is logged and the loop keeps delivering
                _LOG.exception("outbox pass failed")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stopping.wait(), interval_s)

    def start(self, interval_s: float = 1.0) -> asyncio.Task[None]:
        """Starts the background delivery loop unless it is running.

        Args:
            interval_s: Pause between passes in seconds; ignored when the
                loop is already running.

        Returns:
            The running task.
        """
        if self._task is None or self._task.done():
            self._stopping.clear()
            self._task = asyncio.create_task(self._loop(interval_s))
        return self._task

    async def stop(self) -> None:
        """Stops the loop and waits for the current pass to finish.

        Calling it when the loop is not running is a no-op.
        """
        self._stopping.set()
        if self._task is not None:
            await self._task
            self._task = None
