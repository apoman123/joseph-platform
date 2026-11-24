# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Risk endpoints: kill switch, limits and audit."""

from __future__ import annotations

import fastapi

from joseph_execution.api import auth
from joseph_execution.api import schemas
from joseph_execution.audit import service as audit_service
from joseph_execution.domain import enums
from joseph_execution.domain import identity
from joseph_execution.risk import limits

# Shared parameter default; a module constant keeps calls out of argument
# defaults (ruff B008).
_Principal = fastapi.Depends(auth.principal)


def build_router() -> fastapi.APIRouter:
    """Builds the risk router.

    Returns:
        Router.
    """
    router = fastapi.APIRouter(prefix="/v1", tags=["risk"])

    @router.get("/risk/kill-switch")
    async def list_switches(
        request: fastapi.Request, principal: identity.Principal = _Principal
    ) -> dict[str, object]:
        """Lists kill switches.

        Args:
            request: Request.
            principal: Caller.

        Returns:
            Contract ``KillSwitchList``.
        """
        items = auth.api(request).kill_switches.list(principal)
        return {"switches": [s.to_contract() for s in items]}

    @router.post("/risk/kill-switch")
    async def set_switch(
        body: schemas.KillSwitchBody,
        request: fastapi.Request,
        principal: identity.Principal = _Principal,
    ) -> dict[str, object]:
        """Engages or releases a kill switch.

        Args:
            body: Request.
            request: Request.
            principal: Caller.

        Returns:
            Contract ``KillSwitchState``.
        """
        switch = await auth.api(request).kill_switches.set(
            principal,
            enums.KillSwitchScope(body.scope),
            body.target_id,
            body.engage,
            body.cancel_open_orders,
            body.flatten_positions,
            body.reason,
        )
        return switch.to_contract()

    @router.get("/risk/limits")
    async def get_limits(
        account_id: str,
        request: fastapi.Request,
        principal: identity.Principal = _Principal,
    ) -> dict[str, object]:
        """Returns effective limits and platform hard caps.

        Args:
            account_id: Account.
            request: Request.
            principal: Caller.

        Returns:
            Limits document.
        """
        svc = auth.api(request)
        account = svc.accounts.get(principal, account_id)
        factor = svc.settings.securities.twd_limit_factor
        with svc.uow_factory() as uow:
            stored = uow.risk_limits.get(principal.tenant_id, account_id)
        effective = limits.from_mapping(stored, account.currency, factor)
        caps = limits.hard_limits(account.currency, factor)
        return {
            "account_id": account_id,
            "limits": effective.to_dict(),
            "hard_limits": caps.to_dict(),
            "policy_version": limits.POLICY_VERSION,
        }

    @router.put("/risk/limits")
    async def put_limits(
        body: schemas.LimitsBody,
        request: fastapi.Request,
        principal: identity.Principal = _Principal,
    ) -> dict[str, object]:
        """Stores user limits (clamped to platform hard caps).

        Args:
            body: Limits.
            request: Request.
            principal: Caller.

        Returns:
            Limits document.
        """
        svc = auth.api(request)
        account = svc.accounts.get(principal, body.account_id)
        factor = svc.settings.securities.twd_limit_factor
        effective = limits.from_mapping(body.limits, account.currency, factor)
        with svc.uow_factory() as uow:
            uow.risk_limits.put(
                principal.tenant_id,
                body.account_id,
                effective.to_dict(),
                svc.clock.now_ms(),
            )
            svc.audit.record(
                uow,
                tenant_id=principal.tenant_id,
                actor=principal.actor,
                action="risk.limits_changed",
                entity_type="account",
                entity_id=body.account_id,
                account_id=body.account_id,
                payload=effective.to_dict(),
            )
        return {
            "account_id": body.account_id,
            "limits": effective.to_dict(),
            "hard_limits": limits.hard_limits(
                account.currency, factor
            ).to_dict(),
            "policy_version": limits.POLICY_VERSION,
        }

    @router.get("/audit/events")
    async def audit_events(
        request: fastapi.Request,
        principal: identity.Principal = _Principal,
        account_id: str | None = None,
        limit: int = fastapi.Query(default=200, ge=1, le=1000),
    ) -> dict[str, object]:
        """Lists audit events and verifies the tenant chain.

        Args:
            request: Request.
            principal: Caller.
            account_id: Account filter.
            limit: Maximum rows.

        Returns:
            Contract ``AuditEventList``.
        """
        svc = auth.api(request)
        with svc.uow_factory() as uow:
            events = uow.audit.list(principal.tenant_id, account_id, limit)
            valid = audit_service.AuditLog.verify(uow, principal.tenant_id)
        return {
            "events": [e.to_contract() for e in events],
            "chain_valid": valid,
        }

    return router
