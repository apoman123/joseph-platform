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


@pytest.fixture(scope="session")
def definitions() -> dict[str, ast.StrategyDefinition]:
    """Returns the Long A and Short B fixture definitions.

    Returns:
        Definitions keyed by strategy id.
    """
    return fixtures.strategy_definitions()


@pytest.fixture
def make_definition() -> Callable[..., ast.StrategyDefinition]:
    """Provides a factory of definitions derived from the Long A fixture.

    Returns:
        ``make(when=None, **top_level_overrides)``, which parses a deep copy
        of Long A's contract with the overrides applied.
    """
    base = fixtures.strategy_definitions()[fixtures.LONG_A].to_contract()

    def make(
        when: object | None = None, **overrides: object
    ) -> ast.StrategyDefinition:
        """Parses Long A's contract with replacements.

        Args:
            when: Replacement ``when`` root node in contract form, or
                ``None`` to keep Long A's.
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
    result = compiler.compile_strategy(definition)
    assert result.plan is not None, result.validation.errors
    return result.plan


@pytest.fixture
def compile_plan() -> Callable[
    [ast.StrategyDefinition], compiler.CompiledStrategy
]:
    """Provides a compiler for definitions that must be valid.

    Returns:
        ``compile(definition) -> plan``, which fails the test with the
        validation errors when the definition does not compile.
    """
    return _compile_valid
