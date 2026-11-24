# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Account endpoints."""

from __future__ import annotations

import fastapi
from fastapi import responses

from joseph_execution.api import auth
from joseph_execution.api import schemas
from joseph_execution.domain import identity

_Principal = fastapi.Depends(auth.principal)


def build_router() -> fastapi.APIRouter:
    """Builds the accounts router.

    Returns:
        Router.
    """
    router = fastapi.APIRouter(prefix="/v1/accounts", tags=["accounts"])

    @router.get("")
    async def list_accounts(
        request: fastapi.Request, principal: identity.Principal = _Principal
    ) -> dict[str, object]:
        """Lists accounts.

        Args:
            request: Request.
            principal: Caller.

        Returns:
            Contract ``AccountList``.
        """
        return {"accounts": await auth.api(request).accounts.list(principal)}

    @router.post("/connect/start", status_code=201)
    async def connect(
        body: schemas.ConnectBody,
        request: fastapi.Request,
        principal: identity.Principal = _Principal,
    ) -> responses.JSONResponse:
        """Stores encrypted credentials for a venue account.

        Args:
            body: Connection request.
            request: Request.
            principal: Caller.

        Returns:
            Contract ``AccountSummary``.
        """
        summary = await auth.api(request).accounts.connect_start(
            principal,
            body.venue,
            body.label,
            body.credentials,
            body.withdrawal_permission_disabled,
        )
        return responses.JSONResponse(summary, status_code=201)

    @router.post("/{account_id}/verify")
    async def verify(
        account_id: str,
        request: fastapi.Request,
        principal: identity.Principal = _Principal,
    ) -> dict[str, object]:
        """Verifies a connection.

        Args:
            account_id: Account.
            request: Request.
            principal: Caller.

        Returns:
            Contract ``AccountSummary``.
        """
        return await auth.api(request).accounts.verify(principal, account_id)

    @router.post("/{account_id}/revoke")
    async def revoke(
        account_id: str,
        request: fastapi.Request,
        principal: identity.Principal = _Principal,
    ) -> dict[str, object]:
        """Revokes credentials.

        Args:
            account_id: Account.
            request: Request.
            principal: Caller.

        Returns:
            Contract ``AccountSummary``.
        """
        return await auth.api(request).accounts.revoke(principal, account_id)

    @router.post("/{account_id}/live-consent")
    async def grant_live_consent(
        account_id: str,
        body: schemas.AccountLiveConsentBody,
        request: fastapi.Request,
        principal: identity.Principal = _Principal,
    ) -> dict[str, object]:
        """Arms manual LIVE orders on an account (step-up required).

        Args:
            account_id: Verified venue or broker account.
            body: Contract ``AccountLiveConsentRequest``.
            request: Request.
            principal: Caller.

        Returns:
            Contract ``AccountSummary`` with ``live_armed_until_ms``.
        """
        return await auth.api(request).accounts.grant_live_consent(
            principal,
            account_id,
            body.step_up_code,
            body.acknowledge_risk,
            body.duration_ms,
        )

    @router.delete("/{account_id}/live-consent")
    async def revoke_live_consent(
        account_id: str,
        request: fastapi.Request,
        principal: identity.Principal = _Principal,
    ) -> dict[str, object]:
        """Disarms manual LIVE orders on an account.

        Args:
            account_id: Account.
            request: Request.
            principal: Caller.

        Returns:
            Contract ``AccountSummary``.
        """
        return await auth.api(request).accounts.revoke_live_consent(
            principal, account_id
        )

    @router.get("/{account_id}/summary")
    async def summary(
        account_id: str,
        request: fastapi.Request,
        principal: identity.Principal = _Principal,
    ) -> dict[str, object]:
        """Returns an account summary.

        Args:
            account_id: Account.
            request: Request.
            principal: Caller.

        Returns:
            Contract ``AccountSummary``.
        """
        return await auth.api(request).accounts.summary(principal, account_id)

    @router.get("/{account_id}/capabilities")
    async def capabilities(
        account_id: str,
        request: fastapi.Request,
        principal: identity.Principal = _Principal,
    ) -> dict[str, object]:
        """Returns negotiated capabilities.

        Args:
            account_id: Account.
            request: Request.
            principal: Caller.

        Returns:
            Contract ``TradingCapabilities``.
        """
        svc = auth.api(request)
        account = svc.accounts.get(principal, account_id)
        return (await svc.accounts.capabilities(account)).to_contract()

    return router
