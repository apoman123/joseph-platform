# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Private asynchronous Python market scans.

Private asynchronous Python market scans and persistent condition alerts.
"""

from collections.abc import Callable
from typing import Annotated

import fastapi

from joseph.domain import errors
from joseph.domain import identity
from joseph.scripts import jobs
from joseph.scripts import models


def build_router(
    service: jobs.ScriptJobs, get_principal: Callable[..., identity.Principal]
) -> fastapi.APIRouter:
    """Builds authenticated filter progress and future-bar alert controls.

    Args:
        service: Persisted jobs and bounded isolated execution coordinator.
        get_principal: Existing application bearer authentication dependency.

    Returns:
        Router with explicit submission, cancellation and notification controls.
    """
    router = fastapi.APIRouter(tags=["python-jobs"])
    owner = Annotated[identity.Principal, fastapi.Depends(get_principal)]

    @router.post("/v1/scripts/{script_id}/scan", status_code=202)
    async def scan(
        script_id: str, body: models.ScanInput, principal: owner
    ) -> models.ScanJob:
        """Submits a bounded real-market filter scan without blocking HTTP.

        Args:
            script_id: Owned Filter slug or ``builtin:``/``plugin:`` id;
                body conditions must match too.
            body: Market universe, execution bounds and extra conditions.
            principal: Authenticated author.

        Returns:
            Pending job and immutable source provenance.
        """
        return service.submit_scan(principal, script_id, body)

    @router.post("/v1/script-scans", status_code=202)
    async def screen(
        body: models.ScanInput, principal: owner
    ) -> models.ScanJob:
        """Submits a scan of the body's conditions combined with AND.

        Built-in and plugin filters run in process, so composing them
        needs no isolated runtime.

        Args:
            body: Market universe, bounds and one to ten conditions.
            principal: Authenticated author.

        Returns:
            Pending job with every filter pinned.
        """
        return service.submit_scan(principal, None, body)

    @router.get("/v1/script-scans/{job_id}")
    def progress(job_id: str, principal: owner) -> models.ScanJob:
        """Reads an owned scan's progress, matches and per-symbol errors.

        Args:
            job_id: Opaque owned scan identifier.
            principal: Authenticated tenant owner.

        Returns:
            Current persisted scan state.

        Raises:
            errors.NotFoundError: If the owned scan identifier is absent.
        """
        result = service.get(principal, job_id, "scan")
        if not isinstance(result, models.ScanJob):
            raise errors.NotFoundError("scan job not found")
        return result

    @router.post("/v1/script-scans/{job_id}/cancel")
    async def cancel(job_id: str, principal: owner) -> models.ScanJob:
        """Cancels a scan while retaining all completed decisions.

        Args:
            job_id: Owned active scan identifier.
            principal: Authenticated author.

        Returns:
            Terminal or newly cancelled scan state.
        """
        return service.cancel_scan(principal, job_id)

    @router.get("/v1/script-alerts")
    def list_alerts(principal: owner) -> list[models.AlertJob]:
        """Lists owned Python conditions, checkpoints and visible errors.

        Args:
            principal: Authenticated tenant owner.

        Returns:
            Persistent alert jobs, including disabled conditions.
        """
        return service.list_alerts(principal)

    @router.post("/v1/script-alerts", status_code=201)
    def create_alert(
        body: models.AlertJobInput, principal: owner
    ) -> models.AlertJob:
        """Creates a pinned condition, disabled by default.

        Args:
            body: Explicit source, symbol, inputs and selected destinations.
            principal: Authenticated author.

        Returns:
            Persistent job evaluating only future closed bars when enabled.
        """
        return service.save_alert(principal, body)

    @router.put("/v1/script-alerts/{job_id}")
    def update_alert(
        job_id: str, body: models.AlertJobInput, principal: owner
    ) -> models.AlertJob:
        """Replaces a condition or toggles its enabled state.

        Replaces a condition or toggles its enabled state with a new checkpoint.

        Args:
            job_id: Owned persistent alert identifier.
            body: Complete new condition configuration.
            principal: Authenticated author.

        Returns:
            Saved state with historical notifications suppressed.
        """
        return service.save_alert(principal, body, job_id)

    @router.delete("/v1/script-alerts/{job_id}", status_code=204)
    def delete_alert(job_id: str, principal: owner) -> fastapi.Response:
        """Stops and removes an owned condition while preserving signal history.

        Args:
            job_id: Owned condition identifier.
            principal: Authenticated author.

        Returns:
            Empty response after removal.
        """
        service.delete_alert(principal, job_id)
        return fastapi.Response(status_code=204)

    return router
