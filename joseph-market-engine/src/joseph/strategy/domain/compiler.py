"""Semantic validation and compilation of Rule AST v2 definitions.

JSON Schema (enforced by the Pydantic models) guarantees structure. This
compiler adds the semantic checks the schema cannot express and produces a
``CompiledStrategy`` plan: which indicator plugins/timeframes the indicator
engine must compute, how many bars of history the evaluator needs, and the
canonical definition hash. A condition whose plugin or operator has been
removed fails validation with actionable metadata; it is never skipped.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from collections.abc import Collection
from collections.abc import Iterator
from collections.abc import Mapping
from typing import Any

from joseph.domain import structures
from joseph.domain import timeframes
from joseph.strategy.domain import ast
from joseph.strategy.domain import catalog

COMPILER_VERSION = "1.0.0"
"""Semantic version of the compiler rules."""

MAX_NODES = 256
"""Maximum rule nodes per definition."""

MAX_DEPTH = 12
"""Maximum nesting depth of the rule tree."""

RISK_WINDOW_BARS = 60
"""Signal bars kept for the risk model (ATR(14) Wilder warm-up, swings)."""


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
        """Returns True when there are no errors."""
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
    """

    definition: ast.StrategyDefinition
    definition_hash: str
    requirements: EngineRequirements
    lookback_bars: int
    window_bars: int
    gate_count: int
    has_aoi_touch: bool
    node_count: int


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
        errors: Blocking issues.
        warnings: Non-blocking issues.
        plugins: Required plugin timeframes.
    """

    def __init__(
        self,
        definition: ast.StrategyDefinition,
        available: frozenset[str],
        unsupported_ops: frozenset[str],
    ) -> None:
        """Initializes the collector.

        Args:
            definition: Definition being compiled.
            available: Plugin ids currently registered.
            unsupported_ops: Operators that are disabled or removed.
        """
        self.definition = definition
        self.signal_tf = definition.signal_timeframe
        self.available = available
        self.unsupported_ops = unsupported_ops
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
        if plugin not in self.available:
            self.error(
                path,
                "PLUGIN_UNAVAILABLE",
                f"Indicator plugin '{plugin}' is not available (disabled or "
                "removed). Restore the plugin or edit this condition; the "
                "rule is never evaluated without it.",
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
        elif not self.signal_tf.contains(tf):
            self.error(
                path,
                "TIMEFRAME_NOT_NESTED",
                f"{tf.value} bars do not nest inside {self.signal_tf.value} "
                "bars.",
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
    elif isinstance(operand, ast.IndicatorOperand):
        tf = operand.tf or collector.signal_tf
        collector.check_tf(path, tf, higher_only=False)
        collector.require(path, operand.plugin_id, tf)
        published = catalog.PUBLISHED_VALUES.get(operand.plugin_id)
        if published is not None and operand.field not in published:
            collector.warn(
                path,
                "UNKNOWN_INDICATOR_FIELD",
                f"'{operand.field}' is not a published value of "
                f"{operand.plugin_id}; it will evaluate as UNKNOWN.",
            )


def _check_bias(collector: _Collector, node: ast.BiasNode, path: str) -> None:
    """Validates a BIAS node.

    Args:
        collector: Issue collector.
        node: Node.
        path: JSON pointer.
    """
    if len(set(node.timeframes)) != len(node.timeframes):
        collector.error(
            path, "BIAS_DUPLICATE_TF", "Each timeframe may appear once."
        )
    for index, tf in enumerate(node.timeframes):
        collector.check_tf(f"{path}/timeframes/{index}", tf, higher_only=True)
        collector.require(path, catalog.DAILY_BIAS, tf)
    if node.match == "K_OF_N":
        if node.k is None:
            collector.error(path, "BIAS_K_REQUIRED", "K_OF_N bias needs k.")
        elif node.k > len(node.timeframes):
            collector.error(
                path,
                "BIAS_K_EXCEEDS",
                f"k={node.k} exceeds the {len(node.timeframes)} "
                "timeframes listed.",
            )
    elif node.k is not None:
        collector.warn(
            path, "BIAS_K_IGNORED", "k is ignored when match is ALL."
        )


def _check_zone(
    collector: _Collector, node: ast.PriceInZoneNode, path: str
) -> None:
    """Validates a PRICE_IN_ZONE node.

    Args:
        collector: Issue collector.
        node: Node.
        path: JSON pointer.
    """
    collector.check_tf(f"{path}/tf", node.tf, higher_only=True)
    collector.require(path, catalog.PREMIUM_DISCOUNT_OTE, node.tf)


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
    kinds = (
        node.kinds
        if isinstance(node, ast.AoiTouchNode)
        else [*node.kinds_a, *node.kinds_b]
    )
    for kind in kinds:
        collector.require(path, catalog.AOI_KIND_PLUGIN[kind], tf)


def _check_event(collector: _Collector, node: ast.EventNode, path: str) -> None:
    """Validates an EVENT node.

    Args:
        collector: Issue collector.
        node: Node.
        path: JSON pointer.
    """
    _require_event(collector, path, node.name, node.tf or collector.signal_tf)


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


_NODE_CHECKS: Mapping[type, Callable[[_Collector, Any, str], None]] = {
    ast.BiasNode: _check_bias,
    ast.PriceInZoneNode: _check_zone,
    ast.AoiTouchNode: _check_aoi,
    ast.AoiOverlapNode: _check_aoi,
    ast.EventNode: _check_event,
    ast.SequenceNode: _check_sequence,
    ast.CompareNode: _check_compare,
    ast.InRangeNode: _check_in_range,
    ast.CrossesNode: _check_crosses,
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
    tf: timeframes.Timeframe,
) -> None:
    """Records the plugin requirement of an event reference.

    Args:
        collector: Issue collector.
        path: JSON pointer.
        name: Event name.
        tf: Event timeframe.
    """
    collector.check_tf(f"{path}/tf", tf, higher_only=False)
    plugin = catalog.EVENT_PLUGIN[name]
    if plugin is not None:
        collector.require(path, plugin, tf)


def _check_range_groups(
    collector: _Collector, nodes: list[tuple[ast.AnyRuleNode, str]]
) -> None:
    """Checks that range-group members bind one dealing range.

    Args:
        collector: Issue collector.
        nodes: Every node with its pointer.
    """
    groups: dict[str, list[tuple[ast.PriceInZoneNode, str]]] = {}
    for node, path in nodes:
        if isinstance(node, ast.PriceInZoneNode) and node.range_group:
            groups.setdefault(node.range_group, []).append((node, path))
    for name, members in sorted(groups.items()):
        tfs = {member.tf for member, _ in members}
        first_path = members[0][1]
        if len(tfs) > 1:
            collector.error(
                first_path,
                "RANGE_GROUP_TF_MISMATCH",
                f"Range group '{name}' mixes timeframes "
                f"{sorted(tf.value for tf in tfs)}; members must read the "
                "same dealing range.",
            )
        if len(members) == 1:
            collector.warn(
                first_path,
                "RANGE_GROUP_SINGLE",
                f"Range group '{name}' has a single member.",
            )
        zones = {member.zone for member, _ in members}
        if {"PREMIUM", "DISCOUNT"} <= zones:
            collector.warn(
                first_path,
                "RANGE_GROUP_CONTRADICTORY",
                f"Range group '{name}' requires both premium and discount, "
                "which cannot hold at once.",
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
    if risk.target_model == "NEAREST_OPPOSING_LIQUIDITY":
        collector.require(
            "/risk/target_model", catalog.LIQUIDITY_SWEEPS, collector.signal_tf
        )
    universe = definition.universe
    venues = {venue.value for venue in universe.venues}
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


def compile_strategy(
    definition: ast.StrategyDefinition,
    *,
    available_plugins: Collection[str] | None = None,
    unsupported_ops: Collection[str] = (),
) -> CompileResult:
    """Validates and compiles a definition.

    Args:
        definition: Schema-valid definition.
        available_plugins: Registered indicator plugin ids; defaults to the
            seven first-party plugins.
        unsupported_ops: Operators disabled or removed from this build.

    Returns:
        Validation result and, when valid, the compiled plan.
    """
    available = frozenset(
        catalog.DEFAULT_PLUGINS
        if available_plugins is None
        else available_plugins
    )
    collector = _Collector(definition, available, frozenset(unsupported_ops))
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
    _check_range_groups(collector, [(n, p) for n, p, _ in visited])
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
    )
    return CompileResult(validation=validation, plan=plan)
