# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Order, position and fill endpoints."""

from __future__ import annotations

import fastapi
from fastapi import responses

from joseph_execution.api import auth
from joseph_execution.api import schemas
from joseph_execution.domain import decimals
from joseph_execution.domain import enums
from joseph_execution.domain import errors
from joseph_execution.domain import identity
from joseph_execution.domain import intents

# Shared parameter default; a module constant keeps calls out of argument
# defaults (ruff B008).
_Principal = fastapi.Depends(auth.principal)


def _request(body: schemas.IntentBody) -> intents.OrderIntentRequest:
    """Converts a validated body into a domain request.

    Args:
        body: Body.

    Returns:
        Domain request.

    Raises:
        ExecutionError: ``VALIDATION_FAILED`` when the body violates the
            request's structural invariants.
    """
    return intents.OrderIntentRequest.from_contract(body.model_dump())


def _idempotency(header: str | None) -> str:
    """Validates the Idempotency-Key header.

    Args:
        header: Header value.

    Returns:
        Key, unchanged.

    Raises:
        ExecutionError: ``VALIDATION_FAILED`` when missing or empty.
    """
    if not header:
        raise errors.ExecutionError(
            "VALIDATION_FAILED", "Idempotency-Key header is required"
        )
    return header


def _states(state: str) -> list[enums.OrderState]:
    """Parses the ``state`` query parameter of the order list.

    Route docstrings become OpenAPI descriptions, so the route delegates
    this to keep its own docstring (and the generated document) stable.

    Args:
        state: Comma-separated order states; empty items are ignored.

    Returns:
        The states, in the given order.

    Raises:
        ExecutionError: ``VALIDATION_FAILED`` for an unknown state.
    """
    try:
        return [enums.OrderState(s) for s in state.split(",") if s]
    except ValueError as err:
        raise errors.ExecutionError(
            "VALIDATION_FAILED", "unknown order state"
        ) from err


async def _close_request(
    request: fastapi.Request,
    principal: identity.Principal,
    position_id: str,
    body: schemas.CloseBody,
) -> intents.OrderIntentRequest:
    """Builds a reduce-only close request for a position.

    Args:
        request: Request.
        principal: Caller.
        position_id: Position.
        body: Close body.

    Returns:
        Domain request.
    """
    return await auth.api(request).oms.close_request(
        principal,
        position_id,
        decimals.to_decimal(body.fraction),
        enums.OrderType(body.order_type),
        decimals.to_optional(body.limit_price),
    )


def build_router() -> fastapi.APIRouter:
    """Builds the trading router.

    Returns:
        Router.
    """
    router = fastapi.APIRouter(prefix="/v1", tags=["orders"])

    @router.post("/orders/preview")
    async def preview(
        body: schemas.IntentBody,
        request: fastapi.Request,
        principal: identity.Principal = _Principal,
    ) -> dict[str, object]:
        """Previews an order with every risk check.

        Args:
            body: Draft intent.
            request: Request.
            principal: Caller.

        Returns:
            Contract ``OrderPreview``.
        """
        result = await auth.api(request).oms.preview(principal, _request(body))
        return result.to_contract()

    @router.post("/orders/intents", status_code=201)
    async def submit(
        body: schemas.IntentBody,
        request: fastapi.Request,
        principal: identity.Principal = _Principal,
        idempotency_key: str | None = fastapi.Header(
            default=None, alias="Idempotency-Key"
        ),
    ) -> responses.JSONResponse:
        """Submits an order intent.

        Args:
            body: Intent.
            request: Request.
            principal: Caller.
            idempotency_key: Header.

        Returns:
            ``SubmitIntentResponse`` (201, or 200 when replayed).
        """
        result = await auth.api(request).oms.submit_intent(
            principal, _request(body), _idempotency(idempotency_key)
        )
        return responses.JSONResponse(
            result.to_contract(), status_code=200 if result.replayed else 201
        )

    @router.get("/orders")
    async def list_orders(
        request: fastapi.Request,
        principal: identity.Principal = _Principal,
        account_id: str | None = None,
        state: str | None = None,
    ) -> dict[str, object]:
        """Lists orders.

        Args:
            request: Request.
            principal: Caller.
            account_id: Account filter.
            state: Comma-separated states.

        Returns:
            Contract ``OrderList``.
        """
        states = _states(state) if state else None
        items = auth.api(request).oms.list_orders(principal, account_id, states)
        return {"orders": [o.to_contract() for o in items]}

    @router.post("/orders/{order_id}/cancel")
    async def cancel(
        order_id: str,
        request: fastapi.Request,
        principal: identity.Principal = _Principal,
    ) -> dict[str, object]:
        """Requests cancellation.

        Args:
            order_id: Order.
            request: Request.
            principal: Caller.

        Returns:
            Contract ``Order``.
        """
        order = await auth.api(request).oms.cancel_order(principal, order_id)
        return order.to_contract()

    @router.post("/orders/{order_id}/amend")
    async def amend(
        order_id: str,
        body: schemas.AmendBody,
        request: fastapi.Request,
        principal: identity.Principal = _Principal,
    ) -> dict[str, object]:
        """Amends a working order.

        Args:
            order_id: Order.
            body: Amendment.
            request: Request.
            principal: Caller.

        Returns:
            Contract ``Order``.
        """
        order = await auth.api(request).oms.amend_order(
            principal,
            order_id,
            decimals.to_optional(body.limit_price),
            decimals.to_optional(body.trigger_price),
            decimals.to_optional(body.quantity_base),
            body.expected_version,
        )
        return order.to_contract()

    @router.get("/positions")
    async def list_positions(
        request: fastapi.Request,
        principal: identity.Principal = _Principal,
        account_id: str | None = None,
    ) -> dict[str, object]:
        """Lists positions.

        Args:
            request: Request.
            principal: Caller.
            account_id: Account filter.

        Returns:
            Contract ``PositionList``.
        """
        items = await auth.api(request).oms.list_positions(
            principal, account_id
        )
        return {"positions": items}

    @router.post("/positions/{position_id}/close-preview")
    async def close_preview(
        position_id: str,
        body: schemas.CloseBody,
        request: fastapi.Request,
        principal: identity.Principal = _Principal,
    ) -> dict[str, object]:
        """Previews a reduce-only close.

        Args:
            position_id: Position.
            body: Close body.
            request: Request.
            principal: Caller.

        Returns:
            Contract ``OrderPreview``.
        """
        draft = await _close_request(request, principal, position_id, body)
        result = await auth.api(request).oms.preview(principal, draft)
        return result.to_contract()

    @router.post("/positions/{position_id}/close", status_code=201)
    async def close(
        position_id: str,
        body: schemas.CloseBody,
        request: fastapi.Request,
        principal: identity.Principal = _Principal,
        idempotency_key: str | None = fastapi.Header(
            default=None, alias="Idempotency-Key"
        ),
    ) -> responses.JSONResponse:
        """Closes (part of) a position with a reduce-only order.

        Args:
            position_id: Position.
            body: Close body.
            request: Request.
            principal: Caller.
            idempotency_key: Header.

        Returns:
            ``SubmitIntentResponse``.
        """
        key = _idempotency(idempotency_key)
        draft = await _close_request(request, principal, position_id, body)
        if body.preview_token:
            draft = intents.OrderIntentRequest.from_contract(
                {**draft.to_contract(), "preview_token": body.preview_token}
            )
        result = await auth.api(request).oms.submit_intent(
            principal, draft, key
        )
        return responses.JSONResponse(
            result.to_contract(), status_code=200 if result.replayed else 201
        )

    @router.get("/fills")
    async def list_fills(
        request: fastapi.Request,
        principal: identity.Principal = _Principal,
        account_id: str | None = None,
    ) -> dict[str, object]:
        """Lists fills.

        Args:
            request: Request.
            principal: Caller.
            account_id: Account filter.

        Returns:
            Contract ``FillList``.
        """
        items = auth.api(request).oms.list_fills(principal, account_id)
        return {"fills": [f.to_contract() for f in items]}

    return router
