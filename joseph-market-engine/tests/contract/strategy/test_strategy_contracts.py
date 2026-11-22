"""Contract tests: strategy-side outputs conform to the JSON Schemas."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from joseph.backtest import simulator
from joseph.strategy.app import processor
from joseph.strategy.domain import compiler
from joseph.strategy.domain import setup_fsm
from joseph.strategy.testing import fixtures
from joseph.strategy.testing import scenarios

Validate = Callable[[str, str, object], None]


@pytest.mark.parametrize("strategy_id", [fixtures.LONG_A, fixtures.SHORT_B])
def test_definitions_round_trip_through_schema(
    contract_validate: Validate,
    strategy_id: str,
) -> None:
    """Canonical definitions (with defaults) still validate."""
    definition = fixtures.strategy_definitions()[strategy_id]
    contract_validate(
        "strategy.schema.json", "StrategyDefinition", definition.to_contract()
    )


def test_strategy_version_contract(contract_validate: Validate) -> None:
    """Stored versions serialize to the StrategyVersion schema."""
    for definition in fixtures.strategy_definitions().values():
        contract_validate(
            "strategy.schema.json",
            "StrategyVersion",
            fixtures.make_version(definition).to_contract(),
        )


def test_validation_result_with_errors(contract_validate: Validate) -> None:
    """Failing validation results also conform."""
    definition = fixtures.strategy_definitions()[fixtures.LONG_A]
    result = compiler.compile_strategy(
        definition, available_plugins=set(), unsupported_ops={"BIAS"}
    )
    assert not result.validation.valid
    contract_validate(
        "strategy.schema.json",
        "ValidationResult",
        result.validation.to_contract(),
    )


@pytest.mark.parametrize(
    "options",
    [
        scenarios.WorldOptions(),
        scenarios.WorldOptions(target=False),
    ],
)
def test_signal_kinds_conform(
    contract_validate: Validate, options: scenarios.WorldOptions
) -> None:
    """Entry and blocked signals with reason trees conform."""
    definition = fixtures.strategy_definitions()[fixtures.LONG_A]
    plan = compiler.compile_strategy(definition).plan
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
    emitted = [
        s
        for f in scenarios.long_a_frames(options)
        for s in proc.step(f, evaluated_at_ms=f.close_ms).signals
    ]
    assert emitted
    for signal in emitted:
        contract_validate("signal.schema.json", "Signal", signal.to_contract())


def test_empty_metrics_conform(contract_validate: Validate) -> None:
    """Metrics without trades serialize with nulls where undefined."""
    contract_validate(
        "strategy.schema.json",
        "BacktestMetrics",
        simulator.compute_metrics([]).to_contract(),
    )
