# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""SQL strategy persistence: immutability, uniqueness and checkpoints."""

from __future__ import annotations

import dataclasses

import pytest
import sqlalchemy as sa

from joseph.domain import errors
from joseph.strategy.adapters import sql
from joseph.strategy.domain import setup_fsm
from joseph.strategy.domain import versions
from joseph.strategy.testing import fixtures


def _version(n: int, tenant: str = "tenant-a") -> versions.StrategyVersion:
    """Builds a VALIDATED EMA trend template version.

    Args:
        n: Version number.
        tenant: Owning tenant id.

    Returns:
        The version, not yet stored.
    """
    definition = fixtures.strategy_definitions()[fixtures.EMA_TREND_LONG]
    return fixtures.make_version(
        definition,
        tenant_id=tenant,
        version=n,
        state=versions.StrategyState.VALIDATED,
    )


def test_versions_round_trip_and_are_unique(engine: sa.Engine) -> None:
    """Stored versions read back equal; duplicates conflict."""
    sql.create_schema(engine)
    repo = sql.SqlStrategyRepository(engine)
    repo.add(_version(1))
    repo.add(_version(2))
    with pytest.raises(errors.ConflictError):
        repo.add(_version(2))
    assert repo.get("tenant-a", fixtures.EMA_TREND_LONG, 1) == _version(1)
    assert [v.version for v in repo.list_latest("tenant-a")] == [2]
    assert repo.get("tenant-b", fixtures.EMA_TREND_LONG, 1) is None


def test_database_rejects_definition_rewrites(engine: sa.Engine) -> None:
    """Triggers forbid definition rewrites; lifecycle updates still apply."""
    sql.create_schema(engine)
    repo = sql.SqlStrategyRepository(engine)
    repo.add(_version(1))
    table = sql.STRATEGY_VERSIONS
    with pytest.raises(sa.exc.DatabaseError), engine.begin() as conn:
        conn.execute(
            table.update().values(definition_hash="sha256:" + "0" * 64)
        )
    with pytest.raises(sa.exc.DatabaseError), engine.begin() as conn:
        conn.execute(table.update().values(definition_json="{}"))
    updated = repo.update_lifecycle(
        "tenant-a",
        fixtures.EMA_TREND_LONG,
        1,
        versions.StrategyState.ACTIVE,
        versions.Activation("PAPER", 5),
    )
    assert updated.state is versions.StrategyState.ACTIVE
    assert updated.definition == _version(1).definition
    assert [v.version for v in repo.list_active()] == [1]
    with pytest.raises(errors.NotFoundError):
        repo.update_lifecycle(
            "tenant-a",
            fixtures.EMA_TREND_LONG,
            9,
            versions.StrategyState.ACTIVE,
            None,
        )


def test_checkpoints_upsert(engine: sa.Engine) -> None:
    """Checkpoints insert then update in place."""
    sql.create_schema(engine)
    repo = sql.SqlCheckpointRepository(engine)
    key = setup_fsm.InstanceKey(
        "tenant-a", "s:v1", "*", "binance:perp:BTCUSDT", "LONG"
    )
    assert repo.load(key) is None
    state = setup_fsm.InstanceState(key=key, last_close_ms=100)
    repo.save(state, 1)
    repo.save(dataclasses.replace(state, last_close_ms=200), 2)
    loaded = repo.load(key)
    assert loaded is not None and loaded.last_close_ms == 200
