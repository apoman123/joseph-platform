# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Unit tests for signal identity, hashing and serialization."""

from __future__ import annotations

import dataclasses
from collections.abc import Callable

from joseph.domain import quality
from joseph.signals.domain import signal as signal_domain
from joseph.strategy.app import processor
from joseph.strategy.domain import compiler
from joseph.strategy.domain import setup_fsm
from joseph.strategy.testing import fixtures
from joseph.strategy.testing import scenarios
from joseph.strategy.testing import vocabulary


def _entry_signal() -> signal_domain.Signal:
    """Runs the zone-touch rule and returns its entry signal.

    Returns:
        The first signal the rule emits over its scenario frames.
    """
    definition = scenarios.zone_long_definition()
    plan = compiler.compile_strategy(
        definition, vocabulary=vocabulary.VOCABULARY
    ).plan
    assert plan is not None
    version = fixtures.make_version(definition)
    proc = processor.InstanceProcessor(
        plan,
        version,
        setup_fsm.InstanceKey(
            "tenant-01",
            version.strategy_version_id,
            "*",
            "binance:perp:BTCUSDT",
            "LONG",
        ),
        mode="PAPER",
    )
    found: list[signal_domain.Signal] = []
    for frame in scenarios.zone_long_frames():
        found.extend(proc.step(frame, evaluated_at_ms=frame.close_ms).signals)
    return found[0]


def test_dedupe_key_and_id_are_deterministic() -> None:
    """The same scope yields the same key and id."""
    key = signal_domain.dedupe_key(
        tenant_id="t",
        strategy_version_id="s:v1",
        account_scope="*",
        instrument_id="binance:perp:BTCUSDT",
        direction="LONG",
        setup_id="setup-1",
        kind="ENTRY_CONFIRMED",
    )
    assert key == "t|s:v1|*|binance:perp:BTCUSDT|LONG|setup-1|ENTRY_CONFIRMED"
    assert signal_domain.signal_id_for(key) == signal_domain.signal_id_for(key)
    assert signal_domain.signal_id_for(key).startswith("sig-")
    assert len(signal_domain.signal_id_for(key)) == 36


def test_inputs_hash_ignores_provenance() -> None:
    """Live and replayed copies of the same bars hash identically."""
    frames = scenarios.zone_long_frames()[:5]
    replayed = [
        dataclasses.replace(
            f,
            feed_state=quality.FeedState.REPLAY,
            candle=dataclasses.replace(
                f.candle,
                feed_state=quality.FeedState.REPLAY,
                received_at_ms=f.candle.received_at_ms + 99_999,
                source="replay",
            ),
        )
        for f in frames
    ]
    assert signal_domain.inputs_snapshot_hash(
        frames
    ) == signal_domain.inputs_snapshot_hash(replayed)
    last = frames[-1]
    bumped = dataclasses.replace(
        last.candle, base_volume=last.candle.base_volume + 1
    )
    changed = [*frames[:-1], dataclasses.replace(last, candle=bumped)]
    assert signal_domain.inputs_snapshot_hash(
        frames
    ) != signal_domain.inputs_snapshot_hash(changed)


def test_contract_round_trip(
    contract_validate: Callable[[str, str, object], None],
) -> None:
    """Signals serialize to the schema and parse back unchanged."""
    signal = _entry_signal()
    payload = signal.to_contract()
    contract_validate("signal.schema.json", "Signal", payload)
    contract_validate(
        "signal.schema.json", "ReasonNode", payload["reason_tree"]
    )
    assert signal_domain.Signal.from_contract(payload) == signal


def test_severity_mapping_and_rank() -> None:
    """Entries outrank warnings and info."""
    assert signal_domain.SEVERITY_BY_KIND["ENTRY_CONFIRMED"] == "ENTRY"
    assert signal_domain.SEVERITY_BY_KIND["ENTRY_BLOCKED"] == "WARNING"
    assert (
        signal_domain.severity_rank("ENTRY")
        > signal_domain.severity_rank("WARNING")
        > signal_domain.severity_rank("INFO")
    )
