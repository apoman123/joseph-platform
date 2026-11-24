# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Signal history route (``/v1/signals``).

No postponed annotations: FastAPI resolves the injected dependency.
"""

from collections.abc import Callable
from typing import Annotated

import fastapi

from joseph.domain import identity
from joseph.signals.app import ports
from joseph.signals.app import service as signal_service


def build_router(
    *,
    signals: signal_service.SignalService,
    get_principal: Callable[..., identity.Principal],
) -> fastapi.APIRouter:
    """Builds the signal router.

    The route requires a bearer token; ``get_principal`` answers 401
    without a valid one.

    Args:
        signals: Signal service.
        get_principal: Authentication dependency.

    Returns:
        Router serving ``GET /v1/signals``.
    """
    router = fastapi.APIRouter(tags=["signals"])
    principal_dep = Annotated[
        identity.Principal, fastapi.Depends(get_principal)
    ]

    @router.get("/v1/signals")
    def list_signals(
        principal: principal_dep,
        *,
        strategy_id: str | None = None,
        kind: str | None = None,
        instrument_id: str | None = None,
        from_ms: Annotated[int | None, fastapi.Query(ge=0)] = None,
        to_ms: Annotated[int | None, fastapi.Query(ge=0)] = None,
        limit: Annotated[int, fastapi.Query(ge=1, le=500)] = 100,
    ) -> dict[str, object]:
        """Lists the caller's signals, newest first.

        Args:
            principal: Caller; only their tenant's signals are listed.
            strategy_id: Strategy filter.
            kind: Comma-separated signal kinds; empty parts are ignored.
            instrument_id: Instrument filter.
            from_ms: Inclusive lower bound on bar close, UTC epoch
                milliseconds.
            to_ms: Exclusive upper bound on bar close, UTC epoch
                milliseconds.
            limit: Maximum rows (1..500, 422 outside).

        Returns:
            Contract ``SignalList``; ``next_cursor`` is always ``None``
            because the route does not paginate.

        Raises:
            errors.ValidationFailedError: If ``from_ms`` is not before
                ``to_ms`` (422).
        """
        kinds = tuple(k for k in (kind or "").split(",") if k)
        found = signals.query(
            principal,
            ports.SignalFilter(
                strategy_id=strategy_id,
                kinds=kinds,
                instrument_id=instrument_id,
                from_ms=from_ms,
                to_ms=to_ms,
                limit=limit,
            ),
        )
        return {
            "signals": [s.to_contract() for s in found],
            "next_cursor": None,
        }

    return router
