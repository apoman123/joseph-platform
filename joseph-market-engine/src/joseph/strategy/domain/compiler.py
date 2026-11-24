# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Semantic validation and compilation of Rule AST v2 definitions.

JSON Schema (enforced by the Pydantic models) guarantees structure. This
compiler adds the semantic checks the schema cannot express and produces a
``CompiledStrategy`` plan: which indicator plugins/timeframes the indicator
engine must compute, how many bars of history the evaluator needs, and the
canonical definition hash. Kinds, events and values are checked against
the ``catalog.Vocabulary`` declared by the installed plugins. A condition
whose plugin or operator has been removed fails validation with actionable
metadata; it is never skipped.

Session calendars: lower-timeframe inputs must nest in the signal timeframe
on the calendar of every universe venue (``sessions``). Rate series
(``treasury:rate:*``) publish no tradable price, so a universe venue that
only publishes rates, or a whitelisted rate instrument, is rejected; funding
and open-interest operands are rejected for a universe of exchange venues,
which have neither (ADR 0008: never emulate an unsupported feature).
"""

from __future__ import annotations

import dataclasses
import zoneinfo
from collections.abc import Callable
from collections.abc import Collection
from collections.abc import Iterator
from collections.abc import Mapping
from typing import Any

from joseph.domain import instruments
from joseph.domain import sessions
from joseph.domain import structures
from joseph.domain import timeframes
from joseph.strategy.domain import ast
from joseph.strategy.domain import catalog

COMPILER_VERSION = "2.0.0"
"""Semantic version of the compiler rules."""

MAX_NODES = 256
"""Maximum rule nodes per definition."""

MAX_DEPTH = 12
"""Maximum nesting depth of the rule tree."""

RISK_WINDOW_BARS = 60
"""Signal bars kept for the risk model (ATR(14) Wilder warm-up, swings)."""

DERIVATIVE_MARKET_FIELDS = frozenset(
    {"funding_8h_pct", "open_interest_usd", "oi_change_pct"}
)
"""Market operands that exist only for perpetual futures."""


def untradable_reason(instrument_id: str) -> str | None:
    """Explains why an instrument can never be a strategy's traded one.

    Args:
        instrument_id: Canonical instrument id.

    Returns:
        The reason, or ``None`` when it may be traded.
    """
    try:
        parsed = instruments.InstrumentId.parse(instrument_id)
    except ValueError:
        return None
    if (
        parsed.market_type is instruments.MarketType.RATE
        or parsed.venue in instruments.RATE_VENUES
    ):
        return (
            f"{instrument_id} is a published rate series, not a tradable "
            "instrument; a strategy cannot be deployed on it. Trade rates "
            "through a bond ETF (e.g. nasdaq:etf:TLT); rate series may only "
            "be read as context."
        )
    return None


def venue_calendars(
    venue: instruments.Venue,
) -> frozenset[sessions.BarCalendar]:
    """Returns every session calendar used by a venue's instruments.

    Args:
        venue: Venue.

    Returns:
        Calendars (the DEMO venue has 24x7 perps and an XNYS stock).
    """
    return frozenset(
        sessions.get(instruments.calendar_code(venue, market_type))
        for market_type in instruments.MarketType
    )


@dataclasses.dataclass(frozen=True, slots=True)
class ValidationIssue:
    """One validation error or warning (contract ``ValidationIssue``).

    Attributes:
        path: JSON pointer into the definition.
        code: Stable machine-readable code.
        message: Actionable explanation.
    """

    path: str
    code: str
    message: str

    def to_contract(self) -> dict[str, str]:
        """Serializes to the contract object.

        Returns:
            JSON-compatible dict.
        """
        return {"path": self.path, "code": self.code, "message": self.message}


@dataclasses.dataclass(frozen=True, slots=True)
class ValidationResult:
    """Outcome of validating a definition (contract ``ValidationResult``).

    Attributes:
        errors: Blocking problems.
        warnings: Non-blocking problems.
        required_plugins: Plugins the rule reads.
        required_timeframes: Timeframes the rule reads.
        node_count: Number of rule nodes.
        max_depth: Maximum nesting depth.
        definition_hash: Canonical hash of the definition.
    """

    errors: tuple[ValidationIssue, ...]
    warnings: tuple[ValidationIssue, ...]
    required_plugins: tuple[str, ...]
    required_timeframes: tuple[timeframes.Timeframe, ...]
    node_count: int
    max_depth: int
    definition_hash: str | None

    @property
    def valid(self) -> bool:
        """Whether there are no blocking errors."""
        return not self.errors

    def to_contract(self) -> dict[str, object]:
        """Serializes to the contract object.

        Returns:
            JSON-compatible dict.
        """
        return {
            "valid": self.valid,
            "errors": [issue.to_contract() for issue in self.errors],
            "warnings": [issue.to_contract() for issue in self.warnings],
            "required_plugins": list(self.required_plugins),
            "required_timeframes": [
                tf.value for tf in self.required_timeframes
            ],
            "node_count": self.node_count,
            "max_depth": self.max_depth,
            "definition_hash": self.definition_hash,
        }


@dataclasses.dataclass(frozen=True, slots=True)
class EngineRequirements:
    """What the indicator engine must compute for a strategy.

    Attributes:
        signal_timeframe: Timeframe whose closed bars drive evaluation.
        plugins: Plugin id to the set of timeframes it must run on.
        history_bars: Signal bars of history required before evaluation.
    """

    signal_timeframe: timeframes.Timeframe
    plugins: Mapping[str, frozenset[timeframes.Timeframe]]
    history_bars: int

    def to_dict(self) -> dict[str, object]:
        """Serializes for logs and engine configuration.

        Returns:
            JSON-compatible dict.
        """
        return {
            "signal_timeframe": self.signal_timeframe.value,
            "plugins": {
                plugin: sorted(tf.value for tf in tfs)
                for plugin, tfs in sorted(self.plugins.items())
            },
            "history_bars": self.history_bars,
        }


@dataclasses.dataclass(frozen=True, slots=True)
class CompiledStrategy:
    """Executable plan for the evaluator.

    Attributes:
        definition: Validated definition.
        definition_hash: Canonical content hash (``rule_version``).
        requirements: Indicator engine requirements.
        lookback_bars: Bars any node looks back beyond the current bar.
        window_bars: Frames the evaluator keeps (lookback and risk model).
        gate_count: Top-level conjuncts used for setup progress.
        has_aoi_touch: Whether a positive AOI_TOUCH can anchor setups.
        node_count: Number of rule nodes.
        vocabulary: Plugin vocabulary the plan was compiled against.
    """

    definition: ast.StrategyDefinition
    definition_hash: str
    requirements: EngineRequirements
    lookback_bars: int
    window_bars: int
    gate_count: int
    has_aoi_touch: bool
    node_count: int
    vocabulary: catalog.Vocabulary

    @property
    def target_plugins(self) -> tuple[str, ...]:
        """Plugins publishing the ``NEAREST_AOI`` target kinds, sorted."""
        return self.vocabulary.kind_plugins(
            self.definition.risk.target_kinds or ()
        )


@dataclasses.dataclass(frozen=True, slots=True)
class CompileResult:
    """Validation result plus the plan when valid.

    Attributes:
        validation: Validation outcome.
        plan: Compiled plan, or ``None`` when there are errors.
    """

    validation: ValidationResult
    plan: CompiledStrategy | None


def children_of(
    node: ast.AnyRuleNode, path: str
) -> list[tuple[ast.AnyRuleNode, str]]:
    """Lists direct child nodes with their JSON pointers.

    Args:
        node: Parent node.
        path: Parent JSON pointer.

    Returns:
        ``(child, child_path)`` pairs.
    """
    if isinstance(node, (ast.AllNode, ast.AnyNode)):
        return [
            (child, f"{path}/children/{index}")
            for index, child in enumerate(node.children)
        ]
    if isinstance(node, (ast.NotNode, ast.PersistForNode, ast.WithinBarsNode)):
        return [(node.child, f"{path}/child")]
    return []


def walk(
    node: ast.AnyRuleNode, path: str = "/when", depth: int = 1
) -> Iterator[tuple[ast.AnyRuleNode, str, int]]:
    """Yields every node depth-first with its pointer and depth.

    Args:
        node: Root node.
        path: Root JSON pointer.
        depth: Depth of ``node`` (root is 1).

    Yields:
        ``(node, path, depth)`` triples.
    """
    yield node, path, depth
    for child, child_path in children_of(node, path):
        yield from walk(child, child_path, depth + 1)


def _operand_lookback(
    operand: ast.ConstOperand
    | ast.CandleOperand
    | ast.IndicatorOperand
    | ast.MarketOperand,
) -> int:
    """Returns how many bars back an operand reads.

    Args:
        operand: Operand.

    Returns:
        ``bars_ago`` for candle/indicator operands, else 0.
    """
    if isinstance(operand, (ast.CandleOperand, ast.IndicatorOperand)):
        return operand.bars_ago
    return 0


def lookback(node: ast.AnyRuleNode) -> int:  # noqa: PLR0911 - type dispatch.
    """Computes the number of bars a node reads beyond the current one.

    Args:
        node: Rule node.

    Returns:
        Maximum lookback in signal bars.
    """
    if isinstance(node, (ast.AllNode, ast.AnyNode)):
        return max(lookback(child) for child in node.children)
    if isinstance(node, ast.NotNode):
        return lookback(node.child)
    if isinstance(node, (ast.AoiTouchNode, ast.EventNode)):
        return node.within_bars
    if isinstance(node, ast.SequenceNode):
        return node.within_bars - 1
    if isinstance(node, ast.CompareNode):
        return max(_operand_lookback(node.left), _operand_lookback(node.right))
    if isinstance(node, ast.InRangeNode):
        return max(
            _operand_lookback(node.value),
            _operand_lookback(node.low),
            _operand_lookback(node.high),
        )
    if isinstance(node, ast.CrossesNode):
        return 1 + max(
            _operand_lookback(node.left), _operand_lookback(node.right)
        )
    if isinstance(node, ast.PersistForNode):
        return node.bars - 1 + lookback(node.child)
    if isinstance(node, ast.WithinBarsNode):
        return node.bars + lookback(node.child)
    return 0


def has_positive_aoi_touch(
    node: ast.AnyRuleNode, positive: bool = True
) -> bool:
    """Checks for an AOI_TOUCH that is not under a negation.

    Args:
        node: Rule node.
        positive: Whether ``node`` is reached with positive polarity.

    Returns:
        True when an AOI touch can anchor a setup.
    """
    if isinstance(node, ast.AoiTouchNode):
        return positive
    if isinstance(node, ast.NotNode):
        return has_positive_aoi_touch(node.child, not positive)
    return any(
        has_positive_aoi_touch(child, positive)
        for child, _ in children_of(node, "")
    )


def top_level_gates(root: ast.AnyRuleNode) -> list[ast.AnyRuleNode]:
    """Returns the top-level conjuncts used to measure setup progress.

    Args:
        root: Root rule node.

    Returns:
        Children of a root ``ALL`` node, else ``[root]``.
    """
    if isinstance(root, ast.AllNode):
        return list(root.children)
    return [root]


class _Collector:
    """Accumulates issues and requirements while visiting the tree.

    Attributes:
        definition: Definition being compiled.
        signal_tf: Signal timeframe of the definition.
        vocabulary: Kinds, events and values of the installed plugins.
        unsupported_ops: Operators that are disabled or removed.
        calendars: Session calendars of every universe venue; lower
            timeframes must nest on all of them.
        exchange_venues: Universe venues (values) that list securities or
            publish rates, which have no funding or open interest.
        only_exchanges: Whether every universe venue is such a venue.
        errors: Blocking issues.
        warnings: Non-blocking issues.
        plugins: Required plugin timeframes.
    """

    def __init__(
        self,
        definition: ast.StrategyDefinition,
        vocabulary: catalog.Vocabulary,
        unsupported_ops: frozenset[str],
    ) -> None:
        """Initializes the collector.

        Args:
            definition: Definition being compiled.
            vocabulary: Kinds, events and values of the installed plugins.
            unsupported_ops: Operators that are disabled or removed.
        """
        self.definition = definition
        self.signal_tf = definition.signal_timeframe
        self.vocabulary = vocabulary
        self.unsupported_ops = unsupported_ops
        venues = definition.universe.venues
        calendars: set[sessions.BarCalendar] = set()
        for venue in venues:
            calendars.update(venue_calendars(venue))
        self.calendars = frozenset(calendars)
        self.exchange_venues = frozenset(
            venue.value
            for venue in venues
            if venue in instruments.EXCHANGE_VENUES
            or venue in instruments.RATE_VENUES
        )
        self.only_exchanges = len(self.exchange_venues) == len(set(venues))
        self.errors: list[ValidationIssue] = []
        self.warnings: list[ValidationIssue] = []
        self.plugins: dict[str, set[timeframes.Timeframe]] = {}

    def error(self, path: str, code: str, message: str) -> None:
        """Records a blocking issue.

        Args:
            path: JSON pointer.
            code: Issue code.
            message: Explanation.
        """
        self.errors.append(ValidationIssue(path, code, message))

    def warn(self, path: str, code: str, message: str) -> None:
        """Records a warning.

        Args:
            path: JSON pointer.
            code: Issue code.
            message: Explanation.
        """
        self.warnings.append(ValidationIssue(path, code, message))

    def require(self, path: str, plugin: str, tf: timeframes.Timeframe) -> None:
        """Records a plugin requirement and checks availability.

        Args:
            path: JSON pointer of the node needing the plugin.
            plugin: Plugin id.
            tf: Timeframe the plugin must run on.
        """
        self.plugins.setdefault(plugin, set()).add(tf)
        if plugin in self.vocabulary.usable:
            return
        state = (
            "is disabled or removed"
            if self.vocabulary.known(plugin)
            else "is not installed"
        )
        self.error(
            path,
            "PLUGIN_UNAVAILABLE",
            f"Indicator plugin '{plugin}' {state}. Install or enable the "
            "plugin, or edit this condition; the rule is never evaluated "
            "without it.",
        )

    def require_kind(
        self, path: str, kind: str, tf: timeframes.Timeframe
    ) -> None:
        """Records the plugin requirement of an AOI kind.

        Args:
            path: JSON pointer of the kind.
            kind: AOI kind.
            tf: Timeframe the publishing plugin must run on.
        """
        plugin = self.vocabulary.kind_plugin.get(kind)
        if plugin is None:
            self.error(
                path,
                "KIND_UNKNOWN",
                f"No installed indicator plugin publishes AOI kind {kind}.",
            )
            return
        self.require(path, plugin, tf)

    def check_field(self, path: str, plugin: str, field: str) -> None:
        """Warns when a plugin does not declare a published value.

        Args:
            path: JSON pointer of the field.
            plugin: Plugin id.
            field: Value key.
        """
        declared = self.vocabulary.values.get(plugin)
        if declared is not None and field not in declared:
            self.warn(
                path,
                "UNKNOWN_INDICATOR_FIELD",
                f"'{field}' is not a published value of {plugin}; it will "
                "evaluate as UNKNOWN.",
            )

    def check_tf(
        self, path: str, tf: timeframes.Timeframe, *, higher_only: bool
    ) -> None:
        """Checks a referenced timeframe against the signal timeframe.

        Args:
            path: JSON pointer.
            tf: Referenced timeframe.
            higher_only: Whether finer timeframes are an error.
        """
        if tf.duration_ms >= self.signal_tf.duration_ms:
            return
        if higher_only:
            self.error(
                path,
                "TIMEFRAME_FINER_THAN_SIGNAL",
                f"{tf.value} is finer than the {self.signal_tf.value} signal "
                "timeframe; use a timeframe at or above it.",
            )
        elif not all(
            calendar.contains(self.signal_tf, tf) for calendar in self.calendars
        ):
            self.error(
                path,
                "TIMEFRAME_NOT_NESTED",
                f"{tf.value} bars do not nest inside {self.signal_tf.value} "
                "bars on the session calendars of the universe.",
            )
        else:
            self.warn(
                path,
                "LOWER_TIMEFRAME_INPUT",
                f"Reads {tf.value} data inside {self.signal_tf.value} bars; "
                "only bars confirmed by the signal close are used.",
            )


def _visit_operand(
    collector: _Collector,
    path: str,
    operand: ast.ConstOperand
    | ast.CandleOperand
    | ast.IndicatorOperand
    | ast.MarketOperand,
) -> None:
    """Validates one operand.

    Args:
        collector: Issue collector.
        path: JSON pointer of the operand.
        operand: Operand.
    """
    if isinstance(operand, ast.CandleOperand):
        if operand.tf is not None and operand.tf != collector.signal_tf:
            collector.error(
                path,
                "CANDLE_TF_UNSUPPORTED",
                "Candle operands read the signal timeframe only; use an "
                "indicator value for other timeframes.",
            )
    elif (
        isinstance(operand, ast.MarketOperand)
        and operand.field in DERIVATIVE_MARKET_FIELDS
        and collector.exchange_venues
    ):
        venues = ", ".join(sorted(collector.exchange_venues))
        if collector.only_exchanges:
            collector.error(
                path,
                "MARKET_FIELD_UNAVAILABLE",
                f"{operand.field} exists only for perpetual futures; {venues} "
                "list exchange securities, which have no funding or open "
                "interest. Remove this condition.",
            )
        else:
            collector.warn(
                path,
                "MARKET_FIELD_PARTIAL",
                f"{operand.field} exists only for perpetual futures; on "
                f"{venues} it is UNKNOWN and blocks entries.",
            )
    elif isinstance(operand, ast.IndicatorOperand):
        tf = operand.tf or collector.signal_tf
        collector.check_tf(path, tf, higher_only=False)
        collector.require(path, operand.plugin_id, tf)
        collector.check_field(path, operand.plugin_id, operand.field)


def _check_state(collector: _Collector, node: ast.StateNode, path: str) -> None:
    """Validates a STATE node.

    Args:
        collector: Issue collector.
        node: Node.
        path: JSON pointer.
    """
    if len(set(node.timeframes)) != len(node.timeframes):
        collector.error(
            path, "STATE_DUPLICATE_TF", "Each timeframe may appear once."
        )
    for index, tf in enumerate(node.timeframes):
        collector.check_tf(f"{path}/timeframes/{index}", tf, higher_only=False)
        collector.require(path, node.plugin_id, tf)
    if node.match == "K_OF_N":
        if node.k is None:
            collector.error(path, "STATE_K_REQUIRED", "K_OF_N state needs k.")
        elif node.k > len(node.timeframes):
            collector.error(
                path,
                "STATE_K_EXCEEDS",
                f"k={node.k} exceeds the {len(node.timeframes)} "
                "timeframes listed.",
            )
    elif node.k is not None:
        collector.warn(
            path, "STATE_K_IGNORED", "k is ignored when match is ALL."
        )
    declared = collector.vocabulary.values.get(node.plugin_id)
    if declared is None:
        return
    for index, clause in enumerate(node.where):
        clause_path = f"{path}/where/{index}"
        collector.check_field(
            f"{clause_path}/field", node.plugin_id, clause.field
        )
        info = declared.get(clause.field)
        if info is not None:
            _check_clause_values(collector, clause_path, clause, info)


def _check_clause_values(
    collector: _Collector,
    path: str,
    clause: ast.ValueMatch,
    info: catalog.ValueInfo,
) -> None:
    """Warns when a STATE clause can never match the declared value.

    Args:
        collector: Issue collector.
        path: JSON pointer of the clause.
        clause: Clause.
        info: Declared type of the value.
    """
    if info.type not in {catalog.STRING, catalog.BOOLEAN}:
        collector.warn(
            f"{path}/field",
            "STATE_VALUE_NOT_PUBLISHED",
            f"'{clause.field}' is a {info.type} value; STATE matches string "
            "or boolean values only, so this clause never matches.",
        )
        return
    for index, entry in enumerate(clause.any_of):
        if info.type == catalog.BOOLEAN:
            ok = isinstance(entry, bool)
        else:
            ok = isinstance(entry, str) and (
                not info.choices or entry in info.choices
            )
        if not ok:
            choices = (
                "true or false"
                if info.type == catalog.BOOLEAN
                else ", ".join(info.choices) or "a string"
            )
            collector.warn(
                f"{path}/any_of/{index}",
                "STATE_VALUE_NOT_PUBLISHED",
                f"'{clause.field}' never publishes {entry!r}; it publishes "
                f"{choices}.",
            )


def _check_in_zone(
    collector: _Collector, node: ast.InZoneNode, path: str
) -> None:
    """Validates an IN_ZONE node.

    Args:
        collector: Issue collector.
        node: Node.
        path: JSON pointer.
    """
    collector.check_tf(f"{path}/tf", node.tf, higher_only=False)
    for index, kind in enumerate(node.kinds):
        collector.require_kind(f"{path}/kinds/{index}", kind, node.tf)


def _check_aoi(
    collector: _Collector,
    node: ast.AoiTouchNode | ast.AoiOverlapNode,
    path: str,
) -> None:
    """Validates AOI_TOUCH and AOI_OVERLAP nodes.

    Args:
        collector: Issue collector.
        node: Node.
        path: JSON pointer.
    """
    tf = node.tf or collector.signal_tf
    collector.check_tf(f"{path}/tf", tf, higher_only=False)
    if isinstance(node, ast.AoiTouchNode):
        kind_lists = [("kinds", node.kinds)]
    else:
        kind_lists = [("kinds_a", node.kinds_a), ("kinds_b", node.kinds_b)]
    for field, kinds in kind_lists:
        for index, kind in enumerate(kinds):
            collector.require_kind(f"{path}/{field}/{index}", kind, tf)


def _check_event(collector: _Collector, node: ast.EventNode, path: str) -> None:
    """Validates an EVENT node.

    Args:
        collector: Issue collector.
        node: Node.
        path: JSON pointer.
    """
    _require_event(
        collector, path, node.name, node.source, node.tf or collector.signal_tf
    )


def _check_sequence(
    collector: _Collector, node: ast.SequenceNode, path: str
) -> None:
    """Validates a SEQUENCE node.

    Args:
        collector: Issue collector.
        node: Node.
        path: JSON pointer.
    """
    if node.within_bars < len(node.steps):
        collector.error(
            path,
            "SEQUENCE_WINDOW_TOO_SHORT",
            f"{len(node.steps)} steps need distinct bars, but the window "
            f"is {node.within_bars} bars.",
        )
    for index, step in enumerate(node.steps):
        _require_event(
            collector,
            f"{path}/steps/{index}",
            step.name,
            step.source,
            step.tf or collector.signal_tf,
        )


def _check_compare(
    collector: _Collector, node: ast.CompareNode, path: str
) -> None:
    """Validates a COMPARE node.

    Args:
        collector: Issue collector.
        node: Node.
        path: JSON pointer.
    """
    _visit_operand(collector, f"{path}/left", node.left)
    _visit_operand(collector, f"{path}/right", node.right)
    if isinstance(node.left, ast.ConstOperand) and isinstance(
        node.right, ast.ConstOperand
    ):
        collector.warn(
            path, "CONSTANT_COMPARISON", "Both operands are constants."
        )


def _check_in_range(
    collector: _Collector, node: ast.InRangeNode, path: str
) -> None:
    """Validates an IN_RANGE node.

    Args:
        collector: Issue collector.
        node: Node.
        path: JSON pointer.
    """
    for name, operand in (
        ("value", node.value),
        ("low", node.low),
        ("high", node.high),
    ):
        _visit_operand(collector, f"{path}/{name}", operand)


def _check_crosses(
    collector: _Collector, node: ast.CrossesNode, path: str
) -> None:
    """Validates a CROSSES node.

    Args:
        collector: Issue collector.
        node: Node.
        path: JSON pointer.
    """
    _visit_operand(collector, f"{path}/left", node.left)
    _visit_operand(collector, f"{path}/right", node.right)


def _check_rr(
    collector: _Collector, node: ast.RiskRewardNode, path: str
) -> None:
    """Validates a RISK_REWARD node.

    Args:
        collector: Issue collector.
        node: Node.
        path: JSON pointer.
    """
    floor = collector.definition.risk.min_rr
    if node.min_rr < floor:
        collector.warn(
            path,
            "RR_BELOW_FLOOR",
            f"Threshold {node.min_rr} is below the strategy floor {floor}; "
            "the floor still applies to every entry.",
        )


def _check_time_window(
    collector: _Collector, node: ast.TimeWindowNode, path: str
) -> None:
    """Validates a TIME_WINDOW node.

    Args:
        collector: Issue collector.
        node: Node.
        path: JSON pointer.
    """
    try:
        zoneinfo.ZoneInfo(node.time_zone)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError):
        collector.error(
            f"{path}/time_zone",
            "TIME_ZONE_UNKNOWN",
            f"'{node.time_zone}' is not a known IANA time zone, such as "
            "America/New_York.",
        )
    for index, window in enumerate(node.windows):
        if window.start == window.end:
            collector.error(
                f"{path}/windows/{index}",
                "TIME_WINDOW_EMPTY",
                f"Window {window.start}-{window.end} starts where it ends; "
                "give it a length.",
            )


_NODE_CHECKS: Mapping[type, Callable[[_Collector, Any, str], None]] = {
    ast.StateNode: _check_state,
    ast.InZoneNode: _check_in_zone,
    ast.AoiTouchNode: _check_aoi,
    ast.AoiOverlapNode: _check_aoi,
    ast.EventNode: _check_event,
    ast.SequenceNode: _check_sequence,
    ast.CompareNode: _check_compare,
    ast.InRangeNode: _check_in_range,
    ast.CrossesNode: _check_crosses,
    ast.TimeWindowNode: _check_time_window,
    ast.RiskRewardNode: _check_rr,
}


def _visit(collector: _Collector, node: ast.AnyRuleNode, path: str) -> None:
    """Validates one node (not its children).

    Args:
        collector: Issue collector.
        node: Node to check.
        path: JSON pointer.
    """
    if node.op in collector.unsupported_ops:
        collector.error(
            path,
            "OP_UNSUPPORTED",
            f"Condition type {node.op} is no longer supported. This version "
            "stays readable but cannot be activated until the condition is "
            "replaced.",
        )
    check = _NODE_CHECKS.get(type(node))
    if check is not None:
        check(collector, node, path)


def _require_event(
    collector: _Collector,
    path: str,
    name: structures.StructureEventName,
    source: str | None,
    tf: timeframes.Timeframe,
) -> None:
    """Checks an event reference and records its plugin requirement.

    Args:
        collector: Issue collector.
        path: JSON pointer.
        name: Event name.
        source: Plugin named by the rule, if any.
        tf: Event timeframe.
    """
    collector.check_tf(f"{path}/tf", tf, higher_only=False)
    vocabulary = collector.vocabulary
    if not vocabulary.event_known(name):
        collector.error(
            f"{path}/name",
            "EVENT_UNKNOWN",
            f"No installed indicator plugin publishes event {name}.",
        )
        if source is not None:
            collector.require(path, source, tf)
        return
    publishers = vocabulary.event_plugins.get(name, ())
    if source is not None:
        if (
            name not in structures.RESERVED_EVENTS
            and vocabulary.known(source)
            and source not in publishers
        ):
            collector.error(
                f"{path}/source",
                "EVENT_SOURCE_MISMATCH",
                f"Plugin '{source}' does not publish {name}; it is published "
                f"by {', '.join(publishers)}.",
            )
        collector.require(path, source, tf)
        return
    if len(publishers) > 1:
        collector.warn(
            path,
            "EVENT_SOURCE_IMPLICIT",
            f"{name} is published by {', '.join(publishers)}; events from "
            f"any of them count and '{publishers[0]}' proves absence. Set "
            "source to read one plugin only.",
        )
    plugin = vocabulary.event_source(name, None)
    if plugin is not None:
        collector.require(path, plugin, tf)


def _check_zone_groups(
    collector: _Collector, nodes: list[tuple[ast.AnyRuleNode, str]]
) -> None:
    """Checks that IN_ZONE group members read one timeframe.

    Args:
        collector: Issue collector.
        nodes: Every node with its pointer.
    """
    groups: dict[str, list[tuple[ast.InZoneNode, str]]] = {}
    for node, path in nodes:
        if isinstance(node, ast.InZoneNode) and node.group:
            groups.setdefault(node.group, []).append((node, path))
    for name, members in sorted(groups.items()):
        tfs = {member.tf for member, _ in members}
        first_path = members[0][1]
        if len(tfs) > 1:
            collector.error(
                first_path,
                "GROUP_TF_MISMATCH",
                f"Zone group '{name}' mixes timeframes "
                f"{sorted(tf.value for tf in tfs)}; members must read zones "
                "of one timeframe.",
            )
        if len(members) == 1:
            collector.warn(
                first_path,
                "GROUP_SINGLE",
                f"Zone group '{name}' has a single member.",
            )


def _check_strategy_level(collector: _Collector, root: ast.AnyRuleNode) -> None:
    """Checks risk, universe, notification and mode settings.

    Args:
        collector: Issue collector.
        root: Root rule node.
    """
    definition = collector.definition
    risk = definition.risk
    if risk.target_model == "FIXED_R" and not risk.fixed_r:
        collector.error(
            "/risk/fixed_r",
            "FIXED_R_REQUIRED",
            "FIXED_R targets need a positive fixed_r.",
        )
    if risk.stop_model == "ATR" and not risk.stop_buffer_atr:
        collector.error(
            "/risk/stop_buffer_atr",
            "ATR_BUFFER_REQUIRED",
            "ATR stops need a positive stop_buffer_atr.",
        )
    if risk.stop_model == "AOI_INVALIDATION" and not has_positive_aoi_touch(
        root
    ):
        collector.error(
            "/risk/stop_model",
            "AOI_STOP_REQUIRES_TOUCH",
            "AOI_INVALIDATION stops need an AOI_TOUCH condition to anchor "
            "the stop.",
        )
    _check_target(collector)
    _check_universe(collector)
    if not definition.notification.emit:
        collector.warn(
            "/notification/emit", "EMIT_EMPTY", "No signal kinds are emitted."
        )
    if definition.execution_mode == "AUTO_LIVE":
        collector.warn(
            "/execution_mode",
            "AUTO_LIVE_REQUIRES_DEPLOYMENT",
            "AUTO_LIVE only runs through an execution-engine deployment with "
            "explicit consent, limits and kill switch; saving the rule does "
            "not enable live trading.",
        )


def _check_target(collector: _Collector) -> None:
    """Checks the target model and its AOI kinds.

    Args:
        collector: Issue collector.
    """
    risk = collector.definition.risk
    if risk.target_model == "NEAREST_AOI":
        if not risk.target_kinds:
            collector.error(
                "/risk/target_kinds",
                "TARGET_KINDS_REQUIRED",
                "NEAREST_AOI targets need the AOI kinds to target.",
            )
            return
        for index, kind in enumerate(risk.target_kinds):
            collector.require_kind(
                f"/risk/target_kinds/{index}", kind, collector.signal_tf
            )
    elif risk.target_kinds:
        collector.warn(
            "/risk/target_kinds",
            "TARGET_KINDS_IGNORED",
            "target_kinds is ignored when target_model is FIXED_R.",
        )


def _check_universe(collector: _Collector) -> None:
    """Checks universe venues and instrument lists.

    Args:
        collector: Issue collector.
    """
    universe = collector.definition.universe
    venues = {venue.value for venue in universe.venues}
    for index, venue in enumerate(universe.venues):
        if venue in instruments.RATE_VENUES:
            collector.error(
                f"/universe/venues/{index}",
                "RATE_VENUE_NOT_TRADABLE",
                f"{venue.value} publishes non-tradable rate series "
                f"({venue.value}:rate:*); a strategy cannot trade them. Trade "
                "rates through a bond ETF on nasdaq, nyse or cboe; rate "
                "series may only be read as context.",
            )
    for index, instrument in enumerate(universe.whitelist):
        reason = untradable_reason(instrument)
        if reason is not None:
            collector.error(
                f"/universe/whitelist/{index}", "RATE_NOT_TRADABLE", reason
            )
    for field_name, values in (
        ("whitelist", universe.whitelist),
        ("blacklist", universe.blacklist),
    ):
        for index, instrument in enumerate(values):
            if instrument.split(":", 1)[0] not in venues:
                collector.error(
                    f"/universe/{field_name}/{index}",
                    "UNIVERSE_VENUE_MISMATCH",
                    f"{instrument} is not on a venue listed in the universe.",
                )
    overlap = set(universe.whitelist) & set(universe.blacklist)
    if overlap:
        collector.error(
            "/universe",
            "UNIVERSE_OVERLAP",
            f"Listed in both whitelist and blacklist: {sorted(overlap)}.",
        )


def compile_strategy(
    definition: ast.StrategyDefinition,
    *,
    vocabulary: catalog.Vocabulary,
    unsupported_ops: Collection[str] = (),
) -> CompileResult:
    """Validates and compiles a definition.

    Args:
        definition: Schema-valid definition.
        vocabulary: Kinds, events and values of the installed plugins.
        unsupported_ops: Operators disabled or removed from this build.

    Returns:
        Validation result and, when valid, the compiled plan.
    """
    collector = _Collector(definition, vocabulary, frozenset(unsupported_ops))
    root: ast.AnyRuleNode = definition.when
    visited = list(walk(root))
    node_count = len(visited)
    depth = max(item[2] for item in visited)
    if node_count > MAX_NODES:
        collector.error(
            "/when",
            "TOO_MANY_NODES",
            f"{node_count} nodes exceed the limit of {MAX_NODES}.",
        )
    if depth > MAX_DEPTH:
        collector.error(
            "/when",
            "TOO_DEEP",
            f"Depth {depth} exceeds the limit of {MAX_DEPTH}.",
        )
    seen_ids: dict[str, str] = {}
    for node, path, _ in visited:
        if node.id is not None:
            if node.id in seen_ids:
                collector.error(
                    path,
                    "DUPLICATE_NODE_ID",
                    f"Node id '{node.id}' is also used at {seen_ids[node.id]}.",
                )
            else:
                seen_ids[node.id] = path
        _visit(collector, node, path)
    _check_zone_groups(collector, [(n, p) for n, p, _ in visited])
    _check_strategy_level(collector, root)

    plugins = {
        plugin: frozenset(tfs) for plugin, tfs in collector.plugins.items()
    }
    tfs = {definition.signal_timeframe}
    for plugin_tfs in plugins.values():
        tfs |= plugin_tfs
    required_tfs = tuple(sorted(tfs, key=lambda tf: tf.duration_ms))
    max_lookback = lookback(root)
    window = max(max_lookback + 1, RISK_WINDOW_BARS)
    definition_hash = definition.definition_hash()
    validation = ValidationResult(
        errors=tuple(collector.errors),
        warnings=tuple(collector.warnings),
        required_plugins=tuple(sorted(plugins)),
        required_timeframes=required_tfs,
        node_count=node_count,
        max_depth=depth,
        definition_hash=definition_hash,
    )
    if not validation.valid:
        return CompileResult(validation=validation, plan=None)
    plan = CompiledStrategy(
        definition=definition,
        definition_hash=definition_hash,
        requirements=EngineRequirements(
            signal_timeframe=definition.signal_timeframe,
            plugins=plugins,
            history_bars=window,
        ),
        lookback_bars=max_lookback,
        window_bars=window,
        gate_count=len(top_level_gates(root)),
        has_aoi_touch=has_positive_aoi_touch(root),
        node_count=node_count,
        vocabulary=vocabulary,
    )
    return CompileResult(validation=validation, plan=plan)
