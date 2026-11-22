"""In-process asynchronous event bus for versioned domain events.

Handlers subscribe by event type (or ``"*"``). Publishing awaits every
handler; a failing handler is logged and counted but never prevents the
others from running. Handlers must be idempotent because the durable outbox
delivers at least once.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable
from collections.abc import Callable

from joseph.domain import events

Handler = Callable[[events.DomainEvent], Awaitable[None]]

_LOG = logging.getLogger(__name__)


class EventBus:
    """Async publish/subscribe bus keyed by ``DomainEvent.event_type``."""

    def __init__(self) -> None:
        """Initializes an empty bus."""
        self._handlers: dict[str, list[Handler]] = {}
        self.failures = 0

    def subscribe(
        self, event_type: str, handler: Handler
    ) -> Callable[[], None]:
        """Registers a handler.

        Args:
            event_type: Event type to receive, or ``"*"`` for all.
            handler: Coroutine function receiving the event.

        Returns:
            Function that unsubscribes the handler.
        """
        self._handlers.setdefault(event_type, []).append(handler)

        def unsubscribe() -> None:
            """Removes the handler if still registered."""
            handlers = self._handlers.get(event_type, [])
            if handler in handlers:
                handlers.remove(handler)

        return unsubscribe

    async def publish(self, event: events.DomainEvent) -> None:
        """Delivers an event to matching handlers concurrently.

        Args:
            event: Event to publish.
        """
        handlers = [
            *self._handlers.get(event.event_type, []),
            *self._handlers.get("*", []),
        ]
        if not handlers:
            return
        results = await asyncio.gather(
            *(handler(event) for handler in handlers), return_exceptions=True
        )
        for result in results:
            if isinstance(result, BaseException):
                self.failures += 1
                _LOG.error(
                    "event handler failed for %s: %s",
                    event.event_type,
                    type(result).__name__,
                )
