# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Signal routing into the outbox and the outbox delivery worker."""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import logging
from collections.abc import Callable
from collections.abc import Mapping
from collections.abc import Sequence

from joseph.alerts.app import ports
from joseph.alerts.domain import model
from joseph.domain import clock as clock_port
from joseph.signals.domain import signal as signal_domain

_LOG = logging.getLogger(__name__)

ChannelPolicy = Callable[[str, str], frozenset[str]]
"""``(tenant_id, strategy_version_id) -> allowed channel names``."""


def default_channels(
    tenant_id: str, strategy_version_id: str
) -> frozenset[str]:
    """Default channel policy: UI only.

    Args:
        tenant_id: Tenant (unused).
        strategy_version_id: Version (unused).

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
            strategy_channels: Channels each strategy version allows.
            ui_enabled: Whether to enqueue the implicit UI delivery.
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

        Args:
            signal: Signal.

        Returns:
            Routing result.
        """
        now = self._clock.now_ms()
        result = model.route(
            signal,
            self.subscriptions.list_for_tenant(signal.tenant_id),
            self._channels(signal.tenant_id, signal.strategy_version_id),
            now,
            ui_target=f"tenant:{signal.tenant_id}"
            if self._ui_enabled
            else None,
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
    """Publishes each signal to several publishers in order.

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
                await publisher.publish(signal)  # type: ignore[attr-defined]
            except Exception:
                _LOG.exception("publisher %s failed", type(publisher).__name__)


@dataclasses.dataclass(frozen=True, slots=True)
class DeliveryStats:
    """Counts from one worker pass.

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

    Attributes:
        outbox: Delivery outbox.
        max_attempts: Attempts before dead-lettering.
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
            senders: Sender per channel.
            clock: Time source.
            max_attempts: Attempts before dead-lettering.
            base_backoff_ms: First retry delay.
            batch_size: Records per pass.
            lease_ms: Claim lease per record.
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
        """Runs passes until stopped.

        Args:
            interval_s: Pause between passes.
        """
        while not self._stopping.is_set():
            try:
                await self.run_once()
            except Exception:
                _LOG.exception("outbox pass failed")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stopping.wait(), interval_s)

    def start(self, interval_s: float = 1.0) -> asyncio.Task[None]:
        """Starts the background delivery loop.

        Args:
            interval_s: Pause between passes.

        Returns:
            The running task.
        """
        if self._task is None or self._task.done():
            self._stopping.clear()
            self._task = asyncio.create_task(self._loop(interval_s))
        return self._task

    async def stop(self) -> None:
        """Stops the loop and waits for the current pass to finish."""
        self._stopping.set()
        if self._task is not None:
            await self._task
            self._task = None
