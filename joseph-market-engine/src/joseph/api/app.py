# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""FastAPI application factory for the market engine."""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from collections.abc import Awaitable
from collections.abc import Callable
from collections.abc import Sequence
from typing import Protocol

import fastapi
from fastapi.middleware import cors

from joseph.api import errors as api_errors
from joseph.api.routers import health
from joseph.api.routers import market
from joseph.api.routers import stream
from joseph.domain import ids
from joseph.market_data.app import service as market_service
from joseph.platform import auth
from joseph.platform import config
from joseph.platform import metrics as metrics_lib
from joseph.platform import streaming


class AppContainer(Protocol):
    """Services the HTTP layer needs (satisfied by ``bootstrap.Container``).

    Attributes:
        settings: Engine settings (CORS origins, demo mode).
        market_data: Market-data service behind the market routes.
        stream_hub: Hub behind the market WebSocket.
        authenticator: Bearer-token authenticator for protected routes.
        metrics: Registry rendered by ``/metrics``.
    """

    settings: config.Settings
    market_data: market_service.MarketDataService
    stream_hub: streaming.StreamHub
    authenticator: auth.TokenAuthenticator
    metrics: metrics_lib.MetricsRegistry

    async def astart(self) -> None:
        """Starts background workers; called once at application startup."""
        ...

    async def aclose(self) -> None:
        """Stops workers and releases resources at application shutdown."""
        ...


def create_app(
    container: AppContainer, extra_routers: Sequence[fastapi.APIRouter] = ()
) -> fastapi.FastAPI:
    """Creates the market-engine ASGI app.

    The app starts the container's workers when it starts serving and
    closes the container when it shuts down. Every response carries an
    ``X-Trace-Id`` header, and errors are rendered as the contract
    ``ErrorResponse`` (see ``api.errors``).

    Args:
        container: Wired services.
        extra_routers: Additional routers (indicators, strategies, ...)
            mounted after the core routers.

    Returns:
        FastAPI application.
    """

    @contextlib.asynccontextmanager
    async def lifespan(_: fastapi.FastAPI) -> AsyncIterator[None]:
        """Starts workers on startup and closes the container on shutdown.

        ``aclose`` runs on an orderly shutdown; it is not reached when
        ``astart`` raises.

        Yields:
            Control while the app serves requests.
        """
        await container.astart()
        try:
            async with contextlib.AsyncExitStack() as stack:
                mcp_lifespan = getattr(app.state, "mcp_lifespan", None)
                if mcp_lifespan is not None:
                    await stack.enter_async_context(mcp_lifespan())
                yield
        finally:
            await container.aclose()

    app = fastapi.FastAPI(
        title="Joseph Market Engine",
        version=health.VERSION,
        description=(
            "Python-authoritative market data, script filters, indicators, "
            "strategies and signals. Contracts: joseph-terminal/packages/"
            "contracts/schemas."
        ),
        lifespan=lifespan,
    )
    app.state.authenticator = container.authenticator
    app.state.container = container
    app.add_middleware(
        cors.CORSMiddleware,
        allow_origins=list(container.settings.cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "Idempotency-Key",
            "Mcp-Method",
            "Mcp-Name",
            "Mcp-Protocol-Version",
            "Mcp-Session-Id",
            "Last-Event-ID",
        ],
        expose_headers=["X-Trace-Id", "Mcp-Session-Id"],
    )
    requests_total = container.metrics.counter(
        "joseph_http_requests_total", "HTTP requests served."
    )

    @app.middleware("http")
    async def trace(
        request: fastapi.Request,
        call_next: Callable[[fastapi.Request], Awaitable[fastapi.Response]],
    ) -> fastapi.Response:
        """Assigns a trace id and counts requests.

        Requests are counted by route template (``unmatched`` for unknown
        paths), not by raw path, which keeps the label set bounded.

        Args:
            request: Incoming request.
            call_next: Next handler in the middleware chain.

        Returns:
            Response with ``X-Trace-Id``.
        """
        request.state.trace_id = ids.uuid7()
        response = await call_next(request)
        response.headers["X-Trace-Id"] = request.state.trace_id
        route = request.scope.get("route")
        requests_total.inc(
            path=getattr(route, "path", "unmatched"),
            status=str(response.status_code),
        )
        return response

    api_errors.install(app)
    app.include_router(
        health.build_router(
            container.market_data, container.metrics, container.settings
        )
    )
    app.include_router(market.build_router(container.market_data))
    app.include_router(
        stream.build_router(
            container.stream_hub, container.market_data, container.authenticator
        )
    )
    for extra in extra_routers:
        app.include_router(extra)
    return app
