# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Live activations of Python Strategy scripts.

Backtests use ``POST /v1/scripts/{script_id}/run``, which returns the
simulated fills, orders, trades, equity and metrics of a strategy.
"""

from collections.abc import Callable
from typing import Annotated

import fastapi

from joseph.domain import identity
from joseph.scripts import activations


def build_router(
    service: activations.StrategyActivations,
    get_principal: Callable[..., identity.Principal],
) -> fastapi.APIRouter:
    """Builds the activation routes.

    Args:
        service: Activation store and evaluation loop.
        get_principal: Bearer-token authentication dependency.

    Returns:
        Router under ``/v1/strategy-activations``.
    """
    router = fastapi.APIRouter(
        prefix="/v1/strategy-activations", tags=["strategy-activations"]
    )
    owner = Annotated[identity.Principal, fastapi.Depends(get_principal)]

    @router.get("")
    def list_activations(
        principal: owner,
    ) -> activations.StrategyActivationList:
        """Lists the caller's activations, newest first.

        Args:
            principal: Authenticated owner.

        Returns:
            Activations with runtime and forwarding availability.
        """
        return service.list_owned(principal)

    @router.post("", status_code=201)
    def create_activation(
        body: activations.StrategyActivationInput, principal: owner
    ) -> activations.StrategyActivation:
        """Activates a strategy on future closed bars.

        AUTO_LIVE is refused with 409 TRADE_MODE_DISABLED: live trading
        exists only as an execution-engine deployment with scoped consent.

        Args:
            body: Strategy, instruments, timeframe, inputs, mode, channels.
            principal: Authenticated author.

        Returns:
            The ACTIVE activation.
        """
        return service.create(principal, body)

    @router.get("/{activation_id}")
    def get_activation(
        activation_id: str, principal: owner
    ) -> activations.StrategyActivation:
        """Reads one activation with its per-instrument progress.

        Args:
            activation_id: Activation id.
            principal: Authenticated owner.

        Returns:
            The activation.
        """
        return service.get(principal, activation_id)

    @router.post("/{activation_id}/pause")
    def pause_activation(
        activation_id: str, principal: owner
    ) -> activations.StrategyActivation:
        """Stops evaluating an activation.

        Args:
            activation_id: Activation id.
            principal: Authenticated author.

        Returns:
            The PAUSED activation.
        """
        return service.set_state(principal, activation_id, "PAUSED")

    @router.post("/{activation_id}/resume")
    def resume_activation(
        activation_id: str, principal: owner
    ) -> activations.StrategyActivation:
        """Resumes an activation from the next closed bar on.

        Args:
            activation_id: Activation id.
            principal: Authenticated author.

        Returns:
            The ACTIVE activation; bars closed while paused never signal.
        """
        return service.set_state(principal, activation_id, "ACTIVE")

    @router.delete("/{activation_id}", status_code=204)
    def delete_activation(
        activation_id: str, principal: owner
    ) -> fastapi.Response:
        """Deletes an activation; recorded signals remain.

        Args:
            activation_id: Activation id.
            principal: Authenticated author.

        Returns:
            Empty 204 response.
        """
        service.delete(principal, activation_id)
        return fastapi.Response(status_code=204)

    return router
