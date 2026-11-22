"""Health, metrics and the authenticated user stream."""

from __future__ import annotations

import asyncio
import contextlib

import fastapi
from fastapi import responses
from starlette import websockets

from joseph_execution.api import auth
from joseph_execution.api import errors as api_errors
from joseph_execution.platform import streams

_STREAM_CHANNELS = {"orders", "fills", "positions", "account", "risk"}


def build_router() -> fastapi.APIRouter:  # noqa: PLR0915
    """Builds the system router.

    Returns:
        Router.
    """
    router = fastapi.APIRouter(tags=["system"])

    @router.get("/health")
    async def health(request: fastapi.Request) -> dict[str, object]:
        """Reports liveness and safety gates (no tenant data).

        Args:
            request: Request.

        Returns:
            Health document.
        """
        svc = auth.api(request)
        return {
            "status": "ok",
            "service": "joseph-execution-engine",
            "live_trading_enabled": svc.settings.live_trading_enabled,
            "emergency_halt": svc.settings.emergency_halt,
            "market_gateway": svc.settings.market_gateway,
        }

    @router.get("/metrics", dependencies=[fastapi.Depends(auth.service)])
    async def metrics(request: fastapi.Request) -> responses.PlainTextResponse:
        """Exposes Prometheus metrics (service token required).

        Args:
            request: Request.

        Returns:
            Prometheus text.
        """
        return responses.PlainTextResponse(
            auth.api(request).metrics.render(),
            media_type="text/plain; version=0.0.4",
        )

    @router.websocket("/v1/stream/user")
    async def user_stream(websocket: fastapi.WebSocket) -> None:  # noqa: PLR0915
        """Streams tenant-scoped order/fill/position/risk events.

        Args:
            websocket: Connection (token in ``?token=`` or header).
        """
        svc: object = websocket.app.state.services
        token = websocket.query_params.get("token") or auth.bearer(
            websocket.headers.get("authorization")
        )
        principal = auth.resolve_token(svc, token)  # type: ignore[arg-type]
        await websocket.accept()
        hub: streams.UserStreamHub = svc.hub  # type: ignore[attr-defined]
        if principal is None:
            await websocket.send_json(
                hub.envelope(
                    "anonymous",
                    "error",
                    None,
                    api_errors.body("UNAUTHENTICATED", "valid token required"),
                    advance=False,
                )
            )
            await websocket.close(code=1008)
            return
        sub = hub.subscribe(principal.tenant_id)
        heartbeat = svc.settings.stream_heartbeat_s  # type: ignore[attr-defined]

        async def reader() -> None:
            """Handles subscribe/ping messages."""
            while True:
                message = await websocket.receive_json()
                if not isinstance(message, dict):
                    continue
                action = message.get("action")
                if action == "ping":
                    await websocket.send_json(
                        hub.envelope(
                            principal.tenant_id,
                            "heartbeat",
                            None,
                            {},
                            advance=False,
                        )
                    )
                    continue
                if action != "subscribe":
                    continue
                channels = {
                    str(c.get("channel"))
                    for c in message.get("channels", [])
                    if isinstance(c, dict)
                } & _STREAM_CHANNELS
                accounts = [
                    str(c["account_id"])
                    for c in message.get("channels", [])
                    if isinstance(c, dict) and c.get("account_id")
                ]
                sub.channels = channels
                sub.account_id = accounts[0] if accounts else None
                await websocket.send_json(
                    hub.envelope(
                        principal.tenant_id,
                        "subscribed",
                        None,
                        {
                            "channels": sorted(channels),
                            "sequence": hub.current_sequence(
                                principal.tenant_id
                            ),
                        },
                        advance=False,
                    )
                )
                last = message.get("last_sequence")
                if isinstance(last, int):
                    missed = hub.replay(sub, last)
                    if missed is None:
                        await websocket.send_json(
                            hub.envelope(
                                principal.tenant_id,
                                "snapshot_required",
                                None,
                                {
                                    "reason": "replay window exceeded",
                                    "channels": sorted(channels),
                                },
                                advance=False,
                            )
                        )
                    else:
                        for envelope in missed:
                            await websocket.send_json(envelope)

        async def writer() -> None:
            """Forwards hub events and heartbeats."""
            while True:
                try:
                    envelope = await asyncio.wait_for(
                        sub.queue.get(), heartbeat
                    )
                except TimeoutError:
                    envelope = hub.envelope(
                        principal.tenant_id,
                        "heartbeat",
                        None,
                        {},
                        advance=False,
                    )
                await websocket.send_json(envelope)

        tasks = [asyncio.create_task(reader()), asyncio.create_task(writer())]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
                with contextlib.suppress(
                    asyncio.CancelledError,
                    websockets.WebSocketDisconnect,
                    RuntimeError,
                ):
                    await task
            hub.unsubscribe(sub)

    return router
