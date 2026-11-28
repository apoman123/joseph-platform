# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Strategy activations: lifecycle, signals, dedupe, forwarding, restart."""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi import testclient
from sqlalchemy import pool

from joseph import bootstrap
from joseph.alerts.adapters import memory as alert_memory
from joseph.alerts.domain import model as alert_model
from joseph.domain import clock
from joseph.domain import errors
from joseph.domain import identity
from joseph.platform import config
from joseph.scripts import activations
from joseph.scripts import models
from joseph.scripts import service as script_service
from joseph.signals.adapters import memory as signal_memory
from joseph.signals.app import ports as signal_ports
from joseph.signals.app import service as signal_service

BAR_MS = 60_000
"""One-minute bars."""

NOW = 1_795_277_530_000
"""Fixed market clock start."""

OWNER = identity.Principal("tenant-a", "user-a")
"""Activation owner."""


class _Runner:
    """Docker runner stand-in.

    Attributes:
        enabled: Whether tenant source can execute.
    """

    def __init__(self, enabled: bool) -> None:
        """Sets availability.

        Args:
            enabled: Whether tenant source can execute.
        """
        self.enabled = enabled


class _Market:
    """Market clock stand-in.

    Attributes:
        clock: Manual clock.
    """

    def __init__(self, manual: clock.ManualClock) -> None:
        """Wraps the clock.

        Args:
            manual: Manual clock.
        """
        self.clock = manual

    def now_ms(self) -> int:
        """Reads the clock.

        Returns:
            UTC epoch milliseconds.
        """
        return self.clock.now_ms()


class _Scripts:
    """ScriptService stand-in that returns scripted strategy results.

    Attributes:
        market_data: Clock holder.
        runner: Tenant runtime availability.
        source_hash: Hash the resolved built-in reports.
        orders: Orders queued on the newest bar of the next run.
        position: Simulated position after the next run.
        closed: Start of the newest closed bar of the next run.
        runs: Number of runs.
    """

    def __init__(self, manual: clock.ManualClock) -> None:
        """Starts without orders.

        Args:
            manual: Manual clock.
        """
        self.market_data = _Market(manual)
        self.runner = _Runner(enabled=False)
        self.source_hash = "a" * 64
        self.orders: list[dict[str, Any]] = []
        self.position = 0.0
        self.closed = NOW - BAR_MS
        self.runs = 0

    def resolve(
        self, tenant_id: str, script_id: str, version: int | None = None
    ) -> script_service.ResolvedScript:
        """Resolves a fake catalog strategy or a tenant indicator.

        Args:
            tenant_id: Owner.
            script_id: Script id.
            version: Ignored.

        Returns:
            Resolved script.
        """
        del tenant_id, version
        catalog = script_id.startswith("builtin:")
        return script_service.ResolvedScript(
            script_id=script_id,
            version=0 if catalog else 3,
            kind="strategy" if script_id != "plotter" else "indicator",
            name="Test strategy",
            overlay=True,
            defaults={},
            requests={},
            source_hash=self.source_hash if catalog else "sha256:t",
            entry=object() if catalog else None,  # type: ignore[arg-type]  # a marker stands in for a catalog entry
            tenant=None,
        )

    def runnable(self, script: script_service.ResolvedScript) -> bool:
        """Tells whether a script can run.

        Args:
            script: Resolved script.

        Returns:
            True for catalog scripts or with the runner enabled.
        """
        return script.entry is not None or self.runner.enabled

    async def run(
        self,
        principal: identity.Principal,
        script_id: str,
        request: models.ScriptRun,
    ) -> models.ScriptResult:
        """Returns the scripted result.

        Args:
            principal: Caller.
            script_id: Script id.
            request: Run request.

        Returns:
            A strategy result whose newest bar closes at ``closed + BAR_MS``.
        """
        del principal
        self.runs += 1
        return models.ScriptResult.model_validate(
            {
                "script_id": script_id,
                "version": 0,
                "kind": "strategy",
                "source_hash": self.source_hash,
                "instrument_id": request.instrument_id,
                "tf": request.tf.value,
                "bars_count": 2,
                "feed_state": "LIVE",
                "data_source": "test",
                "complete": True,
                "overlay": True,
                "plots": [],
                "fills": [],
                "equity_curve": [],
                "metrics": {},
                "orders": self.orders,
                "position_size": self.position,
                "bar_closes": {
                    str(self.closed - BAR_MS): self.closed,
                    str(self.closed): self.closed + BAR_MS,
                },
            }
        )


@dataclasses.dataclass
class _Harness:
    """Service under test and its collaborators.

    Attributes:
        clock: Manual clock.
        scripts: Script stand-in.
        engine: SQLite engine.
        signals: Signal service.
        outbox: Delivery outbox.
        subscriptions: Subscription store.
        service: Activation service.
    """

    clock: clock.ManualClock
    scripts: _Scripts
    engine: sa.Engine
    signals: signal_service.SignalService
    outbox: alert_memory.InMemoryOutbox
    subscriptions: alert_memory.InMemorySubscriptionRepository
    service: activations.StrategyActivations

    def restart(self) -> activations.StrategyActivations:
        """Builds a new service on the same database, like a restart.

        Returns:
            The new service.
        """
        self.service = activations.StrategyActivations(
            self.scripts,  # type: ignore[arg-type]  # a stub stands in for ScriptService
            self.engine,
            history=self.signals,
            subscriptions=self.subscriptions,
            outbox=self.outbox,
            forward_execution=True,
        )
        return self.service


@pytest.fixture
def harness() -> _Harness:
    """Builds an activation service over in-memory stores.

    Returns:
        The harness.
    """
    manual = clock.ManualClock(NOW)
    engine = sa.create_engine(
        "sqlite+pysqlite:///:memory:",
        poolclass=pool.StaticPool,
        connect_args={"check_same_thread": False},
    )
    built = _Harness(
        clock=manual,
        scripts=_Scripts(manual),
        engine=engine,
        signals=signal_service.SignalService(
            signal_memory.InMemorySignalRepository()
        ),
        outbox=alert_memory.InMemoryOutbox(),
        subscriptions=alert_memory.InMemorySubscriptionRepository(),
        service=None,  # type: ignore[arg-type]  # restart() assigns it below
    )
    built.restart()
    return built


def _request(**changes: Any) -> activations.StrategyActivationInput:
    """Builds an activation request.

    Args:
        **changes: Field overrides.

    Returns:
        Request for builtin:test on one instrument.
    """
    body: dict[str, Any] = {
        "script_id": "builtin:test",
        "instrument_ids": ["binance:perp:BTCUSDT"],
        "tf": "1m",
        "mode": "PAPER",
    }
    return activations.StrategyActivationInput.model_validate(body | changes)


def _close_bar(harness: _Harness, orders: list[dict[str, Any]]) -> None:
    """Advances one bar and scripts orders on the newly closed bar.

    Args:
        harness: Harness.
        orders: Orders queued on the newest closed bar.
    """
    harness.clock.advance(BAR_MS)
    harness.scripts.closed += BAR_MS
    harness.scripts.orders = [
        {
            "time_ms": harness.scripts.closed,
            "reference": 100.0,
            "status": "pending",
            **order,
        }
        for order in orders
    ]


async def test_entry_becomes_one_forwarded_signal(harness: _Harness) -> None:
    """A PAPER entry is recorded once and routed to UI and execution."""
    activation = harness.service.create(OWNER, _request())
    assert activation.strategy_version_id == "builtin:test:vaaaaaaaaaaaa"
    assert await harness.service.run_once() == 0
    _close_bar(
        harness,
        [{"action": "long", "quantity": 1, "stop": 95.0, "target": 110.0}],
    )
    assert await harness.service.run_once() == 1
    (signal,) = harness.signals.query(OWNER, signal_ports.SignalFilter())
    assert signal.kind == "ENTRY_CONFIRMED"
    assert signal.direction == "LONG"
    assert signal.mode == "PAPER"
    assert signal.strategy_version_id == activation.strategy_version_id
    assert signal.bar_start_ms == harness.scripts.closed
    assert str(signal.entry_price) == "100.0"
    assert str(signal.stop_price) == "95.0"
    assert str(signal.rr) == "2.00"
    channels = sorted(r.channel.value for r in harness.outbox.all())
    assert channels == ["EXECUTION", "UI"]
    assert await harness.service.run_once() == 0
    assert len(harness.outbox.all()) == 2
    stored = harness.service.get(OWNER, activation.activation_id)
    assert stored.emitted == 1
    assert stored.instruments["binance:perp:BTCUSDT"].last_error is None


async def test_alert_only_is_not_forwarded_and_exits_signal(
    harness: _Harness,
) -> None:
    """ALERT_ONLY entries reach the UI only; closes are exit signals."""
    harness.service.create(OWNER, _request(mode="ALERT_ONLY"))
    _close_bar(harness, [{"action": "short", "quantity": 1}])
    await harness.service.run_once()
    harness.scripts.position = -1.0
    _close_bar(harness, [{"action": "close", "quantity": 0}])
    await harness.service.run_once()
    kinds = [
        (s.kind, s.direction)
        for s in harness.signals.query(OWNER, signal_ports.SignalFilter())
    ]
    assert kinds == [
        ("POSITION_EXITED", "SHORT"),
        ("ENTRY_CONFIRMED", "SHORT"),
    ]
    assert {r.channel for r in harness.outbox.all()} == {alert_model.Channel.UI}


async def test_reversal_emits_exit_then_entry(harness: _Harness) -> None:
    """A long target while short exits the short and enters long."""
    harness.service.create(OWNER, _request())
    harness.scripts.position = -2.0
    _close_bar(harness, [{"action": "long", "quantity": 1}])
    created = await harness.service.run_once()
    assert created == 2
    kinds = {
        s.kind
        for s in harness.signals.query(OWNER, signal_ports.SignalFilter())
    }
    assert kinds == {"POSITION_EXITED", "ENTRY_CONFIRMED"}


async def test_history_is_never_replayed(harness: _Harness) -> None:
    """Bars closed before activation, while paused or before a restart."""
    _close_bar(harness, [{"action": "long", "quantity": 1}])
    activation = harness.service.create(OWNER, _request())
    assert await harness.service.run_once() == 0
    harness.service.set_state(OWNER, activation.activation_id, "PAUSED")
    _close_bar(harness, [{"action": "long", "quantity": 1}])
    assert await harness.service.run_once() == 0
    harness.service.set_state(OWNER, activation.activation_id, "ACTIVE")
    assert await harness.service.run_once() == 0
    harness.restart()
    assert await harness.service.run_once() == 0
    _close_bar(harness, [{"action": "long", "quantity": 1}])
    assert await harness.service.run_once() == 1
    harness.restart()
    assert await harness.service.run_once() == 0


async def test_stale_bars_and_changed_code_do_not_signal(
    harness: _Harness,
) -> None:
    """A bar closed long ago is skipped; a new built-in hash is an error."""
    activation = harness.service.create(OWNER, _request())
    harness.scripts.closed += BAR_MS
    harness.scripts.orders = [
        {
            "time_ms": harness.scripts.closed,
            "action": "long",
            "quantity": 1,
            "reference": 100.0,
            "status": "pending",
        }
    ]
    harness.clock.advance(60 * BAR_MS)
    assert await harness.service.run_once() == 0
    harness.scripts.source_hash = "b" * 64
    _close_bar(harness, [{"action": "long", "quantity": 1}])
    assert await harness.service.run_once() == 0
    stored = harness.service.get(OWNER, activation.activation_id)
    assert stored.last_error is not None
    assert "changed" in stored.last_error
    with pytest.raises(errors.ConflictError):
        harness.service.set_state(OWNER, activation.activation_id, "PAUSED")
        harness.service.set_state(OWNER, activation.activation_id, "ACTIVE")


def test_activation_rules(harness: _Harness) -> None:
    """AUTO_LIVE, non-strategies, unknown channels and tenant source."""
    with pytest.raises(errors.TradeModeDisabledError):
        harness.service.create(OWNER, _request(mode="AUTO_LIVE"))
    with pytest.raises(errors.ValidationFailedError):
        harness.service.create(OWNER, _request(script_id="plotter"))
    with pytest.raises(errors.ValidationFailedError):
        harness.service.create(OWNER, _request(channels=["EXECUTION"]))
    with pytest.raises(errors.ValidationFailedError, match="bond ETF"):
        harness.service.create(
            OWNER, _request(instrument_ids=["treasury:rate:UST10Y"])
        )
    with pytest.raises(errors.UnsupportedError):
        harness.service.create(OWNER, _request(script_id="my-strategy"))
    harness.scripts.runner.enabled = True
    tenant = harness.service.create(OWNER, _request(script_id="my-strategy"))
    assert tenant.strategy_version_id == "my-strategy:v3"
    other = identity.Principal("tenant-b", "user-b")
    assert harness.service.list_owned(other).activations == []
    with pytest.raises(errors.NotFoundError):
        harness.service.get(other, tenant.activation_id)
    harness.service.delete(OWNER, tenant.activation_id)
    assert harness.service.list_owned(OWNER).activations == []


HEADERS = {"Authorization": "Bearer act-a"}
"""Ordinary authenticated user."""


@pytest.fixture
def client() -> Iterator[testclient.TestClient]:
    """Serves a DEMO engine with the activation loop off.

    Yields:
        Started application client.
    """
    settings = dataclasses.replace(
        config.Settings(),
        demo_only=True,
        database_url="sqlite://",
        strategy_activations_enabled=False,
        script_runtime="disabled",
        dev_tokens=config.parse_dev_tokens("act-a:tenant-a:user-a"),
    )
    container = bootstrap.build_container(
        settings, clock=clock.ManualClock(NOW)
    )
    with testclient.TestClient(
        bootstrap.create_application(container)
    ) as connection:
        yield connection


def test_api_lifecycle_with_a_builtin_strategy(
    client: testclient.TestClient,
) -> None:
    """An activation is created, evaluated, paused, resumed and deleted."""
    refused = client.post(
        "/v1/strategy-activations",
        headers=HEADERS,
        json={
            "script_id": "builtin:ema-trend-long",
            "instrument_ids": ["mock:perp:BTCUSD"],
            "tf": "1m",
            "mode": "AUTO_LIVE",
        },
    )
    assert refused.status_code == 409
    assert refused.json()["code"] == "TRADE_MODE_DISABLED"
    created = client.post(
        "/v1/strategy-activations",
        headers=HEADERS,
        json={
            "script_id": "builtin:ema-trend-long",
            "instrument_ids": ["mock:perp:BTCUSD"],
            "tf": "1m",
            "mode": "PAPER",
            "inputs": {"require_flow": False},
            "history_bars": 200,
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["state"] == "ACTIVE"
    assert body["strategy_version_id"].startswith("builtin:ema-trend-long:v")
    service = client.app.state.strategy_activations  # type: ignore[attr-defined]  # TestClient.app is typed as a plain ASGI callable
    client.portal.call(service.run_once)  # type: ignore[union-attr]  # the portal exists inside the client context
    read = client.get(
        f"/v1/strategy-activations/{body['activation_id']}", headers=HEADERS
    ).json()
    assert read["last_checked_ms"] is not None
    assert read["instruments"]["mock:perp:BTCUSD"]["last_error"] is None
    paused = client.post(
        f"/v1/strategy-activations/{body['activation_id']}/pause",
        headers=HEADERS,
    )
    assert paused.json()["state"] == "PAUSED"
    resumed = client.post(
        f"/v1/strategy-activations/{body['activation_id']}/resume",
        headers=HEADERS,
    )
    assert resumed.json()["state"] == "ACTIVE"
    listing = client.get("/v1/strategy-activations", headers=HEADERS).json()
    assert [a["activation_id"] for a in listing["activations"]] == [
        body["activation_id"]
    ]
    assert listing["runtime_enabled"] is False
    assert (
        client.delete(
            f"/v1/strategy-activations/{body['activation_id']}",
            headers=HEADERS,
        ).status_code
        == 204
    )


def test_strategy_backtest_returns_orders_and_trades(
    client: testclient.TestClient,
) -> None:
    """A built-in strategy run reports orders, trades and trade metrics."""
    response = client.post(
        "/v1/scripts/builtin:rsi-reversion-short/run",
        headers=HEADERS,
        json={
            "instrument_id": "mock:perp:BTCUSD",
            "tf": "1m",
            "limit": 1000,
            "inputs": {"min_rr": 0},
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["kind"] == "strategy"
    assert "trade_count" in body["metrics"]
    assert len(body["trades"]) >= body["metrics"]["trade_count"]
    for order in body["orders"]:
        assert order["action"] in {"short", "close"}
