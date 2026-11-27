# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Persistent, revocable MCP credentials bound to one resource.

Persistent, revocable MCP credentials bound to one resource and user identity.

MCP bearer credentials are never forwarded to either engine. The server resolves
them to an existing configured user identity after validating audience, issuer,
expiry, revocation and the explicitly granted automation permissions.
"""

from __future__ import annotations

import secrets
import time
import urllib.parse
import uuid
from collections.abc import Sequence

import jwt
import pydantic
import sqlalchemy as sa
from mcp.server.auth import provider

from joseph.domain import errors
from joseph.domain import identity
from joseph.platform import config
from joseph.platform import redaction

METADATA = sa.MetaData()
"""Metadata for private signing material and revocable connection grants."""

KEYS = sa.Table(
    "mcp_resource_keys",
    METADATA,
    sa.Column("name", sa.String(64), primary_key=True),
    sa.Column("value", sa.Text, nullable=False),
)
(
    "Private persisted resource signing material; never "
    "exposed as an MCP resource."
)

CONNECTIONS = sa.Table(
    "mcp_connections",
    METADATA,
    sa.Column("connection_id", sa.String(36), primary_key=True),
    sa.Column("tenant_id", sa.String(128), nullable=False),
    sa.Column("user_id", sa.String(128), nullable=False),
    sa.Column("name", sa.String(120), nullable=False),
    sa.Column("scopes", sa.String(64), nullable=False),
    sa.Column("expires_at", sa.BigInteger, nullable=False),
    sa.Column("revoked", sa.Boolean, nullable=False, default=False),
)
"""Per-user automation grants; only hashes/identifiers, never bearer strings."""


class ConnectionInput(pydantic.BaseModel):
    """An explicit user-created MCP automation permission grant.

    Attributes:
        name: Human-readable connection name, maximum one hundred twenty chars.
        write: Allow changes to private user data and notification destinations.
        trade: Allow trading operations in addition to all existing broker
            gates.
        lifetime_s: Lifetime in seconds, one minute through thirty days.
    """

    model_config = pydantic.ConfigDict(extra="forbid", strict=True)
    name: str = pydantic.Field(min_length=1, max_length=120)
    write: bool = False
    trade: bool = False
    lifetime_s: int = pydantic.Field(default=86400, ge=60, le=2592000)


class ConnectionGrant(pydantic.BaseModel):
    """A newly issued connection credential shown once.

    A newly issued connection credential shown once to its authenticated owner.

    Attributes:
        connection_id: Opaque revocation identifier.
        url: Public MCP endpoint URL to configure in the client.
        token: Secret resource-bound bearer credential, returned only at
            creation.
        scopes: Explicit read/write/trade permissions.
        expires_at: Expiration as UTC epoch seconds.
    """

    connection_id: str
    url: str
    token: str = pydantic.Field(repr=False)
    scopes: list[str]
    expires_at: int


class Authority:
    """Issues and verifies resource-scoped grants.

    Issues and verifies resource-scoped grants without service-token privilege.

    Attributes:
        engine: Private application database for signing keys and revocation.
        resource: Canonical public MCP URL used as both token audience and
            issuer.
    """

    def __init__(
        self,
        engine: sa.Engine,
        tokens: Sequence[config.DevToken],
        resource: str,
        signing_key: str | None = None,
    ) -> None:
        """Loads persistent signing material.

        Loads persistent signing material and configured upstream user
        identities.

        Args:
            engine: Application database, owned by the market container.
            tokens: Existing user tokens; service tokens are never included.
            resource: Canonical HTTP(S) MCP endpoint audience.
            signing_key: Optional operator-managed secret of at least 32 bytes;
                otherwise generated and stored privately in the database.

        Raises:
            ValueError: If the URL or explicit signing key is invalid.
        """
        resource = resource.strip()
        if any(char.isspace() for char in resource):
            raise ValueError("MCP public URL cannot contain whitespace")
        parsed = urllib.parse.urlsplit(resource)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "MCP public URL must be a canonical HTTP(S) endpoint"
            )
        if signing_key is not None and len(signing_key.encode()) < 32:
            raise ValueError("MCP signing key must contain at least 32 bytes")
        self.engine = engine
        self.resource = resource.rstrip("/")
        self._tokens = tuple(tokens)
        METADATA.create_all(engine)
        with engine.begin() as connection:
            stored = connection.execute(
                sa.select(KEYS.c.value).where(KEYS.c.name == "resource-signing")
            ).scalar_one_or_none()
            if signing_key:
                self._key = signing_key
            elif stored:
                self._key = str(stored)
            else:
                self._key = secrets.token_urlsafe(48)
                connection.execute(
                    KEYS.insert().values(
                        name="resource-signing", value=self._key
                    )
                )
        redaction.register_secret(self._key)

    def issue(
        self, principal: identity.Principal, request: ConnectionInput
    ) -> ConnectionGrant:
        """Creates a revocable, audience-bound grant after explicit user setup.

        Args:
            principal: Authenticated owner using the ordinary application token.
            request: Explicit write/trade choices and bounded credential
                lifetime.

        Returns:
            One-time secret credential and nonsecret connection metadata.

        Raises:
            errors.ForbiddenError: If a scoped reader requests mutation
                authority.
            errors.ValidationFailedError: If trading is enabled without writes.
        """
        if request.trade and not request.write:
            raise errors.ValidationFailedError(
                "trading permission requires write permission"
            )
        if (
            request.write
            and principal.scopes
            and not principal.has_scope("strategy:write")
        ):
            raise errors.ForbiddenError(
                "the current user cannot grant write permission"
            )
        connection_id = str(uuid.uuid4())
        scopes = ["read"]
        if request.write:
            scopes.append("write")
        if request.trade:
            scopes.append("trade")
        now = int(time.time())
        expires = now + request.lifetime_s
        with self.engine.begin() as connection:
            connection.execute(
                CONNECTIONS.insert().values(
                    connection_id=connection_id,
                    tenant_id=principal.tenant_id,
                    user_id=principal.user_id,
                    name=request.name,
                    scopes=" ".join(scopes),
                    expires_at=expires,
                    revoked=False,
                )
            )
        token = jwt.encode(
            {
                "iss": self.resource,
                "aud": self.resource,
                "sub": principal.user_id,
                "tenant_id": principal.tenant_id,
                "jti": connection_id,
                "scope": scopes,
                "iat": now,
                "exp": expires,
            },
            self._key,
            algorithm="HS256",
        )
        redaction.register_secret(token)
        return ConnectionGrant(
            connection_id=connection_id,
            url=self.resource,
            token=token,
            scopes=scopes,
            expires_at=expires,
        )

    def list(self, principal: identity.Principal) -> list[dict[str, object]]:
        """Lists owned connection metadata.

        Lists owned connection metadata without returning any bearer
        credentials.

        Args:
            principal: Authenticated owner; both tenant and user must match.

        Returns:
            Owned connection dictionaries with string identifiers and names,
            scopes as a list of permission strings, UTC-second expiry and a
            boolean revocation flag. No credential is returned.
        """
        with self.engine.connect() as connection:
            rows = connection.execute(
                sa.select(CONNECTIONS).where(
                    CONNECTIONS.c.tenant_id == principal.tenant_id,
                    CONNECTIONS.c.user_id == principal.user_id,
                )
            ).mappings()
            return [
                {**dict(row), "scopes": str(row["scopes"]).split()}
                for row in rows
            ]

    def revoke(self, principal: identity.Principal, connection_id: str) -> None:
        """Revokes an owned MCP connection for all subsequent requests.

        Args:
            principal: Authenticated owner of the grant.
            connection_id: Opaque connection identifier.

        Returns:
            None after setting the owned grant's revocation flag.

        Raises:
            errors.NotFoundError: If absent or not owned by this tenant/user.
        """
        with self.engine.begin() as connection:
            result = connection.execute(
                CONNECTIONS.update()
                .where(
                    CONNECTIONS.c.connection_id == connection_id,
                    CONNECTIONS.c.tenant_id == principal.tenant_id,
                    CONNECTIONS.c.user_id == principal.user_id,
                )
                .values(revoked=True)
            )
        if not result.rowcount:
            raise errors.NotFoundError("MCP connection not found")

    def resolve(self, claims: dict[str, object]) -> str:
        """Resolves verified resource claims.

        Resolves verified resource claims to an existing upstream user
        credential.

        Args:
            claims: Verified JWT claims, never untrusted tool arguments.

        Returns:
            User credential accepted by the existing backend authentication
            layer.

        Raises:
            errors.UnauthenticatedError: If the original user was removed.
        """
        for entry in self._tokens:
            if entry.tenant_id == claims.get(
                "tenant_id"
            ) and entry.user_id == claims.get("sub"):
                return entry.token
        raise errors.UnauthenticatedError(
            "MCP connection owner is no longer configured"
        )

    async def verify_token(self, token: str) -> provider.AccessToken | None:
        """Verifies signature, audience, expiry, revocation.

        Verifies signature, audience, expiry, revocation and current user
        mapping.

        Args:
            token: Raw resource-specific bearer token received by the MCP SDK.

        Returns:
            Validated access identity, or None for any invalid/revoked
            credential.
        """
        try:
            claims = jwt.decode(
                token,
                self._key,
                algorithms=["HS256"],
                audience=self.resource,
                issuer=self.resource,
                options={"require": ["exp", "iat", "jti", "sub", "aud", "iss"]},
            )
            with self.engine.connect() as connection:
                row = (
                    connection.execute(
                        sa.select(CONNECTIONS).where(
                            CONNECTIONS.c.connection_id == claims["jti"],
                            CONNECTIONS.c.tenant_id == claims.get("tenant_id"),
                            CONNECTIONS.c.user_id == claims["sub"],
                            CONNECTIONS.c.revoked.is_(False),
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
            if (
                row is None
                or row["expires_at"] <= int(time.time())
                or " ".join(claims.get("scope", [])) != row["scopes"]
            ):
                return None
            self.resolve(claims)
            return provider.AccessToken(
                token=token,
                client_id=str(claims["jti"]),
                scopes=list(claims["scope"]),
                expires_at=int(claims["exp"]),
                resource=self.resource,
                subject=str(claims["sub"]),
                claims=claims,
            )
        except (
            jwt.PyJWTError,
            errors.UnauthenticatedError,
            ValueError,
            TypeError,
            KeyError,
        ):
            return None
