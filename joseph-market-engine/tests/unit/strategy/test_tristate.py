"""Exhaustive truth tables for Kleene three-valued logic."""

from __future__ import annotations

import itertools

import pytest

from joseph.strategy.domain import tristate

T = tristate.TriState
_ALL = (T.TRUE, T.FALSE, T.UNKNOWN)

_AND = {
    (T.TRUE, T.TRUE): T.TRUE,
    (T.TRUE, T.FALSE): T.FALSE,
    (T.TRUE, T.UNKNOWN): T.UNKNOWN,
    (T.FALSE, T.TRUE): T.FALSE,
    (T.FALSE, T.FALSE): T.FALSE,
    (T.FALSE, T.UNKNOWN): T.FALSE,
    (T.UNKNOWN, T.TRUE): T.UNKNOWN,
    (T.UNKNOWN, T.FALSE): T.FALSE,
    (T.UNKNOWN, T.UNKNOWN): T.UNKNOWN,
}
_OR = {
    (T.TRUE, T.TRUE): T.TRUE,
    (T.TRUE, T.FALSE): T.TRUE,
    (T.TRUE, T.UNKNOWN): T.TRUE,
    (T.FALSE, T.TRUE): T.TRUE,
    (T.FALSE, T.FALSE): T.FALSE,
    (T.FALSE, T.UNKNOWN): T.UNKNOWN,
    (T.UNKNOWN, T.TRUE): T.TRUE,
    (T.UNKNOWN, T.FALSE): T.UNKNOWN,
    (T.UNKNOWN, T.UNKNOWN): T.UNKNOWN,
}


@pytest.mark.parametrize(("a", "b"), list(itertools.product(_ALL, _ALL)))
def test_conjunction_table(a: tristate.TriState, b: tristate.TriState) -> None:
    """AND follows the Kleene table."""
    assert tristate.all_of([a, b]) is _AND[(a, b)]


@pytest.mark.parametrize(("a", "b"), list(itertools.product(_ALL, _ALL)))
def test_disjunction_table(a: tristate.TriState, b: tristate.TriState) -> None:
    """OR follows the Kleene table."""
    assert tristate.any_of([a, b]) is _OR[(a, b)]


def test_negation_keeps_unknown() -> None:
    """NOT UNKNOWN is UNKNOWN."""
    assert tristate.negate(T.TRUE) is T.FALSE
    assert tristate.negate(T.FALSE) is T.TRUE
    assert tristate.negate(T.UNKNOWN) is T.UNKNOWN


def test_empty_identities() -> None:
    """Empty AND is TRUE and empty OR is FALSE."""
    assert tristate.all_of([]) is T.TRUE
    assert tristate.any_of([]) is T.FALSE


@pytest.mark.parametrize(
    ("values", "k", "expected"),
    [
        ([T.TRUE, T.TRUE, T.FALSE], 2, T.TRUE),
        ([T.TRUE, T.FALSE, T.FALSE], 2, T.FALSE),
        ([T.TRUE, T.UNKNOWN, T.FALSE], 2, T.UNKNOWN),
        ([T.UNKNOWN, T.UNKNOWN, T.UNKNOWN], 1, T.UNKNOWN),
        ([T.FALSE, T.FALSE, T.UNKNOWN], 2, T.FALSE),
    ],
)
def test_k_of_n(
    values: list[tristate.TriState], k: int, expected: tristate.TriState
) -> None:
    """K-of-N is decided only when UNKNOWNs cannot change the outcome."""
    assert tristate.at_least(values, k) is expected


def test_of_maps_optional_booleans() -> None:
    """None maps to UNKNOWN."""
    assert T.of(None) is T.UNKNOWN
    assert T.of(True) is T.TRUE
    assert T.of(False) is T.FALSE
