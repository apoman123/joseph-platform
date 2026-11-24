# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Execution engine with a settable clock for deterministic system tests.

``create_application`` builds the real engine like
``joseph_execution.bootstrap.create_application`` (settings from the
environment, every adapter, the HTTP market gateway and the background
jobs) but injects a ``ManualClock`` through ``bootstrap.build_container``'s
``clock`` parameter, the hook the engine's own test harness uses. Exchange
sessions, DAY expiry and settlement are then evaluated at the time the
suite sets instead of the wall clock.

The clock starts at ``JOSEPH_SYSTEM_TEST_CLOCK_MS`` (the wall clock when
unset) and stays there until ``PUT /system-test/clock`` with
``{"now_ms": <UTC epoch ms>}`` and the ``X-Joseph-Service-Token`` header
moves it (backwards too). This module is test infrastructure: it is never
packaged or deployed. Run it from the execution engine's environment as
``uvicorn clocked_execution:create_application --factory`` with
``--app-dir system-tests/launchers`` (``tests/conftest.py`` does).
"""

from __future__ import annotations

import hmac
import os
import time

import fastapi
import pydantic
from joseph_execution import bootstrap
from joseph_execution.platform import clock as clock_lib
from joseph_execution.platform import config

CLOCK_PATH = "/system-test/clock"
"""Route that reads (``GET``) and sets (``PUT``) the engine clock."""


class ClockBody(pydantic.BaseModel):
    """Request body of ``PUT /system-test/clock``.

    Attributes:
        now_ms: New engine time in UTC epoch milliseconds.
    """

    now_ms: int = pydantic.Field(ge=0)


def clock_router(
    the_clock: clock_lib.ManualClock, service_token: str | None
) -> fastapi.APIRouter:
    """Builds the clock control routes.

    Args:
        the_clock: Clock shared by every engine component.
        service_token: Token required in ``X-Joseph-Service-Token``.

    Returns:
        Router with ``GET`` and ``PUT`` on ``CLOCK_PATH``.
    """
    router = fastapi.APIRouter(tags=["system-test"])

    def require_token(presented: str | None) -> None:
        """Rejects callers without the service token.

        Args:
            presented: ``X-Joseph-Service-Token`` header value, if any.

        Raises:
            fastapi.HTTPException: 403 without a matching token.
        """
        if not (
            service_token
            and presented
            and hmac.compare_digest(presented, service_token)
        ):
            raise fastapi.HTTPException(403, "service token required")

    @router.get(CLOCK_PATH)
    async def read_clock() -> dict[str, int]:
        """Returns the engine time; no token is needed to read it.

        Returns:
            ``{"now_ms": <UTC epoch ms>}``.
        """
        return {"now_ms": the_clock.now_ms()}

    @router.put(CLOCK_PATH)
    async def set_clock(
        body: ClockBody,
        token: str | None = fastapi.Header(
            None, alias="X-Joseph-Service-Token"
        ),
    ) -> dict[str, int]:
        """Pins the engine time, earlier or later than the current one.

        Args:
            body: New engine time.
            token: ``X-Joseph-Service-Token`` header value, if any.

        Returns:
            ``{"now_ms": <UTC epoch ms>}`` after the change.

        Raises:
            fastapi.HTTPException: 403 without the service token.
        """
        require_token(token)
        the_clock.current_ms = body.now_ms
        return {"now_ms": the_clock.now_ms()}

    return router


def create_application() -> fastapi.FastAPI:
    """Builds the execution engine on a settable clock (uvicorn factory).

    Returns:
        The engine's application with the clock routes added.
    """
    settings = config.from_env()
    raw = os.environ.get("JOSEPH_SYSTEM_TEST_CLOCK_MS", "").strip()
    start_ms = int(raw) if raw else int(time.time() * 1000)
    the_clock = clock_lib.ManualClock(start_ms)
    container = bootstrap.build_container(settings, clock=the_clock)
    app = bootstrap.build_app(container)
    app.include_router(clock_router(the_clock, settings.service_token))
    return app
