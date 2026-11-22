"""Bearer-token and service-token authentication."""

from __future__ import annotations

import hmac

import fastapi

from joseph_execution.api import services
from joseph_execution.domain import errors
from joseph_execution.domain import identity


def api(request: fastapi.Request) -> services.ApiServices:
    """Returns the API services of the application.

    Args:
        request: Request.

    Returns:
        Services.
    """
    result: services.ApiServices = request.app.state.services
    return result


def resolve_token(
    svc: services.ApiServices, token: str | None
) -> identity.Principal | None:
    """Maps a bearer token to a principal.

    Args:
        svc: Services.
        token: Token or ``None``.

    Returns:
        Principal or ``None``.
    """
    if not token:
        return None
    for candidate, (tenant, user) in svc.settings.dev_tokens.items():
        if hmac.compare_digest(candidate.encode(), token.encode()):
            return identity.Principal(tenant_id=tenant, user_id=user)
    return None


def bearer(header: str | None) -> str | None:
    """Extracts a bearer token from an Authorization header.

    Args:
        header: Header value.

    Returns:
        Token or ``None``.
    """
    if not header:
        return None
    scheme, _, token = header.partition(" ")
    return token.strip() if scheme.lower() == "bearer" else None


def principal(request: fastapi.Request) -> identity.Principal:
    """FastAPI dependency authenticating the caller.

    Also provisions the tenant's paper account on first use.

    Args:
        request: Request.

    Returns:
        Principal.

    Raises:
        ExecutionError: ``UNAUTHENTICATED``.
    """
    svc = api(request)
    found = resolve_token(svc, bearer(request.headers.get("authorization")))
    if found is None:
        raise errors.ExecutionError(
            "UNAUTHENTICATED", "valid bearer token required"
        )
    svc.accounts.ensure_paper_account(found.tenant_id)
    return found


def service(request: fastapi.Request) -> None:
    """FastAPI dependency requiring the internal service token.

    Args:
        request: Request.

    Raises:
        ExecutionError: ``UNAUTHENTICATED``.
    """
    svc = api(request)
    expected = svc.settings.service_token
    provided = request.headers.get("x-joseph-service-token", "")
    if not expected or not hmac.compare_digest(
        expected.encode(), provided.encode()
    ):
        raise errors.ExecutionError("UNAUTHENTICATED", "service token required")
