"""Bearer-token authentication (framework independent).

Development deployments map static tokens to principals. Production replaces
``TokenAuthenticator`` with an OIDC verifier behind the same interface.
Token comparison is constant time.
"""

from __future__ import annotations

import hmac
from collections.abc import Iterable

from joseph.domain import errors
from joseph.domain import identity
from joseph.platform import config


class TokenAuthenticator:
    """Resolves bearer tokens to principals."""

    def __init__(self, tokens: Iterable[config.DevToken]) -> None:
        """Initializes the authenticator.

        Args:
            tokens: Accepted development tokens.
        """
        self._tokens = tuple(tokens)

    @property
    def enabled(self) -> bool:
        """Whether any token is configured."""
        return bool(self._tokens)

    def authenticate(self, token: str | None) -> identity.Principal:
        """Authenticates a bearer token.

        Args:
            token: Raw token text (without the ``Bearer`` prefix).

        Returns:
            The authenticated principal.

        Raises:
            errors.UnauthenticatedError: If the token is missing or unknown.
        """
        if not token:
            raise errors.UnauthenticatedError("Sign in to use this endpoint.")
        candidate = token.encode("utf-8")
        for entry in self._tokens:
            if hmac.compare_digest(candidate, entry.token.encode("utf-8")):
                return entry.principal()
        raise errors.UnauthenticatedError(
            "The session token is invalid or expired."
        )

    def authenticate_header(self, header: str | None) -> identity.Principal:
        """Authenticates an ``Authorization`` header value.

        Args:
            header: Header value such as ``"Bearer abc"``.

        Returns:
            The authenticated principal.

        Raises:
            errors.UnauthenticatedError: If the header is missing/malformed.
        """
        if not header:
            raise errors.UnauthenticatedError("Sign in to use this endpoint.")
        scheme, _, value = header.partition(" ")
        if scheme.lower() != "bearer" or not value.strip():
            raise errors.UnauthenticatedError(
                "Use an Authorization: Bearer <token> header."
            )
        return self.authenticate(value.strip())
