"""Unit tests for request dependencies and error mapping."""

from __future__ import annotations

import fastapi
from fastapi import testclient

from joseph.api import deps
from joseph.api import errors as api_errors
from joseph.domain import identity
from joseph.platform import auth
from joseph.platform import config

_PRINCIPAL = fastapi.Depends(deps.get_principal)


def _app(with_auth: bool = True) -> fastapi.FastAPI:
    """Builds a tiny app protected by ``deps.get_principal``.

    Args:
        with_auth: Whether an authenticator is configured.

    Returns:
        App.
    """
    app = fastapi.FastAPI()
    if with_auth:
        app.state.authenticator = auth.TokenAuthenticator(
            config.parse_dev_tokens("tok:tenant-01:user-01")
        )
    api_errors.install(app)

    @app.get("/me")
    def me(
        principal: identity.Principal = _PRINCIPAL,
    ) -> dict[str, str]:
        """Echoes the principal.

        Args:
            principal: Authenticated principal.

        Returns:
            Tenant and user.
        """
        return {"tenant": principal.tenant_id, "user": principal.user_id}

    @app.get("/boom")
    def boom() -> None:
        """Raises an unexpected error.

        Raises:
            RuntimeError: Always.
        """
        raise RuntimeError("secret internals")

    return app


def test_get_principal_authenticates_bearer_tokens() -> None:
    """Valid tokens resolve; missing or bad ones are 401 ErrorResponses."""
    client = testclient.TestClient(_app())
    ok = client.get("/me", headers={"Authorization": "Bearer tok"})
    assert ok.json() == {"tenant": "tenant-01", "user": "user-01"}
    for headers in ({}, {"Authorization": "Bearer wrong"}):
        response = client.get("/me", headers=headers)
        assert response.status_code == 401
        assert response.json()["code"] == "UNAUTHENTICATED"


def test_missing_authenticator_is_unauthenticated() -> None:
    """Without configured auth nobody is let in."""
    client = testclient.TestClient(_app(with_auth=False))
    assert (
        client.get("/me", headers={"Authorization": "Bearer tok"}).status_code
        == 401
    )


def test_internal_errors_hide_details() -> None:
    """Unexpected exceptions return INTERNAL without internals."""
    client = testclient.TestClient(_app(), raise_server_exceptions=False)
    response = client.get("/boom")
    assert response.status_code == 500
    assert response.json()["code"] == "INTERNAL"
    assert "secret internals" not in response.text


def test_status_mapping_covers_contract_codes() -> None:
    """Every mapped domain code has an HTTP status."""
    assert api_errors.STATUS_BY_CODE["RATE_LIMITED"] == 429
    assert api_errors.STATUS_BY_CODE["MARKET_STALE"] == 503
