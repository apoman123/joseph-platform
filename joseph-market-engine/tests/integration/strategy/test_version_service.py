# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Integration tests of strategy versioning, validation and activation."""

from __future__ import annotations

import copy
from typing import Any

import pytest
import sqlalchemy as sa

from joseph.domain import clock
from joseph.domain import errors
from joseph.domain import identity
from joseph.strategy.adapters import memory
from joseph.strategy.adapters import sql
from joseph.strategy.app import ports
from joseph.strategy.app import version_service
from joseph.strategy.domain import ast
from joseph.strategy.domain import errors as strategy_errors
from joseph.strategy.domain import versions
from joseph.strategy.testing import fixtures

ALICE = identity.Principal("tenant-a", "alice")
BOB = identity.Principal("tenant-b", "bob")


@pytest.fixture(params=["memory", "sql"])
def repository(
    request: pytest.FixtureRequest, engine: sa.Engine
) -> ports.StrategyRepository:
    """Provides each repository implementation.

    Args:
        request: Parametrization: ``memory`` or ``sql``.
        engine: SQLite engine; the SQL repository creates its schema there.

    Returns:
        An empty repository.
    """
    if request.param == "memory":
        return memory.InMemoryStrategyRepository()
    sql.create_schema(engine)
    return sql.SqlStrategyRepository(engine)


def _long_a(**changes: object) -> ast.StrategyDefinition:
    """Returns Long A with top-level changes.

    Args:
        **changes: Top-level contract fields to replace.

    Returns:
        The parsed definition.
    """
    raw = copy.deepcopy(
        fixtures.strategy_definitions()[fixtures.LONG_A].to_contract()
    )
    raw.update(changes)
    return ast.parse_definition(raw)


def _service(
    repository: ports.StrategyRepository,
    manual_clock: clock.ManualClock,
    **kwargs: Any,
) -> version_service.StrategyVersionService:
    """Builds a version service over a repository.

    Args:
        repository: Strategy repository.
        manual_clock: Clock stamping versions.
        **kwargs: ``StrategyVersionService`` options such as ``on_change``.

    Returns:
        The service.
    """
    return version_service.StrategyVersionService(
        repository,
        manual_clock,
        **kwargs,
    )


def test_create_and_version(
    repository: ports.StrategyRepository, manual_clock: clock.ManualClock
) -> None:
    """Edits create new immutable versions; identical edits do not."""
    service = _service(repository, manual_clock)
    v1 = service.create(ALICE, _long_a())
    assert (v1.version, v1.state) == (1, versions.StrategyState.VALIDATED)
    with pytest.raises(errors.ConflictError):
        service.create(ALICE, _long_a())
    assert service.create_version(ALICE, v1.strategy_id, _long_a()) == v1
    manual_clock.advance(1000)
    v2 = service.create_version(ALICE, v1.strategy_id, _long_a(name="Edited"))
    assert v2.version == 2
    assert v2.definition_hash != v1.definition_hash
    history = service.get_versions(ALICE, v1.strategy_id)
    assert [v.version for v in history] == [2, 1]
    assert history[1].definition == v1.definition
    assert [v.version for v in service.list_latest(ALICE)] == [2]


def test_tenant_isolation(
    repository: ports.StrategyRepository, manual_clock: clock.ManualClock
) -> None:
    """Another tenant cannot see or change a strategy."""
    service = _service(repository, manual_clock)
    v1 = service.create(ALICE, _long_a())
    with pytest.raises(errors.NotFoundError):
        service.get_versions(BOB, v1.strategy_id)
    with pytest.raises(errors.NotFoundError):
        service.activate(BOB, v1.strategy_id, 1, "PAPER")
    assert service.list_latest(BOB) == []
    bob_v1 = service.create(BOB, _long_a())
    assert bob_v1.tenant_id == "tenant-b"


def test_activation_rules(
    repository: ports.StrategyRepository, manual_clock: clock.ManualClock
) -> None:
    """Only one version is active; edits never inherit activation."""
    changes: list[str] = []
    service = _service(
        repository, manual_clock, on_change=lambda: changes.append("x")
    )
    v1 = service.create(ALICE, _long_a())
    active = service.activate(
        ALICE, v1.strategy_id, 1, "PAPER", ["binance:perp:BTCUSDT"]
    )
    assert active.state is versions.StrategyState.ACTIVE
    assert active.activation is not None
    assert active.activation.instruments == ("binance:perp:BTCUSDT",)
    v2 = service.create_version(ALICE, v1.strategy_id, _long_a(name="v2"))
    assert v2.state is versions.StrategyState.VALIDATED
    assert v2.activation is None
    service.activate(ALICE, v1.strategy_id, 2, "SHADOW")
    states = {
        v.version: v.state for v in service.get_versions(ALICE, v1.strategy_id)
    }
    assert states == {
        1: versions.StrategyState.VALIDATED,
        2: versions.StrategyState.ACTIVE,
    }
    paused = service.pause(ALICE, v1.strategy_id)
    assert paused.state is versions.StrategyState.PAUSED
    with pytest.raises(errors.ConflictError):
        service.pause(ALICE, v1.strategy_id)
    assert len(changes) == 3


def test_auto_live_cannot_be_activated(
    repository: ports.StrategyRepository, manual_clock: clock.ManualClock
) -> None:
    """AUTO_LIVE, unknown modes and out-of-universe instruments are refused."""
    service = _service(repository, manual_clock)
    v1 = service.create(ALICE, _long_a(execution_mode="AUTO_LIVE"))
    with pytest.raises(strategy_errors.TradeModeDisabledError) as caught:
        service.activate(ALICE, v1.strategy_id, 1, "AUTO_LIVE")
    assert caught.value.code == "TRADE_MODE_DISABLED"
    assert "consent" in caught.value.message
    with pytest.raises(errors.ValidationFailedError):
        service.activate(ALICE, v1.strategy_id, 1, "YOLO")
    with pytest.raises(errors.ValidationFailedError):
        service.activate(
            ALICE, v1.strategy_id, 1, "PAPER", ["mexc:perp:BTC_USDT"]
        )


def test_removed_plugin_marks_version_unsupported(
    repository: ports.StrategyRepository,
    manual_clock: clock.ManualClock,
) -> None:
    """A version whose plugin was removed cannot run and is flagged."""
    full = _service(repository, manual_clock)
    v1 = full.create(ALICE, _long_a())
    full.activate(ALICE, v1.strategy_id, 1, "PAPER")
    reduced = _service(
        repository,
        manual_clock,
        available_plugins={
            "daily_bias",
            "premium_discount_ote",
            "fvg_ifvg",
            "order_breaker_blocks",
            "liquidity_sweeps",
        },
    )
    result = reduced.revalidate(ALICE, v1.strategy_id)
    assert not result.valid
    stored = reduced.get_version(ALICE, v1.strategy_id, 1)
    assert stored.state is versions.StrategyState.UNSUPPORTED
    assert stored.definition == v1.definition
    with pytest.raises(strategy_errors.StrategyValidationError) as caught:
        reduced.activate(ALICE, v1.strategy_id, 1, "PAPER")
    assert caught.value.issues[0]["code"] == "PLUGIN_UNAVAILABLE"
    assert full.revalidate(ALICE, v1.strategy_id).valid
    assert full.get_version(ALICE, v1.strategy_id).state is (
        versions.StrategyState.VALIDATED
    )


def test_invalid_definitions_are_saved_as_drafts(
    repository: ports.StrategyRepository,
    manual_clock: clock.ManualClock,
) -> None:
    """Semantically invalid rules are kept as DRAFT and cannot activate."""
    service = _service(repository, manual_clock)
    draft = service.create(
        ALICE,
        _long_a(
            risk={
                "min_rr": "2",
                "stop_model": "ATR",
                "target_model": "FIXED_R",
                "fixed_r": "2",
            }
        ),
    )
    assert draft.state is versions.StrategyState.DRAFT
    with pytest.raises(strategy_errors.StrategyValidationError):
        service.activate(ALICE, draft.strategy_id, 1, "ALERT_ONLY")


def test_write_scope_is_required_when_scopes_present(
    repository: ports.StrategyRepository,
    manual_clock: clock.ManualClock,
) -> None:
    """Read-only principals cannot write."""
    service = _service(repository, manual_clock)
    reader = identity.Principal("tenant-a", "r", frozenset({"strategy:read"}))
    with pytest.raises(errors.ForbiddenError):
        service.create(reader, _long_a())


def test_path_and_body_ids_must_match(
    repository: ports.StrategyRepository, manual_clock: clock.ManualClock
) -> None:
    """A version body for another strategy is rejected."""
    service = _service(repository, manual_clock)
    service.create(ALICE, _long_a())
    with pytest.raises(errors.ValidationFailedError):
        service.create_version(ALICE, "other-id", _long_a())
