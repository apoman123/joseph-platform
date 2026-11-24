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

# Strategy slugs: lower-case alphanumerics and dashes, 1 to 63 characters;
# a path that does not match is answered with 422.
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

    Every route requires a bearer token; ``get_principal`` answers 401
    without a valid one. Bodies that do not match the rule AST schema are
    answered with 422 ``VALIDATION_FAILED``.

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
        """Stores a new strategy as version 1 (201).

        A semantically invalid definition is still stored, as a DRAFT.

        Args:
            definition: Rule AST v2 definition.
            principal: Caller; needs the ``strategy:write`` scope.

        Returns:
            Contract ``StrategyVersion``.

        Raises:
            errors.ConflictError: If the strategy id already exists (409).
            errors.ForbiddenError: If the caller may not write (403).
        """
        return versions.create(principal, definition).to_contract()

    @router.get("")
    def list_strategies(principal: principal_dep) -> dict[str, object]:
        """Lists the latest version of each strategy.

        Args:
            principal: Caller; only their tenant's strategies are listed.

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
            definition: Rule AST v2 definition.
            principal: Caller.

        Returns:
            Contract ``ValidationResult``; an invalid definition is a 200
            response with ``valid`` false, not an error.
        """
        del principal  # Unused: the dependency only enforces sign-in.
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

        Raises:
            errors.NotFoundError: If the caller's tenant has no such
                strategy (404).
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
        """Stores a new immutable version (201).

        A definition identical to the latest version returns that version
        instead of creating a duplicate.

        Args:
            strategy_id: Strategy slug.
            definition: Edited definition; its ``strategy_id`` must match
                the path.
            principal: Caller; needs the ``strategy:write`` scope.

        Returns:
            Contract ``StrategyVersion``.

        Raises:
            errors.ValidationFailedError: If the definition's id differs
                from the path (422).
            errors.NotFoundError: If the strategy does not exist (404).
            errors.ForbiddenError: If the caller may not write (403).
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

        A version that no longer validates becomes UNSUPPORTED and is
        deactivated.

        Args:
            strategy_id: Strategy slug.
            principal: Caller.
            body: Optional version selector; the latest version when
                omitted.

        Returns:
            Contract ``ValidationResult``.

        Raises:
            errors.NotFoundError: If the strategy or version does not exist
                (404).
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
            body: Version, mode and optional instruments.
            principal: Caller; needs the ``strategy:write`` scope.

        Returns:
            Contract ``StrategyVersion``.

        Raises:
            strategy_errors.TradeModeDisabledError: For ``AUTO_LIVE`` (409
                ``TRADE_MODE_DISABLED``).
            errors.ValidationFailedError: For an unknown mode or instrument,
                or a version invalid against the current catalog (422).
            errors.NotFoundError: If the version does not exist (404).
            errors.ForbiddenError: If the caller may not write (403).
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
            principal: Caller; needs the ``strategy:write`` scope.

        Returns:
            Contract ``StrategyVersion``.

        Raises:
            errors.ConflictError: If no version is active (409).
            errors.ForbiddenError: If the caller may not write (403).
        """
        return versions.pause(principal, strategy_id).to_contract()

    return router
