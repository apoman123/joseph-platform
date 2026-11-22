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
    """Builds definitions from the Long A fixture with overrides.

    Returns:
        ``make(when=None, **top_level_overrides)``.
    """
    base = fixtures.strategy_definitions()[fixtures.LONG_A].to_contract()

    def make(
        when: object | None = None, **overrides: object
    ) -> ast.StrategyDefinition:
        """Creates a definition.

        Args:
            when: Replacement root node (dict) or None.
            **overrides: Top-level field replacements.

        Returns:
            Parsed definition.
        """
        raw = copy.deepcopy(base)
        if when is not None:
            raw["when"] = when
        raw.update(overrides)
        return ast.parse_definition(raw)

    return make


@pytest.fixture
def compile_plan() -> Callable[
    [ast.StrategyDefinition], compiler.CompiledStrategy
]:
    """Compiles a definition that must be valid.

    Returns:
        ``compile(definition) -> plan``.
    """

    def compile_valid(
        definition: ast.StrategyDefinition,
    ) -> compiler.CompiledStrategy:
        """Compiles and asserts validity.

        Args:
            definition: Definition.

        Returns:
            Plan.
        """
        result = compiler.compile_strategy(definition)
        assert result.plan is not None, result.validation.errors
        return result.plan

    return compile_valid
