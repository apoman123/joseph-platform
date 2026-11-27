# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Authenticated authoring.

Authenticated authoring and isolated execution of five Python script kinds.
"""

from collections.abc import Callable
from typing import Annotated

import fastapi
import pydantic

from joseph.domain import errors
from joseph.domain import identity
from joseph.scripts import models
from joseph.scripts import service as script_service


class ScriptList(pydantic.BaseModel):
    """An owned script catalog and its execution availability.

    Attributes:
        scripts: Latest visible scripts or requested immutable version history.
        runtime_enabled: Whether an isolated runtime has been enabled by
            operator.
        next_offset: Next bounded catalog window, or None at the end.
    """

    scripts: list[models.ScriptVersion]
    runtime_enabled: bool
    next_offset: int | None = None


def build_router(
    service: script_service.ScriptService,
    get_principal: Callable[..., identity.Principal],
) -> fastapi.APIRouter:
    """Builds private script routes.

    Builds private script routes without exposing host execution or broker keys.

    Args:
        service: Script authoring and isolated simulation service.
        get_principal: Existing bearer-token authentication dependency.

    Returns:
        Router serving source, versions, validation and isolated script runs.
    """
    router = fastapi.APIRouter(prefix="/v1/scripts", tags=["python-scripts"])
    principal_dep = Annotated[
        identity.Principal, fastapi.Depends(get_principal)
    ]

    @router.get("")
    def list_scripts(
        principal: principal_dep,
        limit: Annotated[int, fastapi.Query(ge=1, le=100)] = 100,
        offset: Annotated[int, fastapi.Query(ge=0)] = 0,
    ) -> ScriptList:
        """Lists the authenticated tenant's visible Python scripts.

        Args:
            principal: Authenticated tenant owner.
            limit: Maximum sources, one through one hundred.
            offset: Nonnegative catalog offset.

        Returns:
            Private catalog and execution availability.
        """
        rows = service.repository.list(
            principal.tenant_id, limit=limit, offset=offset
        )
        return ScriptList(
            scripts=rows,
            runtime_enabled=service.runner.enabled,
            next_offset=offset + len(rows) if len(rows) == limit else None,
        )

    @router.post("", status_code=201)
    def create_script(
        body: models.ScriptInput, principal: principal_dep
    ) -> models.ScriptVersion:
        """Creates a private Python script at immutable version one.

        Args:
            body: Declared script kind, source, inputs and pinned imports.
            principal: Authenticated author with strategy:write permission.

        Returns:
            Newly saved immutable version.

        Raises:
            errors.JosephError: On invalid source, dependencies, access or slug
                conflict.
        """
        return service.save(principal, body, create=True)

    @router.post("/validate")
    def validate_script(
        body: models.ScriptInput, principal: principal_dep
    ) -> dict[str, object]:
        """Validates source structure and imports without running user code.

        Args:
            body: Script source, declaration and owned dependency pins.
            principal: Authenticated tenant owner.

        Returns:
            Validity, kind and executed false; invalid input returns HTTP 422.

        Raises:
            errors.JosephError: If source or an owned pinned import is invalid.
        """
        return service.validate(principal, body)

    @router.get("/{script_id}")
    def get_script(
        script_id: str, principal: principal_dep
    ) -> models.ScriptVersion:
        """Reads the latest visible version of an owned Python script.

        Args:
            script_id: Tenant-local script slug.
            principal: Authenticated tenant owner.

        Returns:
            Immutable version including source and pinned dependency metadata.

        Raises:
            errors.NotFoundError: If absent, archived or owned by another
                tenant.
        """
        return service.repository.get(principal.tenant_id, script_id)

    @router.get("/{script_id}/versions")
    def list_versions(
        script_id: str,
        principal: principal_dep,
        limit: Annotated[int, fastapi.Query(ge=1, le=100)] = 100,
        offset: Annotated[int, fastapi.Query(ge=0)] = 0,
    ) -> ScriptList:
        """Lists immutable version history for an owned script.

        Args:
            script_id: Tenant-local script slug.
            principal: Authenticated tenant owner.
            limit: Maximum immutable versions, one through one hundred.
            offset: Nonnegative version-history offset.

        Returns:
            Descending version history; empty for an absent script.
        """
        rows = service.repository.list(
            principal.tenant_id, script_id=script_id, limit=limit, offset=offset
        )
        return ScriptList(
            scripts=rows,
            runtime_enabled=service.runner.enabled,
            next_offset=offset + len(rows) if len(rows) == limit else None,
        )

    @router.get("/{script_id}/versions/{version}")
    def get_version(
        script_id: str,
        version: Annotated[int, fastapi.Path(ge=1)],
        principal: principal_dep,
    ) -> models.ScriptVersion:
        """Reads a pinned private version, including retained archived imports.

        Args:
            script_id: Tenant-local script slug.
            version: Positive immutable version number.
            principal: Authenticated tenant owner.

        Returns:
            Exact source version and content hash for reproducibility.

        Raises:
            errors.NotFoundError: If the owned immutable version is absent.
        """
        return service.repository.get(principal.tenant_id, script_id, version)

    @router.post("/{script_id}/versions", status_code=201)
    def save_version(
        script_id: str, body: models.ScriptInput, principal: principal_dep
    ) -> models.ScriptVersion:
        """Appends a version only when its expected edit version still matches.

        Args:
            script_id: Slug matching the request body.
            body: New source and explicit expected_version lock.
            principal: Authenticated author with strategy:write permission.

        Returns:
            Newly persisted immutable version without changing existing pins.

        Raises:
            errors.JosephError: On path mismatch, stale edit, invalid source or
                access.
        """
        if body.script_id != script_id:
            raise errors.ValidationFailedError(
                "script_id does not match the path"
            )
        return service.save(principal, body, create=False)

    @router.delete("/{script_id}", status_code=204)
    def archive_script(
        script_id: str, principal: principal_dep
    ) -> fastapi.Response:
        """Archives an owned script while preserving immutable library imports.

        Args:
            script_id: Tenant-local script slug.
            principal: Authenticated author with strategy:write permission.

        Returns:
            Empty HTTP 204 response after hiding the script's latest head.

        Raises:
            errors.ForbiddenError: If the caller cannot author scripts.
            errors.NotFoundError: If the owned visible script is absent.
        """
        script_service.require_write(principal)
        service.repository.archive(principal.tenant_id, script_id)
        return fastapi.Response(status_code=204)

    @router.post("/{script_id}/run")
    async def run_script(
        script_id: str, body: models.ScriptRun, principal: principal_dep
    ) -> models.ScriptResult:
        """Calculates plots or simulates a strategy without placing real orders.

        Args:
            script_id: Tenant-local indicator or strategy slug.
            body: Version, closed-bar range, inputs and simulation costs.
            principal: Authenticated tenant owner.

        Returns:
            Validated plots, provenance and optional simulation
            fills/performance.

        Raises:
            errors.JosephError: For absent data, invalid source/output, disabled
                runtime, resource limits or standalone library execution.
        """
        return await service.run(principal, script_id, body)

    return router
