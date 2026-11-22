"""Deployment and internal signal endpoints."""

from __future__ import annotations

import fastapi
from fastapi import responses

from joseph_execution.api import auth
from joseph_execution.api import schemas
from joseph_execution.domain import deployments
from joseph_execution.domain import enums
from joseph_execution.domain import identity

_Principal = fastapi.Depends(auth.principal)


def build_router() -> fastapi.APIRouter:
    """Builds the deployments router.

    Returns:
        Router.
    """
    router = fastapi.APIRouter(tags=["deployments"])

    @router.get("/v1/deployments")
    async def list_deployments(
        request: fastapi.Request, principal: identity.Principal = _Principal
    ) -> dict[str, object]:
        """Lists deployments.

        Args:
            request: Request.
            principal: Caller.

        Returns:
            Contract ``DeploymentList``.
        """
        items = auth.api(request).deployments.list(principal)
        return {"deployments": [d.to_contract() for d in items]}

    @router.post("/v1/deployments", status_code=201)
    async def create(
        body: schemas.DeploymentBody,
        request: fastapi.Request,
        principal: identity.Principal = _Principal,
    ) -> responses.JSONResponse:
        """Creates a deployment.

        Args:
            body: Request.
            request: Request.
            principal: Caller.

        Returns:
            Contract ``Deployment``.
        """
        deployment = auth.api(request).deployments.create(
            principal,
            body.strategy_version_id,
            body.account_id,
            enums.DeploymentMode(body.mode),
            deployments.DeploymentLimits.from_contract(
                body.limits.model_dump()
            ),
            deployments.SizingModel.from_contract(body.sizing.model_dump()),
        )
        return responses.JSONResponse(deployment.to_contract(), status_code=201)

    @router.post("/v1/deployments/{deployment_id}/disable")
    async def disable(
        deployment_id: str,
        request: fastapi.Request,
        principal: identity.Principal = _Principal,
    ) -> dict[str, object]:
        """Disables a deployment.

        Args:
            deployment_id: Deployment.
            request: Request.
            principal: Caller.

        Returns:
            Contract ``Deployment``.
        """
        return (
            auth.api(request)
            .deployments.disable(principal, deployment_id)
            .to_contract()
        )

    @router.post("/v1/deployments/{deployment_id}/live-consent")
    async def live_consent(
        deployment_id: str,
        body: schemas.LiveConsentBody,
        request: fastapi.Request,
        principal: identity.Principal = _Principal,
    ) -> dict[str, object]:
        """Grants AUTO_LIVE consent (fails closed in this build).

        Args:
            deployment_id: Deployment.
            body: Consent request.
            request: Request.
            principal: Caller.

        Returns:
            Contract ``Deployment``.
        """
        deployment = await auth.api(request).deployments.grant_live_consent(
            principal,
            deployment_id,
            body.step_up_code,
            body.acknowledge_risk,
            body.duration_ms,
        )
        return deployment.to_contract()

    @router.post(
        "/internal/v1/signals",
        status_code=202,
        dependencies=[fastapi.Depends(auth.service)],
    )
    async def ingest_signal(
        request: fastapi.Request,
    ) -> dict[str, object]:
        """Receives a strategy signal from the market engine.

        Args:
            request: Request (body is a contract ``Signal``).

        Returns:
            Per-deployment results.
        """
        body = await request.json()
        if not isinstance(body, dict):
            body = {}
        return await auth.api(request).intake.ingest(body)

    return router
