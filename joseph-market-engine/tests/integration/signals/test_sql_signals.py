# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Signal repositories (memory and SQL): dedupe keys, filters, isolation."""

from __future__ import annotations

import dataclasses
import json

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
    """Builds a signal whose bar spans minute ``n`` to ``n + 1``.

    Args:
        n: Bar index in minutes.
        tenant: Owning tenant id.
        kind: Signal kind.
        instrument: Canonical instrument id.

    Returns:
        The signal; equal arguments give the same ``signal_id``.
    """
    key = f"{tenant}|s:v1|{instrument}|{n}|LONG|{kind}"
    return signal_domain.Signal(
        signal_id=signal_domain.signal_id_for(key),
        dedupe_key=key,
        tenant_id=tenant,
        strategy_id="s",
        strategy_version_id="s:v1",
        source_hash="sha256:r",
        instrument_id=instrument,
        direction="LONG",
        timeframe="5m",
        kind=kind,
        severity=signal_domain.SEVERITY_BY_KIND[kind],
        mode="ALERT_ONLY",
        bar_start_ms=n * 60_000,
        bar_close_ms=(n + 1) * 60_000,
        evaluated_at_ms=(n + 1) * 60_000,
        data_source="LIVE",
    )


@pytest.fixture(params=["memory", "sql"])
def repository(
    request: pytest.FixtureRequest, engine: sa.Engine
) -> ports.SignalRepository:
    """Provides each repository implementation.

    Args:
        request: Parametrization: ``memory`` or ``sql``.
        engine: SQLite engine; the SQL repository creates its schema there.

    Returns:
        An empty repository.
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
    """Filters apply newest first within the tenant; bad filters are refused."""
    for n in range(5):
        repository.add(_signal(n))
    repository.add(_signal(9, kind="POSITION_EXITED"))
    repository.add(_signal(7, instrument="hyperliquid:perp:BTC"))
    repository.add(_signal(3, tenant="t2"))
    svc = service.SignalService(repository)
    alice = identity.Principal("t1", "a")
    newest = svc.query(alice, ports.SignalFilter(limit=3))
    assert [s.bar_close_ms for s in newest] == [600_000, 480_000, 300_000]
    exits = svc.query(alice, ports.SignalFilter(kinds=("POSITION_EXITED",)))
    assert [s.kind for s in exits] == ["POSITION_EXITED"]
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


def _legacy_body(kind: str, signal_id: str) -> dict[str, object]:
    """Builds a body stored by the removed declarative strategies.

    Args:
        kind: Stored signal kind.
        signal_id: Stored signal id; it also forms the dedupe key.

    Returns:
        The contract ``Signal`` of that era, with setup, AOI, reason tree,
        risk and volume fields.
    """
    return {
        "signal_id": signal_id,
        "dedupe_key": f"t1|{signal_id}",
        "tenant_id": "t1",
        "strategy_id": "price-alert",
        "strategy_version_id": "price-alert:v2",
        "setup_id": "setup-1",
        "instrument_id": "binance:perp:BTCUSDT",
        "direction": "NONE",
        "timeframe": "5m",
        "kind": kind,
        "setup_state": "WAITING",
        "severity": "WARNING",
        "bar_start_ms": 60_000,
        "bar_close_ms": 120_000,
        "evaluated_at_ms": 120_005,
        "inputs_snapshot_hash": "sha256:i",
        "rule_version": "sha256:r",
        "evaluator_version": "python-1",
        "indicator_versions": {"ema": "1.0.0"},
        "reason_tree": {"node_id": "cross_up", "op": "PYTHON"},
        "aoi_ids": ["zone-1"],
        "group_ids": [],
        "entry_price": None,
        "stop_price": None,
        "target_price": None,
        "rr": None,
        "risk_evaluation": {"status": "NOT_APPLICABLE", "reasons": []},
        "mode": "ALERT_ONLY",
        "data_source": "LIVE",
        "revision": 0,
        "message": "BTC crossed 95000",
        "bar_base_volume": "1.5",
        "bar_quote_volume": None,
    }


def _insert_raw(engine: sa.Engine, body: dict[str, object]) -> None:
    """Stores a body as an older engine wrote it.

    Args:
        engine: Engine with the signals table.
        body: Contract body to store verbatim.
    """
    with engine.begin() as conn:
        conn.execute(
            sql.SIGNALS.insert().values(
                signal_id=body["signal_id"],
                tenant_id=body["tenant_id"],
                dedupe_key=body["dedupe_key"],
                strategy_id=body["strategy_id"],
                strategy_version_id=body["strategy_version_id"],
                instrument_id=body["instrument_id"],
                kind=body["kind"],
                bar_close_ms=body["bar_close_ms"],
                body_json=json.dumps(body, sort_keys=True),
            )
        )


def test_legacy_bodies_stay_readable() -> None:
    """A body with the declarative-strategy fields restores current fields."""
    restored = signal_domain.Signal.from_contract(
        _legacy_body("CUSTOM_ALERT", "sig-old")
    )
    assert restored.source_hash == "sha256:r"
    assert restored.condition == "cross_up"
    assert restored.message == "BTC crossed 95000"
    assert set(restored.to_contract()) == set(_signal(1).to_contract())


def test_migration_drops_retired_kinds_and_rewrites_bodies(
    engine: sa.Engine,
) -> None:
    """Retired-kind rows go; other legacy rows take the current shape."""
    sql.METADATA.create_all(engine)
    _insert_raw(engine, _legacy_body("CUSTOM_ALERT", "sig-alert"))
    entry = _legacy_body("ENTRY_CONFIRMED", "sig-entry")
    entry.update(
        direction="LONG",
        entry_price="95000",
        stop_price="94000",
        target_price="97000",
        rr="2.00",
        mode="PAPER",
    )
    _insert_raw(engine, entry)
    for n, kind in enumerate(sorted(signal_domain.RETIRED_KINDS)):
        _insert_raw(engine, _legacy_body(kind, f"sig-retired-{n}"))
    current = _signal(5)
    repo = sql.SqlSignalRepository(engine)
    assert repo.add(current)
    assert sql.migrate_legacy_signals(engine) == 8
    assert sql.migrate_legacy_signals(engine) == 0
    sql.create_schema(engine)
    listed = repo.query("t1", ports.SignalFilter())
    assert sorted(s.signal_id for s in listed) == sorted(
        ["sig-alert", "sig-entry", current.signal_id]
    )
    with engine.connect() as conn:
        bodies = {
            str(row[0]): json.loads(str(row[1]))
            for row in conn.execute(
                sa.select(sql.SIGNALS.c.signal_id, sql.SIGNALS.c.body_json)
            )
        }
    expected_keys = set(current.to_contract())
    assert all(set(b) == expected_keys for b in bodies.values())
    assert bodies["sig-alert"]["condition"] == "cross_up"
    migrated_entry = bodies["sig-entry"]
    assert migrated_entry["source_hash"] == "sha256:r"
    assert (
        migrated_entry["stop_price"],
        migrated_entry["target_price"],
        migrated_entry["condition"],
    ) == ("94000", "97000", None)
    assert repo.get("t1", current.signal_id) == current
