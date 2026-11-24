# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""SQL signal repository: unique dedupe keys, filters and isolation."""

from __future__ import annotations

import dataclasses

import pytest
import sqlalchemy as sa

from joseph.domain import errors
from joseph.domain import identity
from joseph.signals.adapters import memory
from joseph.signals.adapters import sql
from joseph.signals.app import ports
from joseph.signals.app import service
from joseph.signals.domain import signal as signal_domain


def _signal(
    n: int,
    *,
    tenant: str = "t1",
    kind: str = "ENTRY_CONFIRMED",
    instrument: str = "binance:perp:BTCUSDT",
) -> signal_domain.Signal:
    """Builds a signal whose bar closes at ``n`` minutes.

    Args:
        n: Bar index.
        tenant: Tenant.
        kind: Kind.
        instrument: Instrument.

    Returns:
        Signal.
    """
    key = f"{tenant}|s:v1|*|{instrument}|LONG|setup-{n}|{kind}"
    return signal_domain.Signal(
        signal_id=signal_domain.signal_id_for(key),
        dedupe_key=key,
        tenant_id=tenant,
        strategy_id="s",
        strategy_version_id="s:v1",
        setup_id=f"setup-{n}",
        instrument_id=instrument,
        direction="LONG",
        timeframe="5m",
        kind=kind,
        setup_state="FIRED",
        severity="ENTRY",
        bar_start_ms=n * 60_000,
        bar_close_ms=(n + 1) * 60_000,
        evaluated_at_ms=(n + 1) * 60_000,
        inputs_snapshot_hash="h",
        rule_version="r",
        evaluator_version="1",
        indicator_versions={},
        reason_tree={"op": "ALL"},
        aoi_ids=("a",),
        range_ids=(),
        entry_price=None,
        stop_price=None,
        target_price=None,
        rr=None,
        risk_status="PASS",
        risk_reasons=(),
        mode="ALERT_ONLY",
        data_source="LIVE",
    )


@pytest.fixture(params=["memory", "sql"])
def repository(
    request: pytest.FixtureRequest, engine: sa.Engine
) -> ports.SignalRepository:
    """Provides each repository implementation.

    Args:
        request: Parametrization.
        engine: SQLite engine.

    Returns:
        Repository.
    """
    if request.param == "memory":
        return memory.InMemorySignalRepository()
    sql.create_schema(engine)
    return sql.SqlSignalRepository(engine)


def test_dedupe_key_is_unique(repository: ports.SignalRepository) -> None:
    """The same dedupe key is stored once, even with a new id."""
    first = _signal(1)
    assert repository.add(first)
    assert not repository.add(first)
    assert not repository.add(dataclasses.replace(first, signal_id="sig-x"))


def test_query_filters_order_and_isolation(
    repository: ports.SignalRepository,
) -> None:
    """Filters apply, newest first, scoped to the tenant."""
    for n in range(5):
        repository.add(_signal(n))
    repository.add(_signal(9, kind="ENTRY_BLOCKED"))
    repository.add(_signal(7, instrument="hyperliquid:perp:BTC"))
    repository.add(_signal(3, tenant="t2"))
    svc = service.SignalService(repository)
    alice = identity.Principal("t1", "a")
    newest = svc.query(alice, ports.SignalFilter(limit=3))
    assert [s.bar_close_ms for s in newest] == [600_000, 480_000, 300_000]
    blocked = svc.query(alice, ports.SignalFilter(kinds=("ENTRY_BLOCKED",)))
    assert [s.kind for s in blocked] == ["ENTRY_BLOCKED"]
    ranged = svc.query(
        alice,
        ports.SignalFilter(
            from_ms=120_000, to_ms=240_000, instrument_id="binance:perp:BTCUSDT"
        ),
    )
    assert [s.bar_close_ms for s in ranged] == [180_000, 120_000]
    assert (
        len(svc.query(identity.Principal("t2", "b"), ports.SignalFilter())) == 1
    )
    found = svc.get(alice, _signal(1).signal_id)
    assert found == _signal(1)
    with pytest.raises(errors.NotFoundError):
        svc.get(identity.Principal("t2", "b"), _signal(1).signal_id)
    with pytest.raises(errors.ValidationFailedError):
        svc.query(alice, ports.SignalFilter(limit=0))
    with pytest.raises(errors.ValidationFailedError):
        svc.query(alice, ports.SignalFilter(from_ms=5, to_ms=5))
