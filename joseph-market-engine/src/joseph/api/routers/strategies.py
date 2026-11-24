# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Strategy authoring routes (``/v1/strategies``).

This module deliberately does not use postponed annotations: FastAPI must
resolve the injected ``get_principal`` dependency from the closure.
"""

from collections.abc import Callable
from typing import Annotated

import fastapi
import pydantic

from joseph.domain import identity
from joseph.strategy.app import version_service
from joseph.strategy.domain import ast

_STRATEGY_ID = r"^[a-z0-9][a-z0-9-]{0,62}$"


class ActivateBody(pydantic.BaseModel):
    """Body of ``POST /{strategy_id}/activate`` (contract ActivateRequest).

    ``mode`` is accepted as text so AUTO_LIVE gets an explicit
    TRADE_MODE_DISABLED explanation instead of a generic schema error.

    Attributes:
        version: Version to activate.
        mode: ``ALERT_ONLY``, ``PAPER`` or ``SHADOW``.
        instruments: Optional explicit instruments.
    """

    model_config = pydantic.ConfigDict(extra="forbid")

    version: Annotated[int, pydantic.Strict(), pydantic.Field(ge=1)]
    mode: str
    instruments: list[ast.InstrumentIdStr] = pydantic.Field(
        default_factory=list
    )


class RevalidateBody(pydantic.BaseModel):
    """Optional body of ``POST /{strategy_id}/validate``.

    Attributes:
        version: Version to re-validate (latest when omitted).
    """

    model_config = pydantic.ConfigDict(extra="forbid")

    version: Annotated[int, pydantic.Strict(), pydantic.Field(ge=1)] | None = (
        None
    )


def build_router(
    *,
    versions: version_service.StrategyVersionService,
    get_principal: Callable[..., identity.Principal],
) -> fastapi.APIRouter:
    """Builds the strategy router.

    Args:
        versions: Strategy version service.
        get_principal: FastAPI dependency returning the authenticated
            principal (raises for anonymous callers).

    Returns:
        Router mounted at ``/v1/strategies``.
    """
    router = fastapi.APIRouter(prefix="/v1/strategies", tags=["strategies"])
    principal_dep = Annotated[
        identity.Principal, fastapi.Depends(get_principal)
    ]
    strategy_path = Annotated[str, fastapi.Path(pattern=_STRATEGY_ID)]

    @router.post("", status_code=201)
    def create_strategy(
        definition: ast.StrategyDefinition, principal: principal_dep
    ) -> dict[str, object]:
        """Stores a new strategy as version 1.

        Args:
            definition: Rule AST v2 definition.
            principal: Caller.

        Returns:
            Contract ``StrategyVersion``.
        """
        return versions.create(principal, definition).to_contract()

    @router.get("")
    def list_strategies(principal: principal_dep) -> dict[str, object]:
        """Lists the latest version of each strategy.

        Args:
            principal: Caller.

        Returns:
            Contract ``StrategyVersionList``.
        """
        return {
            "versions": [
                v.to_contract() for v in versions.list_latest(principal)
            ]
        }

    @router.post("/validate")
    def validate_definition(
        definition: ast.StrategyDefinition, principal: principal_dep
    ) -> dict[str, object]:
        """Validates a definition without storing it.

        Args:
            definition: Definition.
            principal: Caller.

        Returns:
            Contract ``ValidationResult``.
        """
        del principal
        return versions.validate(definition).to_contract()

    @router.get("/{strategy_id}")
    def get_strategy(
        strategy_id: strategy_path, principal: principal_dep
    ) -> dict[str, object]:
        """Lists every version of one strategy.

        Args:
            strategy_id: Strategy slug.
            principal: Caller.

        Returns:
            Contract ``StrategyVersionList``.
        """
        return {
            "versions": [
                v.to_contract()
                for v in versions.get_versions(principal, strategy_id)
            ]
        }

    @router.post("/{strategy_id}/versions", status_code=201)
    def create_version(
        strategy_id: strategy_path,
        definition: ast.StrategyDefinition,
        principal: principal_dep,
    ) -> dict[str, object]:
        """Stores a new immutable version.

        Args:
            strategy_id: Strategy slug.
            definition: Edited definition.
            principal: Caller.

        Returns:
            Contract ``StrategyVersion``.
        """
        return versions.create_version(
            principal, strategy_id, definition
        ).to_contract()

    @router.post("/{strategy_id}/validate")
    def revalidate(
        strategy_id: strategy_path,
        principal: principal_dep,
        body: RevalidateBody | None = None,
    ) -> dict[str, object]:
        """Re-validates a stored version against the current catalog.

        Args:
            strategy_id: Strategy slug.
            principal: Caller.
            body: Optional version selector.

        Returns:
            Contract ``ValidationResult``.
        """
        version = None if body is None else body.version
        return versions.revalidate(
            principal, strategy_id, version
        ).to_contract()

    @router.post("/{strategy_id}/activate")
    def activate(
        strategy_id: strategy_path, body: ActivateBody, principal: principal_dep
    ) -> dict[str, object]:
        """Activates a version in ALERT_ONLY, PAPER or SHADOW mode.

        Args:
            strategy_id: Strategy slug.
            body: Version and mode.
            principal: Caller.

        Returns:
            Contract ``StrategyVersion``.
        """
        return versions.activate(
            principal, strategy_id, body.version, body.mode, body.instruments
        ).to_contract()

    @router.post("/{strategy_id}/pause")
    def pause(
        strategy_id: strategy_path, principal: principal_dep
    ) -> dict[str, object]:
        """Pauses the active version.

        Args:
            strategy_id: Strategy slug.
            principal: Caller.

        Returns:
            Contract ``StrategyVersion``.
        """
        return versions.pause(principal, strategy_id).to_contract()

    return router
