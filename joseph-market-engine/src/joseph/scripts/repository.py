# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Tenant-isolated immutable script versions with optimistic edit locking."""

from __future__ import annotations

import hashlib
import json
import time

import sqlalchemy as sa

from joseph.domain import errors
from joseph.scripts import models

METADATA = sa.MetaData()
"""SQL metadata for user-owned script versions."""

VERSIONS = sa.Table(
    "python_script_versions",
    METADATA,
    sa.Column("tenant_id", sa.String(128), primary_key=True),
    sa.Column("script_id", sa.String(64), primary_key=True),
    sa.Column("version", sa.Integer, primary_key=True),
    sa.Column("body_json", sa.Text, nullable=False),
)
"""Immutable script versions; all lookups include the authenticated tenant."""

HEADS = sa.Table(
    "python_script_heads",
    METADATA,
    sa.Column("tenant_id", sa.String(128), primary_key=True),
    sa.Column("script_id", sa.String(64), primary_key=True),
    sa.Column("version", sa.Integer, nullable=False),
)
("Visible latest versions; deleting a head archives a script, retaining pins.")


class ScriptRepository:
    """Stores script source privately and never resolves cross-tenant imports.

    Attributes:
        engine: SQLAlchemy engine whose transaction owns version/head updates.
    """

    def __init__(self, engine: sa.Engine) -> None:
        """Creates missing script tables on the application's existing database.

        Args:
            engine: SQLAlchemy engine owned by the market application.
        """
        self.engine = engine
        METADATA.create_all(engine)

    def get(
        self, tenant_id: str, script_id: str, version: int | None = None
    ) -> models.ScriptVersion:
        """Reads an owned version or the latest visible version.

        Args:
            tenant_id: Authenticated owning tenant.
            script_id: Tenant-local script slug.
            version: Optional pinned positive version; archived pins remain
                valid.

        Returns:
            Immutable source, dependency pins and provenance metadata.

        Raises:
            errors.NotFoundError: If the owned script/version does not exist.
        """
        with self.engine.connect() as connection:
            if version is None:
                version = connection.execute(
                    sa.select(HEADS.c.version).where(
                        HEADS.c.tenant_id == tenant_id,
                        HEADS.c.script_id == script_id,
                    )
                ).scalar_one_or_none()
            body = connection.execute(
                sa.select(VERSIONS.c.body_json).where(
                    VERSIONS.c.tenant_id == tenant_id,
                    VERSIONS.c.script_id == script_id,
                    VERSIONS.c.version == version,
                )
            ).scalar_one_or_none()
        if body is None:
            raise errors.NotFoundError("script version not found")
        return models.ScriptVersion.model_validate_json(body)

    def list(
        self,
        tenant_id: str,
        *,
        script_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[models.ScriptVersion]:
        """Lists latest visible scripts or all versions of one owned script.

        Args:
            tenant_id: Authenticated owning tenant.
            script_id: Optional slug selecting immutable version history.
            limit: Maximum returned sources, one through one hundred.
            offset: Nonnegative source offset for stable catalog pagination.

        Returns:
            Versions sorted by script slug and descending version.

        Raises:
            errors.ValidationFailedError: If pagination bounds are invalid.
        """
        if not 1 <= limit <= 100 or offset < 0:
            raise errors.ValidationFailedError("invalid script catalog window")
        query = sa.select(VERSIONS.c.body_json).where(
            VERSIONS.c.tenant_id == tenant_id
        )
        if script_id is None:
            query = query.join(
                HEADS,
                sa.and_(
                    VERSIONS.c.tenant_id == HEADS.c.tenant_id,
                    VERSIONS.c.script_id == HEADS.c.script_id,
                    VERSIONS.c.version == HEADS.c.version,
                ),
            )
        else:
            query = query.where(VERSIONS.c.script_id == script_id)
        query = query.order_by(VERSIONS.c.script_id, VERSIONS.c.version.desc())
        query = query.limit(limit).offset(offset)
        with self.engine.connect() as connection:
            return [
                models.ScriptVersion.model_validate_json(row[0])
                for row in connection.execute(query)
            ]

    def save(
        self, tenant_id: str, script: models.ScriptInput, *, create: bool
    ) -> models.ScriptVersion:
        """Atomically creates a script or appends an optimistic-lock version.

        Args:
            tenant_id: Authenticated owning tenant.
            script: Validated source and pinned dependency configuration.
            create: True for initial creation, False to append a version.

        Returns:
            Newly persisted immutable script version.

        Raises:
            errors.ConflictError: If already created or the edit lock is stale.
            errors.NotFoundError: If appending to an absent/archived script.
            errors.ValidationFailedError: If changing a script's declared kind.
        """
        prior = None if create else self.get(tenant_id, script.script_id)
        if prior and prior.kind != script.kind:
            raise errors.ValidationFailedError(
                "a script's kind cannot change; create a new script"
            )
        if prior and script.expected_version != prior.version:
            raise errors.ConflictError(
                "script changed; reload its latest version before saving"
            )
        version = 1 if prior is None else prior.version + 1
        content = script.model_dump(exclude={"expected_version"})
        digest = hashlib.sha256(
            json.dumps(content, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        row = models.ScriptVersion(
            **content,
            version=version,
            source_hash="sha256:" + digest,
            created_at_ms=int(time.time() * 1000),
        )
        try:
            with self.engine.begin() as connection:
                if create:
                    connection.execute(
                        HEADS.insert().values(
                            tenant_id=tenant_id,
                            script_id=script.script_id,
                            version=version,
                        )
                    )
                else:
                    updated = connection.execute(
                        HEADS.update()
                        .where(
                            HEADS.c.tenant_id == tenant_id,
                            HEADS.c.script_id == script.script_id,
                            HEADS.c.version == script.expected_version,
                        )
                        .values(version=version)
                    )
                    if updated.rowcount != 1:
                        raise errors.ConflictError(
                            "script changed; reload before saving"
                        )
                connection.execute(
                    VERSIONS.insert().values(
                        tenant_id=tenant_id,
                        script_id=script.script_id,
                        version=version,
                        body_json=row.model_dump_json(),
                    )
                )
        except sa.exc.IntegrityError:
            raise errors.ConflictError(
                "script or version already exists"
            ) from None
        return row

    def archive(self, tenant_id: str, script_id: str) -> None:
        """Hides an owned script while retaining versions imported by others.

        Args:
            tenant_id: Authenticated owning tenant.
            script_id: Tenant-local script slug.

        Returns:
            None after deleting its visible head, preserving immutable pins.

        Raises:
            errors.NotFoundError: If the owned visible script does not exist.
        """
        with self.engine.begin() as connection:
            result = connection.execute(
                HEADS.delete().where(
                    HEADS.c.tenant_id == tenant_id,
                    HEADS.c.script_id == script_id,
                )
            )
        if not result.rowcount:
            raise errors.NotFoundError("script not found")
