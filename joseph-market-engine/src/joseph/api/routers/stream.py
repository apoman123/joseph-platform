# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Market WebSocket ``/v1/stream/market`` (contract ``stream.schema.json``).

Protocol:

* client → ``ClientMessage`` ``{"action": "subscribe" | "unsubscribe" |
  "ping", "channels": [ChannelSpec], "last_sequence": int | null}``;
* server → ``StreamEnvelope``: ``subscribed``, ``candle.updated``,
  ``aoi.updated``, ``feed.health``,
  ``signal.created``/``signal.updated`` (tenant-scoped, requires
  ``?token=``), ``heartbeat``, ``error`` and ``snapshot_required`` (when
  ``last_sequence`` is older than the retention ring, when it is newer than
  the newest buffered sequence, or when the client fell behind; refetch REST
  snapshots, then resubscribe).

Sequences start again from 0 when the engine restarts, so a
``last_sequence`` newer than the newest buffered sequence means the server
restarted and requires a snapshot.

Control envelopes carry ``sequence=0``; only sequenced envelopes advance a
client's ``last_sequence``.

Subscribing to ``candles`` starts (or joins) a feed session with the
requested ``feed_mode``; unsupported requests are answered with an
``error`` envelope rather than a silent fallback.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Mapping
from typing import Any
from typing import TypeAlias

import fastapi
from starlette import websockets as starlette_ws

from joseph.domain import errors
from joseph.domain import identity
from joseph.domain import quality
from joseph.market_data.app import feed_router
from joseph.market_data.app import service as market_service
from joseph.platform import auth
from joseph.platform import streaming

# Seconds between server heartbeats on an otherwise idle connection.
HEARTBEAT_S = 15.0

# Feed session a candle channel holds: ``(instrument_id, timeframe,
# feed_mode)``, released when the channel is unsubscribed or the socket
# closes.
_SeriesLease: TypeAlias = tuple[str, str, quality.FeedMode]


def _error(
    hub: streaming.StreamHub, code: str, message: str
) -> dict[str, object]:
    """Builds an ``error`` control envelope.

    Args:
        hub: Stream hub.
        code: Contract error code.
        message: Plain-language message.

    Returns:
        Envelope dict.
    """
    return hub.control(
        "error", {"code": code, "message": message}
    ).to_contract()


class _Connection:
    """State of one WebSocket client.

    Attributes:
        websocket: Accepted socket.
        hub: Stream hub the client subscribes through.
        router: Feed router that starts candle feed sessions.
        market_data: Market-data service (venue health for ``feed_health``).
        principal: Authenticated principal, or ``None`` for an anonymous
            client (public channels only).
        subscription: The client's hub subscription.
        leases: Feed sessions held, by candle channel key.
    """

    def __init__(
        self,
        websocket: fastapi.WebSocket,
        hub: streaming.StreamHub,
        router: feed_router.FeedRouter,
        market_data: market_service.MarketDataService,
        principal: identity.Principal | None,
    ) -> None:
        """Initializes the connection.

        Args:
            websocket: Accepted socket.
            hub: Stream hub.
            router: Feed router.
            market_data: Market-data service.
            principal: Authenticated principal or ``None``.
        """
        self.websocket = websocket
        self.hub = hub
        self.router = router
        self.market_data = market_data
        self.principal = principal
        self.subscription = hub.open(
            None if principal is None else principal.tenant_id
        )
        self.leases: dict[str, _SeriesLease] = {}
        self._send_lock = asyncio.Lock()

    async def send(self, payload: Mapping[str, object]) -> None:
        """Sends one JSON frame.

        The pump, the heartbeat and the receive loop all send on the same
        socket; the lock keeps their frames from interleaving.

        Args:
            payload: Envelope dict.
        """
        async with self._send_lock:
            await self.websocket.send_text(json.dumps(payload))

    async def pump(self) -> None:
        """Forwards queued envelopes until cancelled, signalling overflow.

        When the subscription overflowed, the queued envelopes are dropped
        and one ``snapshot_required`` is sent instead, so the client
        refetches rather than seeing a silent gap.
        """
        queue = self.subscription.queue
        while True:
            if self.subscription.overflowed:
                while not queue.empty():
                    queue.get_nowait()
                self.subscription.overflowed = False
                await self.send(
                    self.hub.control(
                        "snapshot_required",
                        {
                            "reason": "client fell behind; refetch snapshots",
                            "channels": sorted(self.subscription.channels),
                        },
                    ).to_contract()
                )
            try:
                envelope = await asyncio.wait_for(queue.get(), 0.5)
            except TimeoutError:
                continue
            await self.send(envelope.to_contract())

    async def heartbeat(self) -> None:
        """Sends a heartbeat every ``HEARTBEAT_S`` seconds until cancelled."""
        while True:
            await asyncio.sleep(HEARTBEAT_S)
            await self.send(
                self.hub.control(
                    "heartbeat", {"last_sequence": self.hub.last_sequence}
                ).to_contract()
            )

    async def _resolve(self, spec: Mapping[str, Any]) -> str:
        """Validates a channel spec and starts any needed feed.

        Args:
            spec: Contract ``ChannelSpec``.

        Returns:
            Hub channel key.

        Raises:
            errors.UnauthenticatedError: For ``signals`` on an anonymous
                connection.
            errors.ValidationFailedError: For an unknown channel, a missing
                or malformed instrument or timeframe, or an unknown
                ``feed_mode``.
            errors.JosephError: If the feed router cannot serve the
                requested candle feed (``UnsupportedError``,
                ``MissingDataError`` or ``NotFoundError``).
        """
        channel = str(spec.get("channel", ""))
        if channel == "signals" and self.principal is None:
            raise errors.UnauthenticatedError(
                "Connect with ?token=<session token> to receive signals."
            )
        if channel != "candles":
            try:
                return streaming.channel_key(spec)
            except ValueError as err:
                raise errors.ValidationFailedError(str(err)) from err
        instrument = market_service.as_instrument_id(
            str(spec.get("instrument_id", ""))
        )
        timeframe = market_service.as_timeframe(str(spec.get("timeframe", "")))
        try:
            mode = quality.FeedMode(str(spec.get("feed_mode") or "AUTO"))
        except ValueError as err:
            raise errors.ValidationFailedError("unknown feed_mode") from err
        key = streaming.candle_channel(
            str(instrument), timeframe.value, mode.value
        )
        if key not in self.leases:
            await self.router.acquire(instrument, timeframe, mode)
            self.leases[key] = (str(instrument), timeframe.value, mode)
        return key

    async def subscribe(self, message: Mapping[str, Any]) -> None:
        """Handles a subscribe request.

        Each spec that cannot be served is answered with its own ``error``
        envelope; the others are still subscribed. A ``subscribed``
        envelope then lists every channel, followed by the current venue
        health when ``feed_health`` was requested and by the replay from
        ``last_sequence`` when one was given. Instead of the replay, the
        client gets ``snapshot_required`` when ``last_sequence`` is older
        than the retention ring, or newer than the newest buffered sequence
        (the server restarted and requires a snapshot).

        Args:
            message: Client message.
        """
        accepted: list[str] = []
        for spec in message.get("channels") or []:
            if not isinstance(spec, Mapping):
                await self.send(
                    _error(
                        self.hub,
                        "VALIDATION_FAILED",
                        "channel specs must be objects",
                    )
                )
                continue
            try:
                key = await self._resolve(spec)
            except errors.JosephError as err:
                await self.send(_error(self.hub, err.code, err.message))
                continue
            accepted.append(key)
        await self.hub.add_channels(self.subscription, accepted)
        await self.send(
            self.hub.control(
                "subscribed", {"channels": sorted(self.subscription.channels)}
            ).to_contract()
        )
        if "feed_health" in accepted:
            for _, feed in self.market_data.venue_status():
                await self.send(
                    self.hub.control(
                        "feed.health", feed.to_contract()
                    ).to_contract()
                )
        last = message.get("last_sequence")
        if isinstance(last, int) and not isinstance(last, bool):
            reason = "resume point is older than retention"
            if last > self.hub.last_sequence:
                reason = (
                    "resume point is newer than the server's latest "
                    "sequence (the server restarted)"
                )
            replay = self.hub.replay(self.subscription, last)
            if replay is None:
                await self.send(
                    self.hub.control(
                        "snapshot_required",
                        {
                            "reason": reason,
                            "channels": sorted(self.subscription.channels),
                        },
                    ).to_contract()
                )
            else:
                for envelope in replay:
                    await self.send(envelope.to_contract())

    async def unsubscribe(self, message: Mapping[str, Any]) -> None:
        """Handles an unsubscribe request.

        Malformed specs are skipped without an error envelope.

        Args:
            message: Client message.
        """
        keys: list[str] = []
        for spec in message.get("channels") or []:
            if not isinstance(spec, Mapping):
                continue
            with contextlib.suppress(ValueError):
                if spec.get("channel") == "candles":
                    keys.append(
                        streaming.candle_channel(
                            str(spec.get("instrument_id")),
                            str(spec.get("timeframe")),
                            str(spec.get("feed_mode") or "AUTO"),
                        )
                    )
                else:
                    keys.append(streaming.channel_key(spec))
        await self.hub.remove_channels(self.subscription, keys)
        for key in keys:
            await self._release(key)
        await self.send(
            self.hub.control(
                "subscribed", {"channels": sorted(self.subscription.channels)}
            ).to_contract()
        )

    async def _release(self, key: str) -> None:
        """Releases the feed lease of a channel, if any.

        Args:
            key: Channel key.
        """
        lease = self.leases.pop(key, None)
        if lease is None:
            return
        instrument, timeframe, mode = lease
        await self.router.release(
            market_service.as_instrument_id(instrument),
            market_service.as_timeframe(timeframe),
            mode,
        )

    async def close(self) -> None:
        """Releases every channel and feed lease."""
        await self.hub.close(self.subscription)
        for key in list(self.leases):
            await self._release(key)


def build_router(
    hub: streaming.StreamHub,
    market_data: market_service.MarketDataService,
    authenticator: auth.TokenAuthenticator,
) -> fastapi.APIRouter:
    """Builds the market WebSocket router.

    Args:
        hub: Stream hub.
        market_data: Market-data service (owns the feed router).
        authenticator: Token authenticator for tenant-scoped channels.

    Returns:
        Router serving ``/v1/stream/market``.
    """
    router = fastapi.APIRouter(tags=["stream"])

    @router.websocket("/v1/stream/market")
    async def market_stream(websocket: fastapi.WebSocket) -> None:
        """Serves the resumable market stream.

        Without ``?token=`` the client is anonymous; an invalid token gets
        an ``error`` envelope and the socket is closed with code 1008
        (policy violation). The pump and heartbeat tasks run until the
        client disconnects; they are then cancelled and every channel and
        feed lease is released.

        Args:
            websocket: Incoming socket.
        """
        await websocket.accept()
        token = websocket.query_params.get("token")
        principal: identity.Principal | None = None
        if token:
            try:
                principal = authenticator.authenticate(token)
            except errors.UnauthenticatedError as err:
                await websocket.send_text(
                    json.dumps(_error(hub, err.code, err.message))
                )
                await websocket.close(code=1008)
                return
        connection = _Connection(
            websocket, hub, market_data.router, market_data, principal
        )
        pump = asyncio.create_task(connection.pump())
        beat = asyncio.create_task(connection.heartbeat())
        try:
            while True:
                raw = await websocket.receive_text()
                try:
                    message = json.loads(raw)
                except ValueError:
                    await connection.send(
                        _error(hub, "VALIDATION_FAILED", "frames must be JSON")
                    )
                    continue
                action = (
                    message.get("action") if isinstance(message, dict) else None
                )
                if action == "subscribe":
                    await connection.subscribe(message)
                elif action == "unsubscribe":
                    await connection.unsubscribe(message)
                elif action == "ping":
                    await connection.send(
                        hub.control(
                            "heartbeat", {"last_sequence": hub.last_sequence}
                        ).to_contract()
                    )
                else:
                    await connection.send(
                        _error(
                            hub,
                            "VALIDATION_FAILED",
                            "action must be subscribe, unsubscribe or ping",
                        )
                    )
        except (starlette_ws.WebSocketDisconnect, RuntimeError):
            # Using a socket that already closed raises a RuntimeError
            # (``WebSocketDisconnected``) rather than WebSocketDisconnect;
            # both end the session.
            pass
        finally:
            pump.cancel()
            beat.cancel()
            await connection.close()

    return router
