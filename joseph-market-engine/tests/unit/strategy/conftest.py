# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Fixtures shared by the strategy unit tests."""

from __future__ import annotations

import copy
from collections.abc import Callable

import pytest

from joseph.strategy.domain import ast
from joseph.strategy.domain import compiler
from joseph.strategy.testing import fixtures
from joseph.strategy.testing import scenarios
from joseph.strategy.testing import vocabulary


@pytest.fixture(scope="session")
def definitions() -> dict[str, ast.StrategyDefinition]:
    """Returns the example template definitions of the contract fixture.

    Returns:
        Definitions keyed by strategy id.
    """
    return fixtures.strategy_definitions()


@pytest.fixture
def make_definition() -> Callable[..., ast.StrategyDefinition]:
    """Provides a factory of definitions derived from the zone-touch rule.

    Returns:
        ``make(when=None, **top_level_overrides)``, which parses a deep copy
        of ``scenarios.ZONE_LONG`` with the overrides applied.
    """
    base = scenarios.zone_long_payload()

    def make(
        when: object | None = None, **overrides: object
    ) -> ast.StrategyDefinition:
        """Parses the zone-touch rule with replacements.

        Args:
            when: Replacement ``when`` root node in contract form, or
                ``None`` to keep the rule's own.
            **overrides: Top-level contract fields to replace.

        Returns:
            The parsed definition.

        Raises:
            pydantic.ValidationError: The patched contract violates the
                schema.
        """
        raw = copy.deepcopy(base)
        if when is not None:
            raw["when"] = when
        raw.update(overrides)
        return ast.parse_definition(raw)

    return make


def _compile_valid(
    definition: ast.StrategyDefinition,
) -> compiler.CompiledStrategy:
    """Compiles a definition and fails the test if it is invalid.

    Args:
        definition: Definition that must compile.

    Returns:
        The compiled plan.
    """
    result = compiler.compile_strategy(
        definition, vocabulary=vocabulary.VOCABULARY
    )
    assert result.plan is not None, result.validation.errors
    return result.plan


@pytest.fixture
def compile_plan() -> Callable[
    [ast.StrategyDefinition], compiler.CompiledStrategy
]:
    """Provides a compiler for definitions that must be valid.

    Returns:
        ``compile(definition) -> plan``, which compiles against the generic
        test vocabulary and fails the test with the validation errors when
        the definition does not compile.
    """
    return _compile_valid
