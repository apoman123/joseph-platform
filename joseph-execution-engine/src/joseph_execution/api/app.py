"""FastAPI application factory."""

from __future__ import annotations

import contextlib
import re
from collections.abc import AsyncIterator
from collections.abc import Awaitable
from collections.abc import Callable
from collections.abc import Coroutine
from collections.abc import Sequence
from typing import Any

import fastapi
from fastapi.middleware import cors

from joseph_execution.api import errors
from joseph_execution.api import services
from joseph_execution.api.routers import accounts
from joseph_execution.api.routers import deployments
from joseph_execution.api.routers import risk
from joseph_execution.api.routers import system
from joseph_execution.api.routers import trading
from joseph_execution.domain import ids
from joseph_execution.domain import tracing

_TRACE = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")

BackgroundTask = Callable[[], Coroutine[Any, Any, None]]


def create_app(
    svc: services.ApiServices,
    background: Sequence[BackgroundTask] = (),
) -> fastapi.FastAPI:
    """Builds the execution engine application.

    Args:
        svc: Services.
        background: Long-running coroutines started with the app.

    Returns:
        Application.
    """

    @contextlib.asynccontextmanager
    async def lifespan(app: fastapi.FastAPI) -> AsyncIterator[None]:
        """Starts and stops background loops.

        Args:
            app: Application.

        Yields:
            Control while the app runs.
        """
        del app
        import asyncio  # noqa: PLC0415 - only needed for lifespan.

        tasks: list[asyncio.Task[None]] = [
            asyncio.create_task(job()) for job in background
        ]
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            for task in tasks:
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    app = fastapi.FastAPI(
        title="Joseph Execution Engine",
        version="0.5.0",
        description="Private trust boundary: risk gateway, OMS, accounts, "
        "audit and kill switch. LIVE trading is disabled by default.",
        lifespan=lifespan,
    )
    app.state.services = svc
    app.add_middleware(
        cors.CORSMiddleware,
        allow_origins=list(svc.settings.cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "Idempotency-Key",
            "X-Trace-Id",
        ],
    )

    @app.middleware("http")
    async def trace(
        request: fastapi.Request,
        call_next: Callable[[fastapi.Request], Awaitable[fastapi.Response]],
    ) -> fastapi.Response:
        """Binds a trace id to the request context.

        Args:
            request: Request.
            call_next: Next handler.

        Returns:
            Response carrying ``X-Trace-Id``.
        """
        incoming = request.headers.get("x-trace-id", "")
        trace_id = incoming if _TRACE.match(incoming) else ids.uuid7()
        token = tracing.bind_trace_id(trace_id)
        try:
            response = await call_next(request)
        finally:
            tracing.reset_trace_id(token)
        response.headers["X-Trace-Id"] = trace_id
        return response

    errors.install(app)
    for module in (system, accounts, trading, risk, deployments):
        app.include_router(module.build_router())
    return app
