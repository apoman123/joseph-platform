# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Request-scoped dependencies: trace ids and authenticated principals."""

from __future__ import annotations

import fastapi

from joseph.domain import errors
from joseph.domain import identity
from joseph.platform import auth


def get_authenticator(request: fastapi.Request) -> auth.TokenAuthenticator:
    """Returns the app's authenticator.

    Args:
        request: Current request.

    Returns:
        Authenticator stored on ``app.state``.

    Raises:
        errors.UnauthenticatedError: If no authenticator is installed on
            ``app.state`` (rendered as a 401 ``ErrorResponse``).
    """
    authenticator = getattr(request.app.state, "authenticator", None)
    if not isinstance(authenticator, auth.TokenAuthenticator):
        raise errors.UnauthenticatedError("Authentication is not configured.")
    return authenticator


def get_principal(request: fastapi.Request) -> identity.Principal:
    """Authenticates the ``Authorization: Bearer`` header (FastAPI dependency).

    Usage: ``principal = fastapi.Depends(deps.get_principal)``.

    Args:
        request: Current request.

    Returns:
        Authenticated principal.

    Raises:
        errors.UnauthenticatedError: On a missing or invalid token
            (rendered as a 401 ``ErrorResponse``).
    """
    return get_authenticator(request).authenticate_header(
        request.headers.get("authorization")
    )
