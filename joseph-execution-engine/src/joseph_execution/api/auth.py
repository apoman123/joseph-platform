# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Bearer-token and service-token authentication.

User bearer tokens are looked up in the configured token table
(``Settings.dev_tokens``, from ``JOSEPH_DEV_TOKENS``); internal callers
present ``Settings.service_token`` in the ``X-Joseph-Service-Token`` header.
Tokens are compared with ``hmac.compare_digest``.
"""

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
        The principal configured for the token, or ``None`` for a missing
        or unknown token.
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
        The stripped token, or ``None`` when the header is missing or its
        scheme is not ``Bearer`` (any case).
    """
    if not header:
        return None
    scheme, _, token = header.partition(" ")
    return token.strip() if scheme.lower() == "bearer" else None


def principal(request: fastapi.Request) -> identity.Principal:
    """FastAPI dependency authenticating the caller.

    Also provisions the tenant's paper account(s) when they do not exist
    yet, so every authenticated tenant has one.

    Args:
        request: Request.

    Returns:
        Principal.

    Raises:
        ExecutionError: ``UNAUTHENTICATED`` (HTTP 401) without a valid
            ``Authorization: Bearer`` token.
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
        ExecutionError: ``UNAUTHENTICATED`` (HTTP 401) when the header does
            not match, or always when no service token is configured.
    """
    svc = api(request)
    expected = svc.settings.service_token
    provided = request.headers.get("x-joseph-service-token", "")
    if not expected or not hmac.compare_digest(
        expected.encode(), provided.encode()
    ):
        raise errors.ExecutionError("UNAUTHENTICATED", "service token required")
