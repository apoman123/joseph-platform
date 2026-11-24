# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Loads the shared contract fixtures used by strategy tests."""

from __future__ import annotations

import json
import os
import pathlib

from joseph.strategy.domain import ast
from joseph.strategy.domain import compiler
from joseph.strategy.domain import evaluator
from joseph.strategy.domain import versions


def contracts_root() -> pathlib.Path:
    """Locates ``packages/contracts`` of the public terminal.

    Returns:
        Directory containing ``schemas/`` and ``fixtures/``; honors
        ``$JOSEPH_CONTRACTS_DIR`` (which points at ``schemas/``).
    """
    override = os.environ.get("JOSEPH_CONTRACTS_DIR")
    if override:
        return pathlib.Path(override).resolve().parent
    return (
        pathlib.Path(__file__).resolve().parents[5]
        / "joseph-terminal"
        / "packages"
        / "contracts"
    )


def load_fixture(name: str) -> dict[str, object]:
    """Reads ``fixtures/{name}.fixture.json``.

    Args:
        name: Fixture name such as ``strategy``.

    Returns:
        Parsed fixture with ``valid`` and ``invalid`` lists.
    """
    path = contracts_root() / "fixtures" / f"{name}.fixture.json"
    data: dict[str, object] = json.loads(path.read_text(encoding="utf-8"))
    return data


def strategy_definitions() -> dict[str, ast.StrategyDefinition]:
    """Parses the valid strategy fixtures keyed by strategy id.

    Returns:
        ``{"discount-ote-reversal": Long A, "premium-ote-short": Short B}``.
    """
    valid = load_fixture("strategy")["valid"]
    assert isinstance(valid, list)
    parsed = [ast.parse_definition(item) for item in valid]
    return {definition.strategy_id: definition for definition in parsed}


LONG_A = "discount-ote-reversal"
"""Strategy id of the Long A fixture."""

SHORT_B = "premium-ote-short"
"""Strategy id of the Short B fixture."""


def make_version(
    definition: ast.StrategyDefinition,
    *,
    tenant_id: str = "tenant-01",
    user_id: str = "user-01",
    version: int = 1,
    mode: str = "PAPER",
    state: versions.StrategyState = versions.StrategyState.ACTIVE,
) -> versions.StrategyVersion:
    """Builds an in-memory strategy version for tests.

    Args:
        definition: Definition.
        tenant_id: Tenant.
        user_id: Author.
        version: Version number.
        mode: Activation mode.
        state: Lifecycle state.

    Returns:
        The version.
    """
    return versions.StrategyVersion(
        strategy_id=definition.strategy_id,
        version=version,
        tenant_id=tenant_id,
        author_user_id=user_id,
        created_at_ms=0,
        definition=definition,
        definition_hash=definition.definition_hash(),
        compiler_version=compiler.COMPILER_VERSION,
        evaluator_version=evaluator.EVALUATOR_VERSION,
        state=state,
        activation=versions.Activation(mode=mode, activated_at_ms=0),
    )
