# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Authenticated authoring.

Authenticated authoring and isolated execution of five Python script kinds.
"""

from collections.abc import Callable
from typing import Annotated
from typing import Literal

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


class CatalogInput(pydantic.BaseModel):
    """A declared input of a built-in or plugin script.

    Attributes:
        default: JSON default value.
        title: Settings label.
        minimum: Optional inclusive numeric lower bound.
        maximum: Optional inclusive numeric upper bound.
        options: Optional accepted values.
        step: Optional numeric step.
        group: Optional settings section.
        tooltip: Optional help text.
    """

    default: pydantic.JsonValue
    title: str
    minimum: float | None
    maximum: float | None
    options: list[pydantic.JsonValue] | None
    step: float | None
    group: str
    tooltip: str


class CatalogScript(pydantic.BaseModel):
    """A built-in or installed plugin script that the engine runs in process.

    Attributes:
        script_id: ``builtin:<id>`` or ``plugin:<entry point>:<id>``.
        kind: indicator, strategy, library, filter or alert.
        origin: builtin or plugin.
        package: Providing distribution of a plugin.
        package_version: That distribution's version.
        title: Display name.
        short_title: Compact legend name.
        description: Catalog description.
        category: Catalog section.
        overlay: Whether plots share the price pane.
        inputs: Declared inputs in display order.
        requests: Declared additional series by alias.
        source_available: Whether the public source can be read.
        source_hash: Identity of the installed code.
        enabled: False when disabled by configuration.
        source: Public source text on the single-script route, else None.
        metrics: Market data a filter loads beyond the snapshot.
    """

    script_id: str
    kind: models.ScriptKind
    origin: Literal["builtin", "plugin"]
    package: str | None
    package_version: str | None
    title: str
    short_title: str
    description: str
    category: str
    overlay: bool
    inputs: dict[str, CatalogInput]
    requests: dict[str, dict[str, pydantic.JsonValue]]
    source_available: bool
    source_hash: str
    enabled: bool
    source: str | None = None
    metrics: list[str] = pydantic.Field(default_factory=list)


class CatalogList(pydantic.BaseModel):
    """Installed built-in and plugin scripts and skipped plugin packages.

    Attributes:
        scripts: Scripts in catalog order.
        failures: Plugin entry points that failed to load, with reasons.
    """

    scripts: list[CatalogScript]
    failures: list[dict[str, str | None]]


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

    @router.get("/catalog")
    def list_catalog(
        principal: principal_dep,
        kind: models.ScriptKind | None = None,
    ) -> CatalogList:
        """Lists the built-in and installed plugin scripts.

        Args:
            principal: Authenticated caller.
            kind: Optional role filter.

        Returns:
            Catalog scripts without source text, and skipped plugins.
        """
        del principal
        return CatalogList(
            scripts=[
                CatalogScript.model_validate(entry.describe())
                for entry in service.catalog.entries(kind)
            ],
            failures=[
                {
                    "entry_point": failure.entry_point,
                    "distribution": failure.distribution,
                    "reason": failure.reason,
                }
                for failure in service.catalog.report.failures
            ],
        )

    @router.get("/catalog/{script_id}")
    def get_catalog_script(
        script_id: str, principal: principal_dep
    ) -> CatalogScript:
        """Reads one catalog script, with source when it is public.

        Args:
            script_id: Catalog id.
            principal: Authenticated caller.

        Returns:
            The script; plugin source stays protected (None).

        Raises:
            errors.NotFoundError: If the script is not installed.
        """
        del principal
        if script_id not in service.catalog:
            raise errors.NotFoundError(f"script {script_id} is not installed")
        entry = next(
            item
            for item in service.catalog.entries()
            if item.script_id == script_id
        )
        return CatalogScript.model_validate(
            {**entry.describe(), "source": entry.source}
        )

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
            script_id: Tenant slug, or a built-in/plugin catalog id.
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
