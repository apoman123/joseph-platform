# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Unit tests for the plugin metadata declarations and their rules."""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from typing import Any
from typing import TypeAlias

import pytest

from joseph.indicators import testing
from joseph.indicators.sdk import metadata

Validate: TypeAlias = Callable[[str, str, object], None]
SCHEMA = "indicators.schema.json"
_DECIMAL = metadata.ValueType.DECIMAL


@dataclasses.dataclass(frozen=True)
class _Plugin:
    """Declarations of a fake plugin.

    Attributes:
        plugin_id: Plugin id.
        output_kinds: Declared AOI kinds.
        output_events: Declared event names.
        output_values: Declared values.
        display: Display hints.
    """

    plugin_id: str = "fake"
    output_kinds: tuple[str, ...] = ("ZONE_UP",)
    output_events: tuple[str, ...] = ("SIGNAL_UP",)
    output_values: tuple[metadata.ValueSpec, ...] = (
        metadata.ValueSpec("level", _DECIMAL, "A level.", metadata.Unit.PRICE),
        metadata.ValueSpec(
            "state",
            metadata.ValueType.STRING,
            "A state.",
            choices=("ON", "OFF"),
        ),
    )
    display: metadata.Display = dataclasses.field(
        default_factory=metadata.Display
    )


def _full_display() -> metadata.Display:
    """Builds a display that uses every field.

    Returns:
        A display valid for ``_Plugin()``.
    """
    return metadata.Display(
        layer=True,
        default_visible=True,
        z_index=100,
        show_events=False,
        kinds=(
            metadata.KindStyle(
                "ZONE_UP",
                "Zone",
                "box",
                tone=metadata.Tone.UP,
                color="#00aA11",
                fill_opacity=0.25,
                line_style=metadata.LineStyle.DOTTED,
                midline=True,
                pending_until_ended=True,
            ),
        ),
        events=(
            metadata.EventStyle(
                "SIGNAL_UP",
                "Signal",
                render="marker_and_segment",
                position="below",
                shape="arrow-up",
                tone=metadata.Tone.UP,
                color="#123456",
                time_meta="origin_ms",
                line_style=metadata.LineStyle.DASHED,
            ),
            metadata.EventStyle("AOI_EXPIRED", "Expired", render="none"),
        ),
        plots=(
            metadata.PlotStyle("level", "Level"),
            metadata.PlotStyle(
                "level",
                "Level bars",
                pane="study",
                render="histogram",
                tone=metadata.Tone.SIGN,
                color="#abcdef",
                line_width=4,
            ),
        ),
        panes=(
            metadata.PaneStyle(
                "study",
                "Study",
                precision=8,
                levels=(metadata.PaneLevel(0, "zero"),),
            ),
        ),
        end_reasons=(metadata.EndReasonStyle("broken", "broken"),),
    )


def _problems(**changes: Any) -> list[str]:
    """Validates ``_Plugin`` with some declarations replaced.

    Args:
        **changes: Fields of ``_Plugin`` to replace.

    Returns:
        The validation messages.
    """
    plugin = dataclasses.replace(_Plugin(display=_full_display()), **changes)
    return metadata.validate(plugin)


def test_a_complete_declaration_is_valid(contract_validate: Validate) -> None:
    """Every field in use, within the rules, is valid and matches the schema."""
    plugin = _Plugin(display=_full_display())
    assert metadata.validate(plugin) == []
    contract_validate(SCHEMA, "PluginDisplay", plugin.display.to_contract())
    for spec in plugin.output_values:
        contract_validate(SCHEMA, "ValueSpec", spec.to_contract())


def test_contract_form_of_optional_fields(contract_validate: Validate) -> None:
    """Unset optional style fields are emitted as null and still validate."""
    style = metadata.KindStyle("ZONE_UP", "Zone", "level")
    body = style.to_contract()
    assert body["color"] is None
    assert body["fill_opacity"] is None
    assert body["line_style"] is None
    contract_validate(SCHEMA, "KindStyle", body)
    event = metadata.EventStyle("SIGNAL_UP", "Signal").to_contract()
    assert event["render"] == "marker"
    assert event["time_meta"] is None
    contract_validate(SCHEMA, "EventStyle", event)
    spec = metadata.ValueSpec("level", _DECIMAL, "A level.")
    assert "choices" not in spec.to_contract()
    contract_validate(SCHEMA, "PluginDisplay", metadata.Display().to_contract())


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"plugin_id": "Fake"}, "plugin id"),
        ({"plugin_id": "9fake"}, "plugin id"),
        ({"output_kinds": ("zone_up",)}, "output kind 'zone_up'"),
        ({"output_kinds": ("ZONE_UP", "ZONE_UP")}, "declared twice"),
        ({"output_events": ("SIGNAL UP",)}, "output event"),
        (
            {"output_values": (metadata.ValueSpec("Level", _DECIMAL, "x"),)},
            "key is not lower snake case",
        ),
        (
            {
                "output_values": (
                    metadata.ValueSpec("level", _DECIMAL, "x"),
                    metadata.ValueSpec("level", _DECIMAL, "y"),
                )
            },
            "declared twice",
        ),
        (
            {
                "output_values": (
                    metadata.ValueSpec("level", _DECIMAL, "x", choices=("a",)),
                )
            },
            "choices need type string",
        ),
        (
            {
                "output_values": (
                    metadata.ValueSpec(
                        "level",
                        metadata.ValueType.STRING,
                        "x",
                        choices=tuple(str(i) for i in range(33)),
                    ),
                )
            },
            "more than 32 choices",
        ),
    ],
)
def test_declaration_rules(changes: dict[str, object], message: str) -> None:
    """Ids, kinds, events and values follow their patterns."""
    problems = _problems(display=metadata.Display(), **changes)
    assert any(message in problem for problem in problems), problems


def _display(**changes: Any) -> metadata.Display:
    """Returns the full display with some fields replaced.

    Args:
        **changes: Fields of ``Display`` to replace.

    Returns:
        The display.
    """
    return dataclasses.replace(_full_display(), **changes)


@pytest.mark.parametrize(
    ("display", "message"),
    [
        (_display(z_index=101), "z_index"),
        (
            _display(kinds=(metadata.KindStyle("OTHER", "Other", "box"),)),
            "kind is not in output_kinds",
        ),
        (
            _display(kinds=(metadata.KindStyle("ZONE_UP", "", "box"),)),
            "must be 1-32 characters",
        ),
        (
            _display(kinds=(metadata.KindStyle("ZONE_UP", "x" * 33, "box"),)),
            "must be 1-32 characters",
        ),
        (
            _display(
                kinds=(
                    metadata.KindStyle(
                        "ZONE_UP", "Zone", "box", tone=metadata.Tone.SIGN
                    ),
                )
            ),
            "tone sign is only for histograms",
        ),
        (
            _display(
                kinds=(
                    metadata.KindStyle("ZONE_UP", "Zone", "box", color="red"),
                )
            ),
            "is not #RRGGBB",
        ),
        (
            _display(
                kinds=(
                    metadata.KindStyle(
                        "ZONE_UP", "Zone", "box", fill_opacity=1.5
                    ),
                )
            ),
            "fill_opacity",
        ),
        (
            _display(
                kinds=(metadata.KindStyle("ZONE_UP", "Zone", "circle"),)  # type: ignore[arg-type]  # deliberately invalid render
            ),
            "render 'circle'",
        ),
        (
            _display(events=(metadata.EventStyle("OTHER", "Other"),)),
            "name is not in output_events",
        ),
        (
            _display(
                events=(
                    metadata.EventStyle(
                        "SIGNAL_UP", "Signal", tone=metadata.Tone.SIDE
                    ),
                )
            ),
            "events may not use tone side",
        ),
        (
            _display(
                events=(
                    metadata.EventStyle(
                        "SIGNAL_UP", "Signal", tone=metadata.Tone.SIGN
                    ),
                )
            ),
            "events may not use tone sign",
        ),
        (
            _display(
                events=(
                    metadata.EventStyle(
                        "SIGNAL_UP", "Signal", time_meta="Origin"
                    ),
                )
            ),
            "time_meta",
        ),
        (
            _display(plots=(metadata.PlotStyle("other", "Other"),)),
            "value is not in output_values",
        ),
        (
            _display(plots=(metadata.PlotStyle("level", "L", pane="nope"),)),
            "is not declared",
        ),
        (
            _display(
                plots=(
                    metadata.PlotStyle(
                        "level", "Level", tone=metadata.Tone.SIGN
                    ),
                )
            ),
            "tone sign is only for histograms",
        ),
        (
            _display(
                plots=(metadata.PlotStyle("level", "Level", line_width=5),)
            ),
            "line_width",
        ),
        (
            _display(panes=(metadata.PaneStyle("price", "Price"),)),
            "malformed or reserved",
        ),
        (
            _display(panes=(metadata.PaneStyle("study", "x" * 49),)),
            "title is longer",
        ),
        (
            _display(panes=(metadata.PaneStyle("study", "S", precision=9),)),
            "precision",
        ),
        (
            _display(
                panes=(
                    metadata.PaneStyle(
                        "study",
                        "S",
                        levels=tuple(
                            metadata.PaneLevel(i, str(i)) for i in range(9)
                        ),
                    ),
                )
            ),
            "more than 8 levels",
        ),
        (
            _display(
                panes=(
                    metadata.PaneStyle(
                        "study",
                        "S",
                        levels=(metadata.PaneLevel(1, "x" * 17),),
                    ),
                )
            ),
            "level label",
        ),
        (
            _display(end_reasons=(metadata.EndReasonStyle("Broken", "x"),)),
            "end reason 'Broken'",
        ),
    ],
)
def test_display_rules(display: metadata.Display, message: str) -> None:
    """Styles reference declared outputs and stay within the contract."""
    problems = _problems(display=display)
    assert any(message in problem for problem in problems), problems


def test_reserved_events_need_no_declaration() -> None:
    """A style for a reserved event is valid without declaring it."""
    plugin = testing.ZoneEchoPlugin()
    assert "AOI_EXPIRED" in plugin.output_events
    styled = _display(
        events=(metadata.EventStyle("AOI_INVALIDATED", "Invalidated"),)
    )
    assert _problems(display=styled) == []


def test_display_must_be_a_display_object() -> None:
    """A display of the wrong type is reported, not crashed on."""
    problems = _problems(display={"layer": True})
    assert problems == ["display is not a metadata.Display"]
