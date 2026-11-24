# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Unit tests for the Rule AST v2 models and canonical hashing."""

from __future__ import annotations

import copy
import json

import pydantic
import pytest

from joseph.strategy.domain import ast
from joseph.strategy.testing import fixtures


def _raw_long_a() -> dict[str, object]:
    """Returns a deep copy of the Long A fixture payload.

    Returns:
        JSON-compatible definition.
    """
    valid = fixtures.load_fixture("strategy")["valid"]
    assert isinstance(valid, list)
    return copy.deepcopy(valid[0])


def test_fixtures_parse_and_round_trip() -> None:
    """Every valid fixture parses and re-parses to an equal model."""
    for definition in fixtures.strategy_definitions().values():
        again = ast.parse_definition(definition.to_contract())
        assert again == definition
        assert again.definition_hash() == definition.definition_hash()


def test_invalid_fixtures_are_rejected() -> None:
    """Invalid fixtures, including an EVAL code node, fail validation."""
    invalid = fixtures.load_fixture("strategy")["invalid"]
    assert isinstance(invalid, list)
    for payload in invalid:
        with pytest.raises(pydantic.ValidationError):
            ast.parse_definition(payload)


def test_decimals_must_be_strings() -> None:
    """Float decimals are rejected so no binary rounding enters rules."""
    raw = _raw_long_a()
    risk = raw["risk"]
    assert isinstance(risk, dict)
    risk["min_rr"] = 2.0
    with pytest.raises(pydantic.ValidationError):
        ast.parse_definition(raw)


def test_unknown_fields_are_rejected() -> None:
    """Misspelled fields fail instead of being ignored."""
    raw = _raw_long_a()
    raw["when"] = {
        "op": "ALL",
        "children": [
            {
                "op": "EVENT",
                "name": "SSL_SWEEP",
                "within_bars": 3,
                "withn_bars": 9,
            },
        ],
    }
    with pytest.raises(pydantic.ValidationError):
        ast.parse_definition(raw)


def test_booleans_and_integers_are_strict() -> None:
    """Truthy strings are not booleans and strings are not integers."""
    raw = _raw_long_a()
    notification = raw["notification"]
    assert isinstance(notification, dict)
    notification["once_per_aoi"] = "yes"
    with pytest.raises(pydantic.ValidationError):
        ast.parse_definition(raw)
    raw = _raw_long_a()
    notification = raw["notification"]
    assert isinstance(notification, dict)
    notification["cooldown_bars"] = "12"
    with pytest.raises(pydantic.ValidationError):
        ast.parse_definition(raw)


def test_defaults_do_not_change_the_hash() -> None:
    """Spelling out schema defaults yields the same canonical hash."""
    raw = _raw_long_a()
    explicit = copy.deepcopy(raw)
    when = explicit["when"]
    assert isinstance(when, dict)
    children = when["children"]
    assert isinstance(children, list)
    children[0]["require_bos"] = False
    children[3]["require_active"] = True
    assert (
        ast.parse_definition(raw).definition_hash()
        == ast.parse_definition(explicit).definition_hash()
    )


def test_any_change_changes_the_hash() -> None:
    """Changing trading meaning changes the hash."""
    raw = _raw_long_a()
    changed = copy.deepcopy(raw)
    risk = changed["risk"]
    assert isinstance(risk, dict)
    risk["min_rr"] = "2.5"
    assert (
        ast.parse_definition(raw).definition_hash()
        != ast.parse_definition(changed).definition_hash()
    )


def test_canonical_json_is_sorted_and_normalized() -> None:
    """Canonical JSON sorts keys and normalizes decimals."""
    definition = ast.parse_definition(_raw_long_a())
    text = definition.canonical_json()
    parsed = json.loads(text)
    assert list(parsed) == sorted(parsed)
    assert parsed["risk"]["min_rr"] == "2"
    assert definition.definition_hash().startswith("sha256:")
    assert len(definition.definition_hash()) == len("sha256:") + 64


def test_defaults_are_materialized() -> None:
    """Optional policy objects take their schema defaults."""
    raw = _raw_long_a()
    raw.pop("setup")
    raw.pop("data_policy")
    definition = ast.parse_definition(raw)
    assert definition.setup.expire_after_bars == 24
    assert definition.data_policy.max_data_age_ms == 120_000
    assert definition.data_policy.allow_rest_poll is True


def test_strategy_id_pattern_is_enforced() -> None:
    """Strategy ids are lowercase slugs."""
    raw = _raw_long_a()
    raw["strategy_id"] = "Long A"
    with pytest.raises(pydantic.ValidationError):
        ast.parse_definition(raw)


def test_models_are_immutable() -> None:
    """Parsed definitions cannot be mutated in place."""
    definition = ast.parse_definition(_raw_long_a())
    with pytest.raises(pydantic.ValidationError):
        definition.name = "changed"  # type: ignore[misc]
