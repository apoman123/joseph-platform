# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Five independent script roles and non-executing source validation tests."""

from __future__ import annotations

import inspect

import pytest

import joseph_script
from joseph.domain import errors
from joseph.scripts import models
from joseph.scripts import worker


@pytest.mark.parametrize(
    "kind,base,method",
    [
        ("indicator", "Indicator", "on_bar"),
        ("strategy", "Strategy", "on_bar"),
        ("library", "Library", "exports"),
        ("filter", "Filter", "matches"),
        ("alert", "Alert", "on_bar"),
    ],
)
def test_five_roles_require_independent_abstract_bases(
    kind: str, base: str, method: str
) -> None:
    """Accepts one correctly inherited concrete class for each role.

    Args:
        kind: Public script kind.
        base: Its independent abstract base class.
        method: Abstract method that concrete implementations must override.

    Returns:
        None after checking abstractness and non-executing validation.
    """
    abstract = getattr(joseph_script, base)
    assert inspect.isabstract(abstract)
    with pytest.raises(TypeError):
        abstract()
    args = "self" if kind == "library" else "self, ctx"
    source = (
        f"from joseph_script import {base}\n"
        f"class Example({base}):\n    def {method}({args}):\n"
        "        return True\n"
    )
    script = models.ScriptInput.model_validate(
        {
            "script_id": "example",
            "kind": kind,
            "name": "Example",
            "source": source,
        }
    )
    models.validate_source(script)


@pytest.mark.parametrize(
    "source",
    [
        "def run(ctx): pass",
        "class Wrong: pass",
        (
            "from joseph_script import Strategy\nclass "
            "Wrong(Strategy):\n def on_bar(self, ctx): pass"
        ),
        "from joseph_script import Indicator\nclass Wrong(Indicator): pass",
    ],
)
def test_script_must_inherit_and_implement_its_selected_role(
    source: str,
) -> None:
    """Rejects generic functions, wrong bases and incomplete subclasses.

    Args:
        source: Invalid indicator declaration.

    Returns:
        None after a structural validation failure without executing source.
    """
    script = models.ScriptInput(
        script_id="example", kind="indicator", name="Example", source=source
    )
    with pytest.raises(errors.ValidationFailedError):
        models.validate_source(script)


def test_indicator_cannot_create_simulated_orders() -> None:
    """Keeps indicator calculations independent of even simulated trading."""
    context = worker.Context({"kind": "indicator"})
    context.advance(
        {
            "time_ms": 0,
            "open": 100,
            "high": 101,
            "low": 99,
            "close": 100,
            "volume": 1,
        }
    )
    with pytest.raises(ValueError, match="strategy"):
        context.entry("long")
    with pytest.raises(ValueError, match="strategy"):
        context.close()


def test_simulated_fill_uses_following_open_and_reports_entry_exit() -> None:
    """Uses actual next-bar fill prices, slippage and fees."""
    context = worker.Context(
        {
            "kind": "strategy",
            "initial_equity": 10000,
            "commission_bps": 10,
            "slippage_bps": 10,
        }
    )
    context.advance(
        {
            "time_ms": 0,
            "open": 100,
            "high": 101,
            "low": 99,
            "close": 100,
            "volume": 1,
        }
    )
    context.entry("long", 2)
    assert not context.fills
    context.advance(
        {
            "time_ms": 1000,
            "open": 110,
            "high": 112,
            "low": 109,
            "close": 111,
            "volume": 2,
        }
    )
    assert context.fills[0]["time_ms"] == 1000
    assert context.fills[0]["price"] == pytest.approx(110.11)
    assert context.fills[0]["effect"] == "entry"
    context.close()
    context.advance(
        {
            "time_ms": 2000,
            "open": 120,
            "high": 122,
            "low": 119,
            "close": 121,
            "volume": 3,
        }
    )
    assert context.fills[-1]["effect"] == "exit"
    assert context.fills[-1]["time_ms"] == 2000
    assert context.position_size == 0


def test_alert_conditions_are_closed_bar_only_and_plain_text() -> None:
    """Records named conditions on confirmed bars without placing orders."""
    context = worker.Context({"kind": "alert"})
    context.advance(
        {"time_ms": 0, "open": 100, "close": 101, "is_final": False}
    )
    context.alert("not closed")
    assert not context.alerts
    context.advance(
        {"time_ms": 1000, "open": 101, "close": 102, "is_final": True}
    )
    context.alert("crossed 100", name="threshold", severity="WARNING")
    assert context.alerts == [
        {
            "time_ms": 1000,
            "name": "threshold",
            "message": "crossed 100",
            "severity": "WARNING",
        }
    ]
