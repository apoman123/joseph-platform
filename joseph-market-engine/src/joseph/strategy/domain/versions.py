# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Immutable strategy versions and their lifecycle metadata."""

from __future__ import annotations

import dataclasses
import enum

from joseph.strategy.domain import ast


class StrategyState(enum.StrEnum):
    """Lifecycle of a stored version (contract ``StrategyState``)."""

    DRAFT = "DRAFT"
    VALIDATED = "VALIDATED"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    ARCHIVED = "ARCHIVED"
    UNSUPPORTED = "UNSUPPORTED"


@dataclasses.dataclass(frozen=True, slots=True)
class Activation:
    """How a version is activated (contract ``Activation``).

    Attributes:
        mode: ``ALERT_ONLY``, ``PAPER`` or ``SHADOW``.
        activated_at_ms: Activation time.
        instruments: Optional explicit instrument list.
    """

    mode: str
    activated_at_ms: int
    instruments: tuple[str, ...] = ()

    def to_contract(self) -> dict[str, object]:
        """Serializes to the contract object.

        Returns:
            JSON-compatible dict.
        """
        return {
            "mode": self.mode,
            "activated_at_ms": self.activated_at_ms,
            "instruments": list(self.instruments),
        }


def version_id(strategy_id: str, version: int) -> str:
    """Formats a strategy version id.

    Args:
        strategy_id: Strategy slug.
        version: Version number (>= 1).

    Returns:
        Text such as ``discount-ote-reversal:v3``.
    """
    return f"{strategy_id}:v{version}"


@dataclasses.dataclass(frozen=True, slots=True)
class StrategyVersion:
    """Immutable definition plus mutable lifecycle metadata.

    The definition and its hash never change after creation; only
    ``state`` and ``activation`` move through the lifecycle, each change
    making a new copy (the record itself is frozen).

    Attributes:
        strategy_id: Strategy slug.
        version: Version number.
        tenant_id: Owning tenant.
        author_user_id: Author.
        created_at_ms: Creation time.
        definition: Validated definition.
        definition_hash: Canonical hash (``rule_version``).
        compiler_version: Compiler semantics version at creation.
        evaluator_version: Evaluator semantics version at creation.
        state: Lifecycle state.
        activation: Activation metadata when active or paused.
    """

    strategy_id: str
    version: int
    tenant_id: str
    author_user_id: str
    created_at_ms: int
    definition: ast.StrategyDefinition
    definition_hash: str
    compiler_version: str
    evaluator_version: str
    state: StrategyState
    activation: Activation | None = None

    @property
    def strategy_version_id(self) -> str:
        """The version id, ``{strategy_id}:v{version}``."""
        return version_id(self.strategy_id, self.version)

    def to_contract(self) -> dict[str, object]:
        """Serializes to the contract ``StrategyVersion`` object.

        Returns:
            JSON-compatible dict.
        """
        return {
            "strategy_version_id": self.strategy_version_id,
            "strategy_id": self.strategy_id,
            "version": self.version,
            "tenant_id": self.tenant_id,
            "author_user_id": self.author_user_id,
            "created_at_ms": self.created_at_ms,
            "definition": self.definition.to_contract(),
            "definition_hash": self.definition_hash,
            "compiler_version": self.compiler_version,
            "evaluator_version": self.evaluator_version,
            "state": self.state.value,
            "activation": None
            if self.activation is None
            else self.activation.to_contract(),
        }
