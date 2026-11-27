# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Mounts authenticated MCP alongside the market API.

Mounts authenticated MCP alongside the market API with a shared lifespan.
"""

import importlib.resources
import json
import urllib.parse
from collections.abc import Callable
from typing import Annotated

import fastapi
import httpx
import pydantic
import sqlalchemy as sa
from mcp.server import transport_security
from mcp.server.auth import settings as auth_settings
from starlette import requests
from starlette import responses
from starlette import routing

from joseph.domain import identity
from joseph.mcp import auth
from joseph.mcp import catalog
from joseph.mcp import gateway
from joseph.mcp import protocol
from joseph.platform import config


def connection_router(
    authority: auth.Authority, get_principal: Callable[..., identity.Principal]
) -> fastapi.APIRouter:
    """Builds user-controlled MCP credential setup.

    Args:
        authority: Resource-scoped issuer and revocation store.
        get_principal: Existing application bearer-token authentication.

    Returns:
        Routes for explicitly creating, listing and revoking connection grants.
    """
    router = fastapi.APIRouter(
        prefix="/v1/mcp/connections", tags=["mcp-connections"]
    )
    principal_dep = Annotated[
        identity.Principal, fastapi.Depends(get_principal)
    ]

    @router.get("")
    def list_connections(principal: principal_dep) -> dict[str, object]:
        """Lists owned connection metadata without returning existing secrets.

        Args:
            principal: Authenticated application user.

        Returns:
            Owned grants and the public MCP endpoint URL.
        """
        return {
            "connections": authority.list(principal),
            "url": authority.resource,
        }

    @router.post("", status_code=201)
    def create_connection(
        body: auth.ConnectionInput, principal: principal_dep
    ) -> auth.ConnectionGrant:
        """Issues a resource-bound MCP credential.

        Issues a resource-bound MCP credential with explicitly selected powers.

        Args:
            body: Connection name, lifetime and write/trade permission choices.
            principal: Authenticated application user creating this grant.

        Returns:
            Secret token shown once, endpoint URL, scopes and expiration.

        Raises:
            errors.ForbiddenError: If the owner cannot grant mutation access.
            errors.ValidationFailedError: If trading is granted without writes.
        """
        return authority.issue(principal, body)

    @router.delete("/{connection_id}", status_code=204)
    def revoke_connection(
        connection_id: str, principal: principal_dep
    ) -> fastapi.Response:
        """Revokes an owned automation credential on subsequent MCP requests.

        Args:
            connection_id: Opaque grant identifier.
            principal: Authenticated owner using the application token.

        Returns:
            Empty HTTP 204 response after revocation.

        Raises:
            errors.NotFoundError: If absent or owned by a different user/tenant.
        """
        authority.revoke(principal, connection_id)
        return fastapi.Response(status_code=204)

    return router


def install(
    app: fastapi.FastAPI,
    *,
    engine: sa.Engine,
    settings: config.Settings,
    get_principal: Callable[..., identity.Principal],
    closers: list[Callable[[], object]],
) -> None:
    """Starts MCP alongside the application backend.

    Args:
        app: Fully configured market application, before serving requests.
        engine: Existing private application database.
        settings: Resource URL, signing configuration and upstream user
            identities.
        get_principal: Existing application bearer authentication dependency.
        closers: Container-owned hooks for HTTP client cleanup at shutdown.

    Returns:
        None after registering transport routes and a lifespan context factory.
    """
    authority = auth.Authority(
        engine,
        settings.dev_tokens,
        settings.mcp_public_url,
        settings.mcp_signing_key,
    )
    app.include_router(connection_router(authority, get_principal))
    # Refresh OpenAPI after every user route has been installed.
    app.openapi_schema = None
    market_document = app.openapi()
    execution_document = json.loads(
        importlib.resources.files("joseph.mcp")
        .joinpath("contracts/execution.json")
        .read_text()
    )
    actions = catalog.operations(
        "market", market_document
    ) + catalog.operations("execution", execution_document)
    market = httpx.AsyncClient(
        base_url="http://market", transport=httpx.ASGITransport(app=app)
    )
    execution = httpx.AsyncClient(base_url=settings.execution_url)
    closers.extend([market.aclose, execution.aclose])
    server = protocol.build_server(
        gateway.Gateway(actions, market, execution), authority
    )
    public = urllib.parse.urlsplit(settings.mcp_public_url)
    hosts = ["localhost:*", "127.0.0.1:*", "[::1]:*", public.netloc]
    if settings.demo_only:
        hosts.append("testserver")
    origins = [*settings.cors_origins, f"{public.scheme}://{public.netloc}"]
    security = transport_security.TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=hosts,
        allowed_origins=origins,
    )
    application = server.streamable_http_app(
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
        transport_security=security,
        auth=auth_settings.AuthSettings(
            issuer_url=pydantic.AnyHttpUrl(authority.resource),
            resource_server_url=pydantic.AnyHttpUrl(authority.resource),
            required_scopes=["read"],
            validate_token_resource=True,
        ),
        token_verifier=authority,
    )

    # Delegate at each declared path, preserving the SDK's auth middleware while
    # avoiding mount-prefix redirects and leaving unrelated API 404s untouched.
    async def protected_metadata(
        request: requests.Request,
    ) -> responses.JSONResponse:
        """Describes the manual bearer authentication flow.

        Args:
            request: Public metadata request; no credentials or user data are
                read.

        Returns:
            RFC 9728 resource metadata with header-based bearer access.
        """
        del request
        return responses.JSONResponse(
            {
                "resource": authority.resource,
                "scopes_supported": ["read", "write", "trade"],
                "bearer_methods_supported": ["header"],
                "resource_name": "Joseph MCP (manual bearer grants)",
            }
        )

    for route in application.routes:
        if isinstance(route, routing.Route):
            if route.path.startswith("/.well-known/oauth-protected-resource"):
                app.router.routes.append(
                    routing.Route(
                        route.path, endpoint=protected_metadata, methods=["GET"]
                    )
                )
                continue
            app.router.routes.append(
                routing.Route(
                    route.path, endpoint=application, methods=route.methods
                )
            )
    app.state.mcp_lifespan = server.session_manager.run
    app.state.mcp_authority = authority
    app.state.mcp_server = server
