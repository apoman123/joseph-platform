# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Unit tests for quote-rule, tick-test and Lee-Ready classification."""

from __future__ import annotations

import decimal

import pytest

from joseph.domain import market
from joseph.market_data.domain import classification

_D = decimal.Decimal
_BUY = market.AggressorSide.BUY
_SELL = market.AggressorSide.SELL
_UNKNOWN = market.AggressorSide.UNKNOWN


@pytest.mark.parametrize(
    ("bid", "ask", "valid"),
    [
        (_D("10.00"), _D("10.02"), True),
        (_D("10.00"), _D("10.00"), False),
        (_D("10.02"), _D("10.00"), False),
        (_D("0"), _D("10.00"), False),
        (None, _D("10.00"), False),
        (_D("10.00"), None, False),
    ],
)
def test_valid_quote_needs_two_uncrossed_sides(
    bid: decimal.Decimal | None, ask: decimal.Decimal | None, valid: bool
) -> None:
    """Missing, zero, locked and crossed quotes are invalid."""
    assert classification.valid_quote(bid, ask) is valid


@pytest.mark.parametrize(
    ("price", "side"),
    [
        (_D("10.02"), _BUY),
        (_D("10.015"), _BUY),
        (_D("10.01"), _UNKNOWN),
        (_D("10.005"), _SELL),
        (_D("9.99"), _SELL),
    ],
)
def test_quote_rule_compares_with_the_midpoint(
    price: decimal.Decimal, side: market.AggressorSide
) -> None:
    """Above the midpoint is a buy, below a sell, at it unclassified."""
    assert classification.quote_rule(price, _D("10.00"), _D("10.02")) is side


def test_quote_rule_without_valid_quote_is_unknown() -> None:
    """A locked quote classifies nothing."""
    assert classification.quote_rule(_D(5), _D(5), _D(5)) is _UNKNOWN


@pytest.mark.parametrize(
    ("price", "reference", "side"),
    [
        (_D("10.01"), _D("10.00"), _BUY),
        (_D("9.99"), _D("10.00"), _SELL),
        (_D("10.00"), _D("10.00"), _UNKNOWN),
        (_D("10.00"), None, _UNKNOWN),
    ],
)
def test_tick_test_compares_with_the_last_different_price(
    price: decimal.Decimal,
    reference: decimal.Decimal | None,
    side: market.AggressorSide,
) -> None:
    """Upticks buy, downticks sell, no reference is unknown."""
    assert classification.tick_test(price, reference) is side


def test_lee_ready_uses_quote_rule_then_tick_test() -> None:
    """Off-midpoint trades use the quote rule; midpoint trades the tick test."""
    bid, ask = _D("10.00"), _D("10.02")
    assert classification.lee_ready(_D("10.02"), bid, ask, _D("10.05")) is _BUY
    assert classification.lee_ready(_D("10.01"), bid, ask, _D("10.00")) is _BUY
    assert classification.lee_ready(_D("10.01"), bid, ask, _D("10.02")) is _SELL
    assert classification.lee_ready(_D("10.01"), None, None, None) is _UNKNOWN


def test_tick_tracker_remembers_zero_ticks() -> None:
    """Zero ticks keep the previous different price as the reference."""
    ticks = classification.TickTracker()
    assert ticks.reference(_D(10)) is None
    ticks.observe(_D(10))
    assert ticks.reference(_D(10)) is None
    assert ticks.reference(_D(11)) == _D(10)
    ticks.observe(_D(11))
    ticks.observe(_D(11))
    assert ticks.reference(_D(11)) == _D(10)
    assert ticks.reference(_D(12)) == _D(11)


def test_classifier_counts_rules_and_resets() -> None:
    """The classifier counts each rule's trades; reset forgets the last tick."""
    classifier = classification.LeeReadyClassifier()
    bid, ask = _D("10.00"), _D("10.02")
    assert classifier.classify(_D("10.01"), bid, ask) is _UNKNOWN
    assert classifier.classify(_D("10.02"), bid, ask) is _BUY
    assert classifier.classify(_D("10.01"), bid, ask) is _SELL
    assert classifier.classify(_D("10.01"), None, None) is _SELL
    assert (classifier.by_quote, classifier.by_tick) == (1, 2)
    assert classifier.unclassified == 1
    classifier.reset()
    assert classifier.classify(_D("10.01"), None, None) is _UNKNOWN
