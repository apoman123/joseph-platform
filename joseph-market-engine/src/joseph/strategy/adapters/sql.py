"""SQLAlchemy Core persistence for strategy versions and checkpoints.

Definitions are stored as canonical JSON and are immutable: the repository
never updates them, and a database trigger rejects any attempt to rewrite
a version's definition or hash (SQLite and PostgreSQL variants).
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import sqlalchemy as sa

from joseph.domain import errors
from joseph.strategy.domain import ast
from joseph.strategy.domain import setup_fsm
from joseph.strategy.domain import versions

METADATA = sa.MetaData()
"""Schema of the strategy context."""

STRATEGY_VERSIONS = sa.Table(
    "strategy_versions",
    METADATA,
    sa.Column("tenant_id", sa.String(128), primary_key=True),
    sa.Column("strategy_id", sa.String(64), primary_key=True),
    sa.Column("version", sa.Integer, primary_key=True),
    sa.Column("author_user_id", sa.String(128), nullable=False),
    sa.Column("created_at_ms", sa.BigInteger, nullable=False),
    sa.Column("definition_json", sa.Text, nullable=False),
    sa.Column("definition_hash", sa.String(80), nullable=False),
    sa.Column("compiler_version", sa.String(32), nullable=False),
    sa.Column("evaluator_version", sa.String(32), nullable=False),
    sa.Column("state", sa.String(16), nullable=False),
    sa.Column("activation_json", sa.Text, nullable=True),
)

STRATEGY_INSTANCES = sa.Table(
    "strategy_instances",
    METADATA,
    sa.Column("instance_key", sa.String(512), primary_key=True),
    sa.Column("tenant_id", sa.String(128), nullable=False, index=True),
    sa.Column("strategy_version_id", sa.String(80), nullable=False),
    sa.Column("instrument_id", sa.String(96), nullable=False),
    sa.Column("direction", sa.String(8), nullable=False),
    sa.Column("state_json", sa.Text, nullable=False),
    sa.Column("updated_at_ms", sa.BigInteger, nullable=False),
)

sa.event.listen(
    STRATEGY_VERSIONS,
    "after_create",
    sa.DDL(  # type: ignore[no-untyped-call]
        "CREATE TRIGGER strategy_versions_immutable BEFORE UPDATE OF "
        "definition_json, definition_hash, tenant_id, strategy_id, version "
        "ON strategy_versions BEGIN SELECT RAISE(ABORT, "
        "'strategy versions are immutable'); END;"
    ).execute_if(dialect="sqlite"),
)
sa.event.listen(
    STRATEGY_VERSIONS,
    "after_create",
    sa.DDL(  # type: ignore[no-untyped-call]
        "CREATE OR REPLACE FUNCTION joseph_forbid_version_rewrite() "
        "RETURNS trigger AS $$ BEGIN IF NEW.definition_json <> "
        "OLD.definition_json OR NEW.definition_hash <> OLD.definition_hash "
        "THEN RAISE EXCEPTION 'strategy versions are immutable'; END IF; "
        "RETURN NEW; END; $$ LANGUAGE plpgsql; CREATE TRIGGER "
        "strategy_versions_immutable BEFORE UPDATE ON strategy_versions FOR "
        "EACH ROW EXECUTE FUNCTION joseph_forbid_version_rewrite();"
    ).execute_if(dialect="postgresql"),
)


def create_schema(engine: sa.Engine) -> None:
    """Creates the strategy tables if missing.

    Args:
        engine: SQLAlchemy engine.
    """
    METADATA.create_all(engine)


def _row_to_version(
    row: sa.Row[tuple[object, ...]],
) -> versions.StrategyVersion:
    """Maps a row to a version.

    Args:
        row: ``strategy_versions`` row.

    Returns:
        The version.
    """
    data = row._mapping
    activation = None
    if data["activation_json"]:
        raw = json.loads(str(data["activation_json"]))
        activation = versions.Activation(
            mode=str(raw["mode"]),
            activated_at_ms=int(raw["activated_at_ms"]),
            instruments=tuple(str(i) for i in raw.get("instruments", [])),
        )
    return versions.StrategyVersion(
        strategy_id=str(data["strategy_id"]),
        version=int(data["version"]),
        tenant_id=str(data["tenant_id"]),
        author_user_id=str(data["author_user_id"]),
        created_at_ms=int(data["created_at_ms"]),
        definition=ast.parse_definition(
            json.loads(str(data["definition_json"]))
        ),
        definition_hash=str(data["definition_hash"]),
        compiler_version=str(data["compiler_version"]),
        evaluator_version=str(data["evaluator_version"]),
        state=versions.StrategyState(str(data["state"])),
        activation=activation,
    )


def _activation_json(activation: versions.Activation | None) -> str | None:
    """Serializes activation metadata.

    Args:
        activation: Activation or ``None``.

    Returns:
        JSON text or ``None``.
    """
    if activation is None:
        return None
    return json.dumps(activation.to_contract(), sort_keys=True)


class SqlStrategyRepository:
    """``StrategyRepository`` backed by SQLAlchemy Core.

    Attributes:
        engine: SQLAlchemy engine.
    """

    def __init__(self, engine: sa.Engine) -> None:
        """Initializes the repository.

        Args:
            engine: SQLAlchemy engine (schema created by ``create_schema``).
        """
        self.engine = engine

    def add(self, version: versions.StrategyVersion) -> None:
        """Stores a new version.

        Args:
            version: Version to store.

        Raises:
            errors.ConflictError: On a duplicate version.
        """
        try:
            with self.engine.begin() as conn:
                conn.execute(
                    STRATEGY_VERSIONS.insert().values(
                        tenant_id=version.tenant_id,
                        strategy_id=version.strategy_id,
                        version=version.version,
                        author_user_id=version.author_user_id,
                        created_at_ms=version.created_at_ms,
                        definition_json=version.definition.canonical_json(),
                        definition_hash=version.definition_hash,
                        compiler_version=version.compiler_version,
                        evaluator_version=version.evaluator_version,
                        state=version.state.value,
                        activation_json=_activation_json(version.activation),
                    )
                )
        except sa.exc.IntegrityError as err:
            raise errors.ConflictError(
                f"{version.strategy_version_id} already exists"
            ) from err

    def get(
        self, tenant_id: str, strategy_id: str, version: int
    ) -> versions.StrategyVersion | None:
        """Fetches one version.

        Args:
            tenant_id: Owning tenant.
            strategy_id: Strategy slug.
            version: Version number.

        Returns:
            The version or ``None``.
        """
        table = STRATEGY_VERSIONS
        query = sa.select(table).where(
            table.c.tenant_id == tenant_id,
            table.c.strategy_id == strategy_id,
            table.c.version == version,
        )
        with self.engine.connect() as conn:
            row = conn.execute(query).first()
        return None if row is None else _row_to_version(row)

    def list_versions(
        self, tenant_id: str, strategy_id: str
    ) -> Sequence[versions.StrategyVersion]:
        """Lists versions newest first.

        Args:
            tenant_id: Owning tenant.
            strategy_id: Strategy slug.

        Returns:
            Versions.
        """
        table = STRATEGY_VERSIONS
        query = (
            sa.select(table)
            .where(
                table.c.tenant_id == tenant_id,
                table.c.strategy_id == strategy_id,
            )
            .order_by(table.c.version.desc())
        )
        with self.engine.connect() as conn:
            return [_row_to_version(row) for row in conn.execute(query)]

    def list_latest(self, tenant_id: str) -> Sequence[versions.StrategyVersion]:
        """Lists the latest version per strategy.

        Args:
            tenant_id: Owning tenant.

        Returns:
            Versions sorted by strategy id.
        """
        table = STRATEGY_VERSIONS
        latest = (
            sa.select(
                table.c.strategy_id,
                sa.func.max(table.c.version).label("version"),
            )
            .where(table.c.tenant_id == tenant_id)
            .group_by(table.c.strategy_id)
            .subquery()
        )
        query = (
            sa.select(table)
            .join(
                latest,
                sa.and_(
                    table.c.strategy_id == latest.c.strategy_id,
                    table.c.version == latest.c.version,
                ),
            )
            .where(table.c.tenant_id == tenant_id)
            .order_by(table.c.strategy_id)
        )
        with self.engine.connect() as conn:
            return [_row_to_version(row) for row in conn.execute(query)]

    def update_lifecycle(
        self,
        tenant_id: str,
        strategy_id: str,
        version: int,
        state: versions.StrategyState,
        activation: versions.Activation | None,
    ) -> versions.StrategyVersion:
        """Updates state and activation only.

        Args:
            tenant_id: Owning tenant.
            strategy_id: Strategy slug.
            version: Version number.
            state: New state.
            activation: New activation.

        Returns:
            Updated version.

        Raises:
            errors.NotFoundError: If missing.
        """
        table = STRATEGY_VERSIONS
        with self.engine.begin() as conn:
            result = conn.execute(
                table.update()
                .where(
                    table.c.tenant_id == tenant_id,
                    table.c.strategy_id == strategy_id,
                    table.c.version == version,
                )
                .values(
                    state=state.value,
                    activation_json=_activation_json(activation),
                )
            )
            if result.rowcount == 0:
                raise errors.NotFoundError(f"{strategy_id}:v{version}")
        found = self.get(tenant_id, strategy_id, version)
        if found is None:
            raise errors.NotFoundError(f"{strategy_id}:v{version}")
        return found

    def list_active(self) -> Sequence[versions.StrategyVersion]:
        """Lists ACTIVE versions across tenants.

        Returns:
            Active versions.
        """
        table = STRATEGY_VERSIONS
        query = sa.select(table).where(
            table.c.state == versions.StrategyState.ACTIVE.value
        )
        with self.engine.connect() as conn:
            return [_row_to_version(row) for row in conn.execute(query)]


class SqlCheckpointRepository:
    """``CheckpointRepository`` backed by SQLAlchemy Core.

    Attributes:
        engine: SQLAlchemy engine.
    """

    def __init__(self, engine: sa.Engine) -> None:
        """Initializes the repository.

        Args:
            engine: SQLAlchemy engine.
        """
        self.engine = engine

    def load(
        self, key: setup_fsm.InstanceKey
    ) -> setup_fsm.InstanceState | None:
        """Loads a checkpoint.

        Args:
            key: Instance key.

        Returns:
            State or ``None``.
        """
        table = STRATEGY_INSTANCES
        query = sa.select(table.c.state_json).where(
            table.c.instance_key == key.as_text()
        )
        with self.engine.connect() as conn:
            row = conn.execute(query).first()
        if row is None:
            return None
        return setup_fsm.InstanceState.from_dict(json.loads(str(row[0])))

    def save(self, state: setup_fsm.InstanceState, updated_at_ms: int) -> None:
        """Upserts a checkpoint.

        Args:
            state: Instance state.
            updated_at_ms: Save time.
        """
        table = STRATEGY_INSTANCES
        key = state.key
        values = {
            "tenant_id": key.tenant_id,
            "strategy_version_id": key.strategy_version_id,
            "instrument_id": key.instrument_id,
            "direction": key.direction,
            "state_json": json.dumps(state.to_dict(), sort_keys=True),
            "updated_at_ms": updated_at_ms,
        }
        with self.engine.begin() as conn:
            updated = conn.execute(
                table.update()
                .where(table.c.instance_key == key.as_text())
                .values(**values)
            )
            if updated.rowcount == 0:
                conn.execute(
                    table.insert().values(instance_key=key.as_text(), **values)
                )
