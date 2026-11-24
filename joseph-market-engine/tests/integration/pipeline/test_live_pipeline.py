# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Integration tests for the live signal pipeline over the DEMO venue."""

from __future__ import annotations

import dataclasses
from collections.abc import AsyncIterator
from collections.abc import Callable

import pytest

from joseph import bootstrap
from joseph.domain import clock as clock_lib
from joseph.domain import identity
from joseph.platform import config
from joseph.platform import streaming
from joseph.signals.app import ports as signal_ports
from joseph.strategy.domain import ast

# 30 s after a 5m boundary, so advancing 5 minutes closes exactly one bar.
_T0 = 1_795_000_000_000 - (1_795_000_000_000 % 300_000) + 30_000
_BTC = "mock:perp:BTCUSD"
_PRINCIPAL = identity.Principal(tenant_id="tenant-a", user_id="user-a")


def _always_true(strategy_id: str) -> dict[str, object]:
    """Builds a strategy whose condition holds on every warm closed bar.

    The condition reads the built-in ``ema`` plugin, so the indicator
    engine and the plugin vocabulary wired by bootstrap are exercised.

    Args:
        strategy_id: Strategy slug.

    Returns:
        A contract strategy definition for DEMO venues in ALERT_ONLY mode.
    """
    return {
        "schema_version": 2,
        "strategy_id": strategy_id,
        "name": "EMA above zero",
        "direction": "LONG",
        "signal_timeframe": "5m",
        "universe": {"qualification": "ANY", "venues": ["mock"]},
        "when": {
            "op": "COMPARE",
            "left": {"kind": "indicator", "plugin_id": "ema", "field": "ema"},
            "cmp": "gt",
            "right": {"kind": "const", "value": "0"},
        },
        "risk": {
            "min_rr": "1.0",
            "stop_model": "ATR",
            "stop_buffer_atr": "1",
            "target_model": "FIXED_R",
            "fixed_r": "2",
        },
        "notification": {
            "cooldown_bars": 0,
            "once_per_aoi": False,
            "emit": ["ENTRY_CONFIRMED", "ENTRY_BLOCKED"],
            "channels": ["UI"],
        },
        "execution_mode": "ALERT_ONLY",
    }


@pytest.fixture
async def stack() -> AsyncIterator[
    tuple[bootstrap.Container, clock_lib.ManualClock]
]:
    """Builds a DEMO-only container on an in-memory database.

    The background pipeline is off; tests drive it with ``run_once``.

    Yields:
        The container and its manual clock; the container closes after the
        test.
    """
    settings = dataclasses.replace(
        config.Settings(),
        demo_only=True,
        database_url="sqlite://",
        pipeline_enabled=False,
    )
    clock = clock_lib.ManualClock(_T0)
    container = bootstrap.build_container(settings, clock=clock)
    yield container, clock
    await container.aclose()


async def _activate(container: bootstrap.Container) -> None:
    """Creates and activates the always-true strategy on BTC.

    Args:
        container: Container whose signal stack stores the strategy.
    """
    assert container.signal_stack is not None
    versions = container.signal_stack.versions
    definition = ast.parse_definition(_always_true("pipeline-test"))
    versions.create(_PRINCIPAL, definition)
    versions.activate(_PRINCIPAL, "pipeline-test", 1, "ALERT_ONLY", [_BTC])


async def test_warm_up_never_fires_for_past_bars(
    stack: tuple[bootstrap.Container, clock_lib.ManualClock],
) -> None:
    """The first pass primes history and records no signal."""
    container, _ = stack
    await _activate(container)
    assert container.signal_stack is not None
    stats = await container.signal_stack.pipeline.run_once()
    assert stats.plans == 1 and stats.series == 1 and stats.errors == 0
    assert stats.signals == 0
    listed = container.signal_stack.signals.query(
        _PRINCIPAL, signal_ports.SignalFilter(limit=50)
    )
    assert list(listed) == []


async def test_new_closed_bar_fires_once_and_reaches_ui_stream(
    stack: tuple[bootstrap.Container, clock_lib.ManualClock],
    contract_validate: Callable[[str, str, object], None],
) -> None:
    """A new closed bar signals once and reaches only its tenant's UI stream."""
    container, clock = stack
    await _activate(container)
    assert container.signal_stack is not None
    pipeline = container.signal_stack.pipeline
    await pipeline.run_once()
    clock.advance(300_000)
    stats = await pipeline.run_once()
    assert stats.frames >= 1 and stats.signals >= 1
    again = await pipeline.run_once()
    assert again.signals == 0
    signals = list(
        container.signal_stack.signals.query(
            _PRINCIPAL, signal_ports.SignalFilter(limit=50)
        )
    )
    assert signals
    for signal in signals:
        body = signal.to_contract()
        contract_validate("signal.schema.json", "Signal", body)
        close_ms = body["bar_close_ms"]
        assert isinstance(close_ms, int) and close_ms > _T0
        assert body["instrument_id"] == _BTC
    delivered = await container.signal_stack.outbox_worker.run_once()
    assert delivered.delivered >= 1
    subscription = streaming.Subscription("tenant-a", max_queue=100)
    subscription.channels.add("signals")
    replay = container.stream_hub.replay(subscription, 0) or []
    assert any(env.type == "signal.created" for env in replay)
    other = streaming.Subscription("tenant-b", max_queue=100)
    other.channels.add("signals")
    assert not (container.stream_hub.replay(other, 0) or [])
