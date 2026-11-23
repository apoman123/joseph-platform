"""Fakes for live venue adapter tests: WebSockets, clocks and sleep.

``FakeConnector`` plays scripted connections: each connection is a list of
frames (strings) and an optional terminal exception; ``sent`` records
every frame the client sent per connection, so tests can assert logins,
subscriptions, pings and resubscription after a reconnect.
"""

from __future__ import annotations

import contextlib
import dataclasses
from collections.abc import AsyncIterator
from collections.abc import Sequence

from joseph_execution.adapters.venues import ws


class ManualMonotonic:
    """A monotonic clock that only moves when told (or when slept)."""

    def __init__(self, start: float = 1_000.0) -> None:
        """Initializes the clock.

        Args:
            start: Initial reading.
        """
        self.now = start
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        """Returns the reading."""
        return self.now

    async def sleep(self, seconds: float) -> None:
        """Advances the clock instead of sleeping.

        Args:
            seconds: Duration.
        """
        self.sleeps.append(seconds)
        self.now += max(seconds, 0.0)


@dataclasses.dataclass(slots=True)
class Script:
    """One scripted connection.

    Attributes:
        frames: Frames delivered in order.
        error: Raised after the frames (``None`` = idle until timeout).
        refuse: Raised on connect instead of opening.
    """

    frames: list[str | bytes]
    error: BaseException | None = None
    refuse: BaseException | None = None


class FakeConnection:
    """Connection replaying a script.

    Attributes:
        sent: Frames sent by the client.
    """

    def __init__(self, script: Script, clock: ManualMonotonic) -> None:
        """Initializes the connection.

        Args:
            script: Script.
            clock: Clock advanced while idle.
        """
        self._frames = list(script.frames)
        self._error = script.error
        self._clock = clock
        self.sent: list[str] = []

    async def send(self, message: str) -> None:
        """Records a frame.

        Args:
            message: Frame.
        """
        self.sent.append(message)

    async def recv(self) -> str | bytes:
        """Returns the next scripted frame.

        Returns:
            Frame.

        Raises:
            BaseException: The scripted error once frames run out.
            TimeoutError: When idle (advances the clock by 30 s).
        """
        if self._frames:
            return self._frames.pop(0)
        if self._error is not None:
            raise self._error
        self._clock.now += 30.0
        raise TimeoutError


class FakeConnector:
    """``ws.ConnectFn`` serving scripted connections in order.

    Attributes:
        urls: URLs opened.
        connections: Connections opened.
    """

    def __init__(
        self, scripts: Sequence[Script], clock: ManualMonotonic
    ) -> None:
        """Initializes the connector.

        Args:
            scripts: One script per connection attempt.
            clock: Clock.
        """
        self._scripts = list(scripts)
        self._clock = clock
        self.urls: list[str] = []
        self.connections: list[FakeConnection] = []

    @contextlib.asynccontextmanager
    async def _open(self, url: str) -> AsyncIterator[ws.WsConnection]:
        """Opens the next scripted connection.

        Args:
            url: URL.

        Yields:
            Connection.

        Raises:
            OSError: When no script is left.
        """
        self.urls.append(url)
        if not self._scripts:
            raise OSError("no more scripted connections")
        script = self._scripts.pop(0)
        if script.refuse is not None:
            raise script.refuse
        connection = FakeConnection(script, self._clock)
        self.connections.append(connection)
        yield connection

    def __call__(
        self, url: str
    ) -> contextlib.AbstractAsyncContextManager[ws.WsConnection]:
        """Implements ``ws.ConnectFn``.

        Args:
            url: URL.

        Returns:
            Context manager.
        """
        return self._open(url)


async def collect(stream: AsyncIterator[object], count: int) -> list[object]:
    """Takes ``count`` items from an async iterator, then closes it.

    Args:
        stream: Iterator.
        count: Items wanted.

    Returns:
        Items.
    """
    items: list[object] = []
    async for item in stream:
        items.append(item)
        if len(items) >= count:
            break
    closer = getattr(stream, "aclose", None)
    if closer is not None:
        await closer()
    return items
