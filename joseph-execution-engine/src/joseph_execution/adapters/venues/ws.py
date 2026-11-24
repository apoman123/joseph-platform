# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Reconnecting private (account) WebSocket streams for live venues.

One loop serves every venue:

* each connection obtains its URL (Binance needs a fresh ``listenKey``),
  then ``on_open`` logs in and (re)subscribes, waiting for the venue's
  acknowledgement;
* right after subscribing, ``resync`` pulls a REST snapshot (open orders,
  recent fills, positions, balances) and replays it as ``resync`` updates,
  so anything missed while disconnected reaches the OMS; the OMS applies
  order states and fills idempotently (duplicates are ignored by fill id);
* an application-level ping keeps venues that require one alive, a
  ``keepalive`` hook extends listen keys, and silence beyond
  ``idle_timeout_s`` forces a reconnect;
* reconnects back off exponentially with full jitter and give up after
  ``max_failures`` consecutive failures (the caller restarts later); the
  count resets only when a connection delivers a frame, so a venue that
  accepts connections and then stays silent also gives up;
* frames that fail to parse are counted and skipped, never fatal.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import inspect
import logging
import random
import time
from collections.abc import AsyncGenerator
from collections.abc import Awaitable
from collections.abc import Callable
from collections.abc import Sequence
from typing import Protocol
from typing import TypeAlias

import websockets

from joseph_execution.domain import errors
from joseph_execution.ports import trading

_LOG = logging.getLogger(__name__)


class WsConnection(Protocol):
    """Minimal WebSocket connection surface used by the stream."""

    async def send(self, message: str) -> None:
        """Sends a text frame.

        Args:
            message: Frame payload.
        """
        ...

    async def recv(self) -> str | bytes:
        """Receives one frame.

        Returns:
            Frame payload.
        """
        ...


# Opens a connection to a URL; adapters take one so tests can inject fakes.
ConnectFn: TypeAlias = Callable[
    [str], contextlib.AbstractAsyncContextManager[WsConnection]
]


def default_connect(
    url: str,
) -> contextlib.AbstractAsyncContextManager[WsConnection]:
    """Opens a WebSocket with protocol-level pings enabled.

    Opening times out after 10 s, pings go every 20 s with a 20 s timeout,
    and frames may be up to 4 MiB (account snapshots can be large).

    Args:
        url: WebSocket URL.

    Returns:
        Async context manager yielding the connection.
    """
    connection: contextlib.AbstractAsyncContextManager[WsConnection] = (
        websockets.connect(
            url,
            open_timeout=10,
            ping_interval=20,
            ping_timeout=20,
            max_size=2**22,
        )
    )
    return connection


@dataclasses.dataclass(frozen=True, slots=True)
class StreamUpdate:
    """One parsed private-stream item.

    Attributes:
        order: Order state, if any.
        fill: Fill, if any.
        position: Position, if any.
        balance: Balance snapshot, if any.
        reconnect: The venue asked the client to reconnect (e.g. an
            expired listen key); no data.
    """

    order: trading.VenueOrderState | None = None
    fill: trading.VenueFill | None = None
    position: trading.VenuePosition | None = None
    balance: trading.VenueAccountSnapshot | None = None
    reconnect: bool = False


@dataclasses.dataclass(frozen=True, slots=True)
class PrivateStreamSpec:
    """Venue-specific private stream description.

    Attributes:
        name: Label for logs and errors (no secrets).
        url: Returns the URL for the next connection.
        on_open: Logs in and subscribes on a fresh connection.
        parse: Converts one frame to updates (empty for acks and pongs);
            may be a coroutine function when parsing needs lookups.
        resync: Fetches the REST snapshot replayed after each subscribe.
        ping_message: Application-level ping frame, if the venue needs one.
        ping_interval_s: Interval between pings, in seconds.
        idle_timeout_s: Reconnect when nothing arrives for this many
            seconds.
        keepalive: Periodic hook (e.g. listen-key keepalive).
        keepalive_interval_s: Interval between keepalives, in seconds.
        max_session_s: Reconnect proactively after this many seconds
            (venues that cut connections at a fixed age).
    """

    name: str
    url: Callable[[], Awaitable[str]]
    on_open: Callable[[WsConnection], Awaitable[None]]
    parse: Callable[
        [str | bytes],
        Sequence[StreamUpdate] | Awaitable[Sequence[StreamUpdate]],
    ]
    resync: Callable[[], Awaitable[Sequence[StreamUpdate]]]
    ping_message: str | None = None
    ping_interval_s: float = 20.0
    idle_timeout_s: float = 90.0
    keepalive: Callable[[], Awaitable[None]] | None = None
    keepalive_interval_s: float = 1800.0
    max_session_s: float | None = None


@dataclasses.dataclass(slots=True)
class StreamStats:
    """Counters of one stream (health and tests).

    Attributes:
        connects: Successful subscriptions.
        failures: Connection failures.
        frames: Frames received.
        parse_errors: Frames that failed to parse.
        resyncs: Snapshot replays.
        pings: Application pings sent.
        keepalives: Keepalive hooks run.
    """

    connects: int = 0
    failures: int = 0
    frames: int = 0
    parse_errors: int = 0
    resyncs: int = 0
    pings: int = 0
    keepalives: int = 0


class _IdleError(Exception):
    """No frame arrived within the idle timeout."""


async def run_private_stream(  # noqa: PLR0912, PLR0915 - recovery loop.
    spec: PrivateStreamSpec,
    *,
    connect: ConnectFn = default_connect,
    max_failures: int = 8,
    base_delay_s: float = 0.5,
    max_delay_s: float = 30.0,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    jitter: Callable[[], float] = random.random,
    stats: StreamStats | None = None,
) -> AsyncGenerator[tuple[StreamUpdate, bool], None]:
    """Yields private updates, reconnecting, resubscribing and resyncing.

    Args:
        spec: Venue stream description.
        connect: WebSocket connector (injectable for tests).
        max_failures: Consecutive failures before giving up.
        base_delay_s: First backoff delay in seconds; it doubles per
            failure.
        max_delay_s: Backoff cap in seconds.
        monotonic: Monotonic clock (injectable).
        sleep: Sleep function (injectable).
        jitter: Uniform ``[0, 1)`` source for full jitter (injectable).
        stats: Optional counters updated in place.

    Yields:
        ``(update, from_resync)`` pairs.

    Raises:
        VenueRejectedError: Login was refused (bad key or permission); never
            retried.
        AdapterUnavailableError: After ``max_failures`` consecutive
            failures.
    """
    counters = stats if stats is not None else StreamStats()
    failures = 0
    while True:
        try:
            url = await spec.url()
            async with connect(url) as connection:
                await spec.on_open(connection)
                counters.connects += 1
                counters.resyncs += 1
                for update in await spec.resync():
                    yield update, True
                opened = last_rx = last_ping = last_keep = monotonic()
                while True:
                    now = monotonic()
                    if (
                        spec.max_session_s is not None
                        and now - opened >= spec.max_session_s
                    ):
                        break
                    deadlines = [last_rx + spec.idle_timeout_s]
                    if spec.ping_message is not None:
                        deadlines.append(last_ping + spec.ping_interval_s)
                    if spec.keepalive is not None:
                        deadlines.append(last_keep + spec.keepalive_interval_s)
                    timeout = max(min(deadlines) - now, 0.01)
                    try:
                        frame = await asyncio.wait_for(
                            connection.recv(), timeout
                        )
                    except TimeoutError:
                        frame = None
                    now = monotonic()
                    if frame is not None:
                        last_rx = now
                        counters.frames += 1
                        failures = 0
                        try:
                            parsed = spec.parse(frame)
                            updates = (
                                await parsed
                                if inspect.isawaitable(parsed)
                                else parsed
                            )
                        except errors.VenueRejectedError:
                            raise
                        except Exception:  # noqa: BLE001 - isolation point: a malformed frame is counted and skipped, the stream keeps running
                            counters.parse_errors += 1
                            _LOG.warning("%s: unparsable frame", spec.name)
                            updates = ()
                        restart = False
                        for update in updates:
                            if update.reconnect:
                                restart = True
                                continue
                            yield update, False
                        if restart:
                            break
                    elif now - last_rx >= spec.idle_timeout_s:
                        raise _IdleError(spec.name)
                    if (
                        spec.ping_message is not None
                        and now - last_ping >= spec.ping_interval_s
                    ):
                        await connection.send(spec.ping_message)
                        counters.pings += 1
                        last_ping = now
                    if (
                        spec.keepalive is not None
                        and now - last_keep >= spec.keepalive_interval_s
                    ):
                        await spec.keepalive()
                        counters.keepalives += 1
                        last_keep = now
        except errors.VenueRejectedError:
            raise
        except Exception as err:  # noqa: BLE001 - isolation point: any connection failure backs off and reconnects until max_failures
            failures += 1
            counters.failures += 1
            _LOG.warning(
                "%s disconnected (%s); attempt %d/%d",
                spec.name,
                type(err).__name__,
                failures,
                max_failures,
            )
            if failures >= max_failures:
                raise errors.AdapterUnavailableError(
                    f"{spec.name} failed {failures} times in a row"
                ) from None
            delay = min(max_delay_s, base_delay_s * 2 ** (failures - 1))
            # Full jitter: a uniform wait in [0, delay) spreads reconnects of
            # many streams after a venue-wide outage.
            await sleep(delay * jitter())
