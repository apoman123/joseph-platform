# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""What a plugin publishes and how the terminal displays it.

Every plugin declares, next to its settings, the AOI kinds and event names
it can publish, the scalar values of its snapshots (``ValueSpec``) and its
display hints (``Display``). The registry rejects a plugin whose
declarations break the rules ``validate`` checks, so the terminal and the
strategy compiler can build their vocabulary from the contract
``IndicatorPluginInfo`` alone.

The dataclasses mirror the contract definitions of the same names in
``indicators.schema.json``; ``to_contract`` emits every field, with ``None``
as ``null``.
"""

from __future__ import annotations

import dataclasses
import enum
import math
import re
from collections.abc import Sequence
from typing import Literal
from typing import Protocol
from typing import TypeAlias

from joseph.domain import structures

KIND_PATTERN = structures.NAME_PATTERN
"""Pattern of AOI kinds and event names (contract ``AoiKind``)."""

VALUE_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
"""Pattern of published value keys and ``EventStyle.time_meta``."""

PLUGIN_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
"""Pattern of plugin ids (contract ``PluginId``)."""

PANE_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
"""Pattern of study pane ids; ``PRICE_PANE`` is reserved."""

END_REASON_PATTERN = structures.END_REASON_PATTERN
"""Pattern of AOI end reasons (contract ``EndReason``)."""

_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
"""Pattern of default colours (contract ``HexColor``)."""

PRICE_PANE = "price"
"""Pane id of the price chart; plugins may not declare it."""

MAX_LABEL = 32
"""Maximum length of kind, event, plot and end-reason labels."""

MAX_LEVEL_LABEL = 16
"""Maximum length of a pane level label."""

MAX_PANE_TITLE = 48
"""Maximum length of a pane title."""

MAX_CHOICES = 32
"""Maximum number of choices of a string value."""

MAX_PANE_LEVELS = 8
"""Maximum number of reference levels per pane."""

KindRender: TypeAlias = Literal["box", "level"]
"""How AOIs of a kind are drawn: a filled box or a line at ``lower``."""

EventRender: TypeAlias = Literal[
    "marker", "segment", "marker_and_segment", "none"
]
"""How events of a name are drawn."""

PlotRender: TypeAlias = Literal["line", "histogram"]
"""How a plotted value is drawn."""

MarkerPosition: TypeAlias = Literal["above", "below", "at"]
"""Where an event marker sits relative to its bar."""

MarkerShape: TypeAlias = Literal["arrow-up", "arrow-down", "circle", "square"]
"""Shape of an event marker."""

_KIND_RENDERS = frozenset({"box", "level"})
_EVENT_RENDERS = frozenset({"marker", "segment", "marker_and_segment", "none"})
_PLOT_RENDERS = frozenset({"line", "histogram"})
_POSITIONS = frozenset({"above", "below", "at"})
_SHAPES = frozenset({"arrow-up", "arrow-down", "circle", "square"})


class ValueType(enum.StrEnum):
    """JSON form of a published value (contract ``ValueSpec.type``)."""

    DECIMAL = "decimal"
    INTEGER = "integer"
    BOOLEAN = "boolean"
    STRING = "string"
    TIMESTAMP_MS = "timestamp_ms"


class Unit(enum.StrEnum):
    """Unit of a published value (contract ``ValueSpec.unit``)."""

    PRICE = "price"
    PERCENT = "percent"
    RATIO = "ratio"
    VOLUME = "volume"
    BARS = "bars"
    NONE = "none"


class Tone(enum.StrEnum):
    """Colour role resolved by the terminal theme (``DisplayTone``).

    ``SIDE`` follows the AOI side and ``SIGN`` the sign of the plotted value
    (histograms only); events may use neither.
    """

    SIDE = "side"
    SIGN = "sign"
    UP = "up"
    DOWN = "down"
    NEUTRAL = "neutral"
    ACCENT = "accent"
    WARNING = "warning"
    MUTED = "muted"


class LineStyle(enum.StrEnum):
    """Stroke of a drawn line (contract ``LineStyle``)."""

    SOLID = "solid"
    DASHED = "dashed"
    DOTTED = "dotted"


_TONES = frozenset(Tone)
_LINE_STYLES = frozenset(LineStyle)


def _enum_value(value: object) -> object:
    """Returns an enum's value, other values unchanged.

    Args:
        value: Field value.

    Returns:
        JSON-compatible value.
    """
    return value.value if isinstance(value, enum.Enum) else value


@dataclasses.dataclass(frozen=True, slots=True)
class ValueSpec:
    """One value a plugin publishes in its snapshots and series.

    Every published value may be ``None`` (for example before warm-up).

    Attributes:
        key: Key in ``IndicatorSnapshot.values``.
        type: JSON form of the value.
        description: Meaning of the value.
        unit: Unit of the value.
        choices: Closed set of values of a string value; any string when
            empty.
    """

    key: str
    type: ValueType
    description: str
    unit: Unit = Unit.NONE
    choices: tuple[str, ...] = ()

    def to_contract(self) -> dict[str, object]:
        """Serializes to the contract ``ValueSpec`` object.

        Returns:
            JSON-compatible dict; ``choices`` only when there are some.
        """
        body: dict[str, object] = {
            "key": self.key,
            "type": _enum_value(self.type),
            "unit": _enum_value(self.unit),
            "description": self.description,
        }
        if self.choices:
            body["choices"] = list(self.choices)
        return body


@dataclasses.dataclass(frozen=True, slots=True)
class KindStyle:
    """How the terminal draws AOIs of one kind.

    Attributes:
        kind: AOI kind declared in ``output_kinds``.
        label: Short label, 1 to 32 characters.
        render: ``box`` fills ``[lower, upper]``; ``level`` draws a line at
            ``lower``.
        tone: Colour role (not ``SIGN``).
        color: Default colour ``#RRGGBB``; user colours take precedence.
        fill_opacity: Box fill opacity, 0 to 1 (terminal default if None).
        line_style: Stroke (terminal default if None).
        midline: Whether a box shows its midline.
        pending_until_ended: Draws live AOIs as pending until they end.
    """

    kind: str
    label: str
    render: KindRender
    tone: Tone = Tone.SIDE
    color: str | None = None
    fill_opacity: float | None = None
    line_style: LineStyle | None = None
    midline: bool = False
    pending_until_ended: bool = False

    def to_contract(self) -> dict[str, object]:
        """Serializes to the contract ``KindStyle`` object.

        Returns:
            JSON-compatible dict.
        """
        return {
            "kind": self.kind,
            "label": self.label,
            "render": self.render,
            "tone": _enum_value(self.tone),
            "color": self.color,
            "fill_opacity": self.fill_opacity,
            "line_style": _enum_value(self.line_style),
            "midline": self.midline,
            "pending_until_ended": self.pending_until_ended,
        }


@dataclasses.dataclass(frozen=True, slots=True)
class EventStyle:
    """How the terminal draws events of one name.

    Attributes:
        name: Event name declared in ``output_events`` or reserved.
        label: Short label, 1 to 32 characters.
        render: Marker on the bar, segment at ``price``, both, or nothing.
        position: Marker position relative to the bar.
        shape: Marker shape.
        tone: Colour role (neither ``SIDE`` nor ``SIGN``).
        color: Default colour ``#RRGGBB``; user colours take precedence.
        time_meta: ``meta`` key holding a UTC ms time: markers sit on that
            bar and segments start there.
        line_style: Segment stroke (terminal default if None).
    """

    name: str
    label: str
    render: EventRender = "marker"
    position: MarkerPosition = "above"
    shape: MarkerShape = "circle"
    tone: Tone = Tone.NEUTRAL
    color: str | None = None
    time_meta: str | None = None
    line_style: LineStyle | None = None

    def to_contract(self) -> dict[str, object]:
        """Serializes to the contract ``EventStyle`` object.

        Returns:
            JSON-compatible dict.
        """
        return {
            "name": self.name,
            "label": self.label,
            "render": self.render,
            "position": self.position,
            "shape": self.shape,
            "tone": _enum_value(self.tone),
            "color": self.color,
            "time_meta": self.time_meta,
            "line_style": _enum_value(self.line_style),
        }


@dataclasses.dataclass(frozen=True, slots=True)
class PlotStyle:
    """One published value drawn as a line or a histogram.

    Attributes:
        value: Key declared in ``output_values``.
        label: Short label, 1 to 32 characters.
        pane: ``PRICE_PANE`` or the id of a declared ``PaneStyle``.
        render: Line or histogram.
        tone: Colour role (``SIGN`` only for histograms; not ``SIDE``).
        color: Default colour ``#RRGGBB``; user colours take precedence.
        line_style: Stroke of a line.
        line_width: Line width in pixels, 1 to 4.
    """

    value: str
    label: str
    pane: str = PRICE_PANE
    render: PlotRender = "line"
    tone: Tone = Tone.ACCENT
    color: str | None = None
    line_style: LineStyle = LineStyle.SOLID
    line_width: int = 1

    def to_contract(self) -> dict[str, object]:
        """Serializes to the contract ``PlotStyle`` object.

        Returns:
            JSON-compatible dict.
        """
        return {
            "value": self.value,
            "label": self.label,
            "pane": self.pane,
            "render": self.render,
            "tone": _enum_value(self.tone),
            "color": self.color,
            "line_style": _enum_value(self.line_style),
            "line_width": self.line_width,
        }


@dataclasses.dataclass(frozen=True, slots=True)
class PaneLevel:
    """Horizontal reference line in a study pane.

    Attributes:
        value: Level value in the pane's units.
        label: Label of at most 16 characters.
        tone: Colour role (not ``SIGN``).
        line_style: Stroke.
    """

    value: float
    label: str
    tone: Tone = Tone.MUTED
    line_style: LineStyle = LineStyle.DOTTED

    def to_contract(self) -> dict[str, object]:
        """Serializes to the contract ``PaneLevel`` object.

        Returns:
            JSON-compatible dict.
        """
        return {
            "value": self.value,
            "label": self.label,
            "tone": _enum_value(self.tone),
            "line_style": _enum_value(self.line_style),
        }


@dataclasses.dataclass(frozen=True, slots=True)
class PaneStyle:
    """Study pane drawn below the price pane.

    Attributes:
        id: Pane id, unique within the plugin; not ``PRICE_PANE``.
        title: Pane title of at most 48 characters.
        precision: Decimal places of the pane's axis, 0 to 8.
        levels: Up to 8 reference levels.
    """

    id: str
    title: str
    precision: int = 2
    levels: tuple[PaneLevel, ...] = ()

    def to_contract(self) -> dict[str, object]:
        """Serializes to the contract ``PaneStyle`` object.

        Returns:
            JSON-compatible dict.
        """
        return {
            "id": self.id,
            "title": self.title,
            "precision": self.precision,
            "levels": [level.to_contract() for level in self.levels],
        }


@dataclasses.dataclass(frozen=True, slots=True)
class EndReasonStyle:
    """Label shown for AOIs that ended with one ``end_reason``.

    Attributes:
        reason: End reason the plugin publishes.
        label: Short label, 1 to 32 characters.
    """

    reason: str
    label: str

    def to_contract(self) -> dict[str, object]:
        """Serializes to the contract ``EndReasonStyle`` object.

        Returns:
            JSON-compatible dict.
        """
        return {"reason": self.reason, "label": self.label}


@dataclasses.dataclass(frozen=True, slots=True)
class Display:
    """Display hints the terminal renders a plugin from.

    Kinds and events without a style use the terminal defaults.

    Attributes:
        layer: Listed in the Layers menu and drawn on the chart; False for
            plugins used only by drawing tools or strategies.
        default_visible: Shown on charts without saved layer state.
        z_index: Drawing order, 0 to 100 (higher on top).
        show_events: Whether event markers are drawn by default.
        kinds: Styles of declared AOI kinds.
        events: Styles of declared (or reserved) event names.
        plots: Published values drawn as lines or histograms.
        panes: Study panes the plots may use.
        end_reasons: Labels of the end reasons the plugin publishes.
    """

    layer: bool = True
    default_visible: bool = False
    z_index: int = 50
    show_events: bool = True
    kinds: tuple[KindStyle, ...] = ()
    events: tuple[EventStyle, ...] = ()
    plots: tuple[PlotStyle, ...] = ()
    panes: tuple[PaneStyle, ...] = ()
    end_reasons: tuple[EndReasonStyle, ...] = ()

    def to_contract(self) -> dict[str, object]:
        """Serializes to the contract ``PluginDisplay`` object.

        Returns:
            JSON-compatible dict.
        """
        return {
            "layer": self.layer,
            "default_visible": self.default_visible,
            "z_index": self.z_index,
            "show_events": self.show_events,
            "kinds": [style.to_contract() for style in self.kinds],
            "events": [style.to_contract() for style in self.events],
            "plots": [style.to_contract() for style in self.plots],
            "panes": [pane.to_contract() for pane in self.panes],
            "end_reasons": [style.to_contract() for style in self.end_reasons],
        }


class DescribedPlugin(Protocol):
    """Declarations ``validate`` reads (``IndicatorPlugin`` has them all)."""

    @property
    def plugin_id(self) -> str:
        """The unique plugin id."""
        ...

    @property
    def output_kinds(self) -> tuple[str, ...]:
        """The AOI kinds the plugin can publish."""
        ...

    @property
    def output_events(self) -> tuple[str, ...]:
        """The event names the plugin can publish."""
        ...

    @property
    def output_values(self) -> tuple[ValueSpec, ...]:
        """The values the plugin publishes."""
        ...

    @property
    def display(self) -> Display:
        """The display hints."""
        ...


def validate(plugin: DescribedPlugin) -> list[str]:
    """Checks a plugin's declarations against the contract rules.

    Args:
        plugin: Plugin (any object with the declarations).

    Returns:
        One message per broken rule; empty when the plugin is valid.
    """
    problems: list[str] = []
    if not _matches(PLUGIN_ID_PATTERN, plugin.plugin_id):
        problems.append(
            f"plugin id {plugin.plugin_id!r} is not lower snake case"
        )
    kinds = _names(plugin.output_kinds, "output kind", problems)
    events = _names(plugin.output_events, "output event", problems)
    values = _value_specs(plugin.output_values, problems)
    display = plugin.display
    if not isinstance(display, Display):
        problems.append("display is not a metadata.Display")
        return problems
    if not _is_int(display.z_index) or not 0 <= display.z_index <= 100:
        problems.append(f"display z_index {display.z_index!r} is not 0-100")
    _kind_styles(display.kinds, kinds, problems)
    _event_styles(display.events, events, problems)
    panes = _pane_styles(display.panes, problems)
    _plot_styles(display.plots, values, panes, problems)
    _end_reasons(display.end_reasons, problems)
    return problems


def _matches(pattern: re.Pattern[str], value: object) -> bool:
    """Checks that a value is a string matching a pattern.

    Args:
        pattern: Compiled pattern.
        value: Candidate.

    Returns:
        True when ``value`` is a ``str`` the pattern matches.
    """
    return isinstance(value, str) and pattern.match(value) is not None


def _is_int(value: object) -> bool:
    """Checks for an integer that is not a boolean.

    Args:
        value: Candidate.

    Returns:
        True for ``int`` values other than ``bool``.
    """
    return isinstance(value, int) and not isinstance(value, bool)


def _label(value: object, limit: int, where: str, problems: list[str]) -> None:
    """Checks a label's length.

    Args:
        value: Label.
        limit: Maximum length.
        where: Owner named in the message.
        problems: Messages, appended to.
    """
    if not isinstance(value, str) or not 1 <= len(value) <= limit:
        problems.append(f"{where} label {value!r} must be 1-{limit} characters")


def _choice(
    value: object, allowed: frozenset[str], where: str, problems: list[str]
) -> None:
    """Checks a value against a closed set of strings.

    Args:
        value: Candidate (a ``StrEnum`` member is a string).
        allowed: Allowed values.
        where: Field named in the message.
        problems: Messages, appended to.
    """
    if value not in allowed:
        problems.append(f"{where} {value!r} is not one of {sorted(allowed)}")


def _color(value: object, where: str, problems: list[str]) -> None:
    """Checks an optional ``#RRGGBB`` colour.

    Args:
        value: Colour or None.
        where: Owner named in the message.
        problems: Messages, appended to.
    """
    if value is not None and not _matches(_HEX_COLOR, value):
        problems.append(f"{where} color {value!r} is not #RRGGBB")


def _names(
    names: Sequence[str], what: str, problems: list[str]
) -> frozenset[str]:
    """Checks declared kinds or event names.

    Args:
        names: Declared names.
        what: Name of the declaration in messages.
        problems: Messages, appended to.

    Returns:
        The declared names.
    """
    seen: set[str] = set()
    for name in names:
        if not _matches(KIND_PATTERN, name):
            problems.append(f"{what} {name!r} is not upper snake case")
        if name in seen:
            problems.append(f"{what} {name!r} is declared twice")
        seen.add(name)
    return frozenset(seen)


def _value_specs(
    specs: Sequence[ValueSpec], problems: list[str]
) -> frozenset[str]:
    """Checks the published value declarations.

    Args:
        specs: Declared values.
        problems: Messages, appended to.

    Returns:
        The declared keys.
    """
    keys: set[str] = set()
    for spec in specs:
        if not isinstance(spec, ValueSpec):
            problems.append(f"output value {spec!r} is not a ValueSpec")
            continue
        where = f"output value {spec.key!r}"
        if not _matches(VALUE_KEY_PATTERN, spec.key):
            problems.append(f"{where}: key is not lower snake case")
        if spec.key in keys:
            problems.append(f"{where} is declared twice")
        keys.add(spec.key)
        _choice(spec.type, frozenset(ValueType), f"{where} type", problems)
        _choice(spec.unit, frozenset(Unit), f"{where} unit", problems)
        if spec.choices and spec.type != ValueType.STRING:
            problems.append(f"{where}: choices need type string")
        if len(spec.choices) > MAX_CHOICES:
            problems.append(f"{where}: more than {MAX_CHOICES} choices")
        if not all(isinstance(choice, str) for choice in spec.choices):
            problems.append(f"{where}: choices must be strings")
    return frozenset(keys)


def _kind_styles(
    styles: Sequence[KindStyle], kinds: frozenset[str], problems: list[str]
) -> None:
    """Checks the kind styles.

    Args:
        styles: Declared styles.
        kinds: Declared output kinds.
        problems: Messages, appended to.
    """
    seen: set[str] = set()
    for style in styles:
        where = f"kind style {style.kind!r}"
        if style.kind not in kinds:
            problems.append(f"{where}: kind is not in output_kinds")
        if style.kind in seen:
            problems.append(f"{where} is declared twice")
        seen.add(style.kind)
        _label(style.label, MAX_LABEL, where, problems)
        _choice(style.render, _KIND_RENDERS, f"{where} render", problems)
        _choice(style.tone, _TONES, f"{where} tone", problems)
        if style.tone == Tone.SIGN:
            problems.append(f"{where}: tone sign is only for histograms")
        _color(style.color, where, problems)
        opacity = style.fill_opacity
        if opacity is not None and (
            not isinstance(opacity, (int, float))
            or not math.isfinite(opacity)
            or not 0 <= opacity <= 1
        ):
            problems.append(f"{where}: fill_opacity {opacity!r} is not 0-1")
        if style.line_style is not None:
            _choice(style.line_style, _LINE_STYLES, f"{where} line", problems)


def _event_styles(
    styles: Sequence[EventStyle], events: frozenset[str], problems: list[str]
) -> None:
    """Checks the event styles.

    Args:
        styles: Declared styles.
        events: Declared output events.
        problems: Messages, appended to.
    """
    allowed = events | structures.RESERVED_EVENTS
    seen: set[str] = set()
    for style in styles:
        where = f"event style {style.name!r}"
        if style.name not in allowed:
            problems.append(f"{where}: name is not in output_events")
        if style.name in seen:
            problems.append(f"{where} is declared twice")
        seen.add(style.name)
        _label(style.label, MAX_LABEL, where, problems)
        _choice(style.render, _EVENT_RENDERS, f"{where} render", problems)
        _choice(style.position, _POSITIONS, f"{where} position", problems)
        _choice(style.shape, _SHAPES, f"{where} shape", problems)
        _choice(style.tone, _TONES, f"{where} tone", problems)
        if style.tone in (Tone.SIDE, Tone.SIGN):
            problems.append(f"{where}: events may not use tone {style.tone}")
        _color(style.color, where, problems)
        if style.time_meta is not None and not _matches(
            VALUE_KEY_PATTERN, style.time_meta
        ):
            problems.append(f"{where}: time_meta is not lower snake case")
        if style.line_style is not None:
            _choice(style.line_style, _LINE_STYLES, f"{where} line", problems)


def _pane_styles(
    panes: Sequence[PaneStyle], problems: list[str]
) -> frozenset[str]:
    """Checks the study panes.

    Args:
        panes: Declared panes.
        problems: Messages, appended to.

    Returns:
        The declared pane ids.
    """
    ids: set[str] = set()
    for pane in panes:
        where = f"pane {pane.id!r}"
        if not _matches(PANE_ID_PATTERN, pane.id) or pane.id == PRICE_PANE:
            problems.append(f"{where}: id is malformed or reserved")
        if pane.id in ids:
            problems.append(f"{where} is declared twice")
        ids.add(pane.id)
        if not isinstance(pane.title, str) or len(pane.title) > MAX_PANE_TITLE:
            problems.append(f"{where}: title is longer than {MAX_PANE_TITLE}")
        if not _is_int(pane.precision) or not 0 <= pane.precision <= 8:
            problems.append(f"{where}: precision is not 0-8")
        if len(pane.levels) > MAX_PANE_LEVELS:
            problems.append(f"{where}: more than {MAX_PANE_LEVELS} levels")
        for level in pane.levels:
            _pane_level(level, where, problems)
    return frozenset(ids)


def _pane_level(level: PaneLevel, where: str, problems: list[str]) -> None:
    """Checks one pane reference level.

    Args:
        level: Declared level.
        where: Owning pane named in messages.
        problems: Messages, appended to.
    """
    value = level.value
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        problems.append(f"{where}: level value {value!r} is not a number")
    if not isinstance(level.label, str) or len(level.label) > MAX_LEVEL_LABEL:
        problems.append(f"{where}: level label is longer than 16")
    _choice(level.tone, _TONES, f"{where} level tone", problems)
    if level.tone == Tone.SIGN:
        problems.append(f"{where}: tone sign is only for histograms")
    _choice(level.line_style, _LINE_STYLES, f"{where} level line", problems)


def _plot_styles(
    plots: Sequence[PlotStyle],
    values: frozenset[str],
    panes: frozenset[str],
    problems: list[str],
) -> None:
    """Checks the plotted values.

    Args:
        plots: Declared plots.
        values: Declared output value keys.
        panes: Declared pane ids.
        problems: Messages, appended to.
    """
    for plot in plots:
        where = f"plot {plot.value!r}"
        if plot.value not in values:
            problems.append(f"{where}: value is not in output_values")
        _label(plot.label, MAX_LABEL, where, problems)
        if plot.pane != PRICE_PANE and plot.pane not in panes:
            problems.append(f"{where}: pane {plot.pane!r} is not declared")
        _choice(plot.render, _PLOT_RENDERS, f"{where} render", problems)
        _choice(plot.tone, _TONES, f"{where} tone", problems)
        if plot.tone == Tone.SIGN and plot.render != "histogram":
            problems.append(f"{where}: tone sign is only for histograms")
        _color(plot.color, where, problems)
        _choice(plot.line_style, _LINE_STYLES, f"{where} line", problems)
        if not _is_int(plot.line_width) or not 1 <= plot.line_width <= 4:
            problems.append(f"{where}: line_width is not 1-4")


def _end_reasons(styles: Sequence[EndReasonStyle], problems: list[str]) -> None:
    """Checks the end-reason labels.

    Args:
        styles: Declared labels.
        problems: Messages, appended to.
    """
    seen: set[str] = set()
    for style in styles:
        where = f"end reason {style.reason!r}"
        if not _matches(END_REASON_PATTERN, style.reason):
            problems.append(f"{where} is not lower snake case")
        if style.reason in seen:
            problems.append(f"{where} is declared twice")
        seen.add(style.reason)
        _label(style.label, MAX_LABEL, where, problems)
