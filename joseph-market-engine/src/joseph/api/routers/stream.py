# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Market WebSocket ``/v1/stream/market`` (contract ``stream.schema.json``).

Protocol:

* client → ``ClientMessage`` ``{"action": "subscribe" | "unsubscribe" |
  "ping", "channels": [ChannelSpec], "last_sequence": int | null}``;
* server → ``StreamEnvelope``: ``subscribed``, ``candle.updated``,
  ``aoi.updated``, ``feed.health``, ``qualification.changed``,
  ``signal.created``/``signal.updated`` (tenant-scoped, requires
  ``?token=``), ``heartbeat``, ``error`` and ``snapshot_required`` (when
  ``last_sequence`` is older than the retention ring or the client fell
  behind; refetch REST snapshots, then resubscribe).

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

import fastapi
from starlette import websockets as starlette_ws

from joseph.domain import errors
from joseph.domain import identity
from joseph.domain import quality
from joseph.market_data.app import feed_router
from joseph.market_data.app import service as market_service
from joseph.platform import auth
from joseph.platform import streaming

HEARTBEAT_S = 15.0
_SeriesLease = tuple[str, str, quality.FeedMode]


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
    """State of one WebSocket client."""

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

        Args:
            payload: Envelope dict.
        """
        async with self._send_lock:
            await self.websocket.send_text(json.dumps(payload))

    async def pump(self) -> None:
        """Forwards queued envelopes, signalling overflow."""
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
        """Sends periodic heartbeats."""
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
            errors.JosephError: If the channel cannot be served.
        """
        channel = str(spec.get("channel", ""))
        if channel == "signals" and self.principal is None:
            raise errors.UnauthenticatedError(
                "Connect with ?token=<session token> to receive signals."
            )
        if channel not in ("candles", "aois"):
            try:
                return streaming.channel_key(spec)
            except ValueError as err:
                raise errors.ValidationFailedError(str(err)) from err
        instrument = market_service.as_instrument_id(
            str(spec.get("instrument_id", ""))
        )
        timeframe = market_service.as_timeframe(str(spec.get("timeframe", "")))
        if channel == "aois":
            return streaming.channel_key(
                {
                    "channel": "aois",
                    "instrument_id": str(instrument),
                    "timeframe": timeframe.value,
                }
            )
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
            replay = self.hub.replay(self.subscription, last)
            if replay is None:
                await self.send(
                    self.hub.control(
                        "snapshot_required",
                        {
                            "reason": "resume point is older than retention",
                            "channels": sorted(self.subscription.channels),
                        },
                    ).to_contract()
                )
            else:
                for envelope in replay:
                    await self.send(envelope.to_contract())

    async def unsubscribe(self, message: Mapping[str, Any]) -> None:
        """Handles an unsubscribe request.

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
        Router.
    """
    router = fastapi.APIRouter(tags=["stream"])

    @router.websocket("/v1/stream/market")
    async def market_stream(websocket: fastapi.WebSocket) -> None:
        """Serves the resumable market stream.

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
            pass
        finally:
            pump.cancel()
            beat.cancel()
            await connection.close()

    return router
