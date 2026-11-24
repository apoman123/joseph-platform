# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Three-valued (Kleene) logic used by the strategy evaluator.

``UNKNOWN`` represents missing, stale or not-yet-warm data. It never
silently becomes ``TRUE`` or ``FALSE``; it decides a result only when the
known operands already do: ``TRUE AND UNKNOWN`` is ``UNKNOWN`` and
``FALSE AND UNKNOWN`` is ``FALSE``, ``TRUE OR UNKNOWN`` is ``TRUE`` and
``FALSE OR UNKNOWN`` is ``UNKNOWN``, and ``NOT UNKNOWN`` is ``UNKNOWN``.
"""

from __future__ import annotations

import enum
from collections.abc import Iterable


class TriState(enum.StrEnum):
    """Kleene truth value (contract ``TriState``)."""

    TRUE = "TRUE"
    FALSE = "FALSE"
    UNKNOWN = "UNKNOWN"

    @classmethod
    def of(cls, value: bool | None) -> TriState:
        """Converts an optional boolean.

        Args:
            value: ``True``, ``False`` or ``None`` (unknown).

        Returns:
            The matching truth value.
        """
        if value is None:
            return cls.UNKNOWN
        return cls.TRUE if value else cls.FALSE


def all_of(values: Iterable[TriState]) -> TriState:
    """Kleene conjunction.

    Args:
        values: Operand truth values.

    Returns:
        FALSE if any operand is FALSE, else UNKNOWN if any is UNKNOWN,
        else TRUE (TRUE for an empty input).
    """
    result = TriState.TRUE
    for value in values:
        if value is TriState.FALSE:
            return TriState.FALSE
        if value is TriState.UNKNOWN:
            result = TriState.UNKNOWN
    return result


def any_of(values: Iterable[TriState]) -> TriState:
    """Kleene disjunction.

    Args:
        values: Operand truth values.

    Returns:
        TRUE if any operand is TRUE, else UNKNOWN if any is UNKNOWN, else
        FALSE (FALSE for an empty input).
    """
    result = TriState.FALSE
    for value in values:
        if value is TriState.TRUE:
            return TriState.TRUE
        if value is TriState.UNKNOWN:
            result = TriState.UNKNOWN
    return result


def negate(value: TriState) -> TriState:
    """Kleene negation.

    Args:
        value: Operand.

    Returns:
        The negation; ``UNKNOWN`` stays ``UNKNOWN``.
    """
    if value is TriState.TRUE:
        return TriState.FALSE
    if value is TriState.FALSE:
        return TriState.TRUE
    return TriState.UNKNOWN


def at_least(values: Iterable[TriState], k: int) -> TriState:
    """K-of-N threshold over truth values.

    Args:
        values: Operand truth values.
        k: Minimum number of TRUE operands.

    Returns:
        TRUE when at least ``k`` are TRUE, FALSE when even treating every
        UNKNOWN as TRUE cannot reach ``k``, otherwise UNKNOWN.
    """
    items = list(values)
    true_count = sum(1 for v in items if v is TriState.TRUE)
    unknown_count = sum(1 for v in items if v is TriState.UNKNOWN)
    if true_count >= k:
        return TriState.TRUE
    if true_count + unknown_count < k:
        return TriState.FALSE
    return TriState.UNKNOWN
