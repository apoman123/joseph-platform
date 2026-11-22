"""Deterministic tri-state evaluator of compiled Rule AST plans.

The evaluator is a pure function of ``(plan, frames, market context,
evaluation time)``. It reads only the given ``BarFrame`` window (latest
last), so backtest, replay, paper, shadow and live share one code path and
one result for the same confirmed bars. Missing, stale, unconfirmed or
not-yet-warm inputs evaluate to UNKNOWN, which can never fire an entry.
Every node produces a ``ReasonNode`` whether it is true or false.
"""

from __future__ import annotations

import dataclasses
import datetime
import decimal
import itertools
import zoneinfo
from collections.abc import Callable
from collections.abc import Mapping
from collections.abc import Sequence
from typing import Any

from joseph.domain import decimals
from joseph.domain import market as market_domain
from joseph.domain import quality
from joseph.domain import structures
from joseph.domain import timeframes
from joseph.strategy.domain import ast
from joseph.strategy.domain import catalog
from joseph.strategy.domain import compiler
from joseph.strategy.domain import reasons
from joseph.strategy.domain import risk
from joseph.strategy.domain import tristate

EVALUATOR_VERSION = "1.0.0"
"""Semantic version of the evaluation semantics."""

T = tristate.TriState
_NEW_YORK = "America/New_York"
_SESSIONS: Mapping[str, tuple[datetime.time, datetime.time | None]] = {
    "ASIA": (datetime.time(20, 0), None),
    "LONDON": (datetime.time(2, 0), datetime.time(5, 0)),
    "NEW_YORK": (datetime.time(8, 30), datetime.time(11, 0)),
}
_UNUSABLE_FEEDS = frozenset(
    {
        quality.FeedState.STALE,
        quality.FeedState.UNAVAILABLE,
        quality.FeedState.CONNECTING,
    }
)
_HUNDRED = decimal.Decimal(100)


@dataclasses.dataclass(frozen=True, slots=True)
class MarketPoint:
    """Market context observation (funding, OI, spread, turnover).

    Attributes:
        ts_ms: Observation time.
        funding_8h_pct: Normalized 8h funding in percent.
        open_interest_usd: Open interest notional.
        oi_change_pct: Open interest change in percent.
        spread_pct: Best bid/ask spread in percent.
        turnover_24h_quote: Rolling 24h quote turnover.
    """

    ts_ms: int
    funding_8h_pct: decimal.Decimal | None = None
    open_interest_usd: decimal.Decimal | None = None
    oi_change_pct: decimal.Decimal | None = None
    spread_pct: decimal.Decimal | None = None
    turnover_24h_quote: decimal.Decimal | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class MarketContext:
    """Chronological market observations for MARKET operands.

    Attributes:
        points: Observations sorted by ``ts_ms``.
        max_age_ms: Oldest acceptable observation relative to a bar close.
    """

    points: tuple[MarketPoint, ...] = ()
    max_age_ms: int = 300_000

    def value_at(self, field: str, at_ms: int) -> decimal.Decimal | None:
        """Returns the latest value of ``field`` observed at or before a time.

        Args:
            field: MarketPoint attribute name.
            at_ms: Bar close time.

        Returns:
            The value, or ``None`` when missing or older than the budget.
        """
        for point in reversed(self.points):
            if point.ts_ms > at_ms:
                continue
            if at_ms - point.ts_ms > self.max_age_ms:
                return None
            value = getattr(point, field)
            return value if isinstance(value, decimal.Decimal) else None
        return None


@dataclasses.dataclass(frozen=True, slots=True)
class Evaluation:
    """Result of evaluating a plan on the latest frame.

    Attributes:
        result: Truth value of ``when``.
        reason_tree: Explanation trace for every node.
        gate_results: Truth value of each top-level conjunct.
        touched: Positive AOI touches on the evaluated bar's lookback.
        range_ids: Dealing ranges bound by true zone conditions.
        risk: Projected levels and net reward/risk.
        current_issue: Why the evaluated frame itself is unusable, if so.
        data_flags: Provenance flags such as ``DEMO_DATA`` or ``GAP``.
        bar_start_ms: Start of the evaluated bar.
        bar_close_ms: Close of the evaluated bar.
        frames: The window actually evaluated.
    """

    result: tristate.TriState
    reason_tree: reasons.ReasonNode
    gate_results: tuple[tristate.TriState, ...]
    touched: tuple[risk.TouchedAoi, ...]
    range_ids: tuple[str, ...]
    risk: risk.RiskAssessment
    current_issue: str | None
    data_flags: tuple[str, ...]
    bar_start_ms: int
    bar_close_ms: int
    frames: tuple[structures.BarFrame, ...]

    @property
    def aoi_ids(self) -> tuple[str, ...]:
        """Returns ids of positively touched AOIs, sorted."""
        return tuple(sorted({t.aoi.aoi_id for t in self.touched}))

    @property
    def usable(self) -> bool:
        """Returns True when the evaluated frame itself is usable."""
        return self.current_issue is None


def to_number(value: object) -> decimal.Decimal | None:
    """Converts a published indicator value to a decimal.

    Args:
        value: Decimal, int or decimal string; booleans are not numbers.

    Returns:
        The decimal, or ``None`` when not numeric.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, decimal.Decimal):
        return value
    if isinstance(value, int):
        return decimal.Decimal(value)
    if isinstance(value, str):
        try:
            return decimals.to_decimal(value)
        except ValueError:
            return None
    return None


@dataclasses.dataclass(frozen=True, slots=True)
class _Window:
    """Bar positions covered by a lookback.

    Attributes:
        positions: Existing positions, oldest first.
        complete: False when history is short or contains a gap.
    """

    positions: tuple[int, ...]
    complete: bool


class _Run:
    """Evaluation state for one ``evaluate`` call.

    Attributes:
        plan: Compiled strategy.
        frames: Evaluation window.
        current: Index of the evaluated frame.
    """

    def __init__(
        self,
        plan: compiler.CompiledStrategy,
        frames: Sequence[structures.BarFrame],
        market: MarketContext | None,
        evaluated_at_ms: int | None,
    ) -> None:
        """Initializes the run.

        Args:
            plan: Compiled strategy.
            frames: Evaluation window, latest last.
            market: Optional market context.
            evaluated_at_ms: Evaluation time for the data-age check.
        """
        self.plan = plan
        self.definition = plan.definition
        self.signal_tf = plan.definition.signal_timeframe
        self.frames = tuple(frames)
        self.current = len(self.frames) - 1
        self.market = market
        self.evaluated_at_ms = evaluated_at_ms
        self.flags: set[str] = set()
        self.group_bindings: dict[str, str | None] = {}
        self.range_ids: set[str] = set()
        self.assessment: risk.RiskAssessment | None = None

    # ----------------------------------------------------------- data access

    def frame_issue(self, pos: int) -> str | None:
        """Explains why a frame is unusable.

        Args:
            pos: Frame index.

        Returns:
            Issue text, or ``None`` when usable.
        """
        frame = self.frames[pos]
        if not frame.candle.is_final:
            return "candle is not final"
        state = frame.feed_state
        if state in _UNUSABLE_FEEDS:
            return f"feed state {state.value}"
        if (
            state is quality.FeedState.POLL
            and not self.definition.data_policy.allow_rest_poll
        ):
            return "polled data is not allowed by the data policy"
        if state is quality.FeedState.DEMO:
            self.flags.add("DEMO_DATA")
        elif state is quality.FeedState.REPLAY:
            self.flags.add("REPLAY_DATA")
        elif state is quality.FeedState.POLL:
            self.flags.add("POLL_DATA")
        if pos == self.current and self.evaluated_at_ms is not None:
            age = self.evaluated_at_ms - frame.close_ms
            if age > self.definition.data_policy.max_data_age_ms:
                return f"bar is {age} ms old"
        return None

    def usable(self, pos: int) -> bool:
        """Checks frame usability.

        Args:
            pos: Frame index.

        Returns:
            True when usable.
        """
        return 0 <= pos <= self.current and self.frame_issue(pos) is None

    def window(self, pos: int, bars: int) -> _Window:
        """Returns positions ``[pos - bars, pos]`` with completeness.

        Args:
            pos: Last position.
            bars: Bars to look back.

        Returns:
            Window of contiguous positions.
        """
        start = pos - bars
        complete = start >= 0
        positions: list[int] = [pos]
        for p in range(pos - 1, max(start, 0) - 1, -1):
            if (
                self.frames[p].candle.end_ms
                != self.frames[p + 1].candle.start_ms
            ):
                complete = False
                self.flags.add("GAP_IN_WINDOW")
                break
            positions.append(p)
        return _Window(tuple(reversed(positions)), complete)

    def snapshot(
        self, pos: int, plugin: str, tf: timeframes.Timeframe
    ) -> structures.IndicatorSnapshot | None:
        """Returns a usable snapshot visible at a frame.

        Args:
            pos: Frame index.
            plugin: Plugin id.
            tf: Series timeframe.

        Returns:
            The snapshot, or ``None`` when missing, warming up, stale or
            stamped later than the frame close (look-ahead guard).
        """
        if not self.usable(pos):
            return None
        frame = self.frames[pos]
        snap = frame.snapshot(plugin, tf)
        if snap is None or not snap.warmup_complete:
            return None
        if snap.as_of_ms > frame.close_ms:
            self.flags.add("LOOKAHEAD_REJECTED")
            return None
        if snap.data_quality.feed_state in _UNUSABLE_FEEDS:
            return None
        return snap

    # ------------------------------------------------------------ dispatch

    def node(
        self, node: ast.AnyRuleNode, path: str, pos: int
    ) -> reasons.ReasonNode:
        """Evaluates one node at a frame position.

        Args:
            node: Rule node.
            path: JSON pointer.
            pos: Frame index (current or earlier).

        Returns:
            Explanation trace including the result.
        """
        handler = _HANDLERS[type(node)]
        return handler(self, node, path, pos)

    def reason(
        self,
        node: ast.AnyRuleNode,
        path: str,
        result: tristate.TriState,
        detail: str,
        evidence: Mapping[str, object] | None = None,
        *,
        children: Sequence[reasons.ReasonNode] = (),
    ) -> reasons.ReasonNode:
        """Builds a reason node.

        Args:
            node: Rule node.
            path: JSON pointer.
            result: Truth value.
            detail: Explanation.
            evidence: Supporting values.
            children: Child traces.

        Returns:
            The reason node.
        """
        return reasons.ReasonNode(
            node_path=path,
            node_id=node.id,
            op=node.op,
            result=result,
            detail=detail,
            evidence=dict(evidence or {}),
            children=tuple(children),
        )

    # ------------------------------------------------------------ helpers

    def candle_price(self, pos: int, source: str) -> decimal.Decimal:
        """Returns the configured price of a frame's candle.

        Args:
            pos: Frame index.
            source: ``close``, ``high``, ``low`` or ``hl2``.

        Returns:
            The price.
        """
        candle = self.frames[pos].candle
        if source == "high":
            return candle.high
        if source == "low":
            return candle.low
        if source == "hl2":
            return (candle.high + candle.low) / 2
        return candle.close

    def group_binding(self, group: str, tf: timeframes.Timeframe) -> str | None:
        """Returns the range id bound by a range group at the current bar.

        Args:
            group: Range group name.
            tf: Dealing-range timeframe.

        Returns:
            The bound range id or ``None`` when unknown.
        """
        if group not in self.group_bindings:
            snap = self.snapshot(self.current, catalog.PREMIUM_DISCOUNT_OTE, tf)
            value = None if snap is None else snap.values.get("range_id")
            self.group_bindings[group] = (
                value if isinstance(value, str) else None
            )
        return self.group_bindings[group]

    def operand(
        self,
        operand: ast.ConstOperand
        | ast.CandleOperand
        | ast.IndicatorOperand
        | ast.MarketOperand,
        pos: int,
    ) -> tuple[decimal.Decimal | None, str]:
        """Resolves an operand at a frame position.

        Args:
            operand: Operand.
            pos: Frame index.

        Returns:
            ``(value, description)``; value is ``None`` when unknown.
        """
        if isinstance(operand, ast.ConstOperand):
            return operand.value, f"const {operand.value}"
        if isinstance(operand, ast.MarketOperand):
            return self._market_operand(operand, pos)
        target = pos - operand.bars_ago
        if target < 0 or not self.window(pos, operand.bars_ago).complete:
            return None, "insufficient history"
        if isinstance(operand, ast.CandleOperand):
            if not self.usable(target):
                return None, f"candle {operand.field} unavailable"
            value = getattr(self.frames[target].candle, operand.field)
            return value, f"candle {operand.field}={value}"
        return self._indicator_operand(operand, target)

    def _market_operand(
        self, operand: ast.MarketOperand, pos: int
    ) -> tuple[decimal.Decimal | None, str]:
        """Resolves a market-context operand.

        Args:
            operand: Market operand.
            pos: Frame index.

        Returns:
            ``(value, description)``.
        """
        if self.market is None or not self.usable(pos):
            return None, f"market {operand.field} unavailable"
        value = self.market.value_at(operand.field, self.frames[pos].close_ms)
        return value, f"market {operand.field}={value}"

    def _indicator_operand(
        self, operand: ast.IndicatorOperand, target: int
    ) -> tuple[decimal.Decimal | None, str]:
        """Resolves a published indicator value.

        Args:
            operand: Indicator operand.
            target: Frame index after applying ``bars_ago``.

        Returns:
            ``(value, description)``.
        """
        tf = operand.tf or self.signal_tf
        snap = self.snapshot(target, operand.plugin_id, tf)
        if snap is None:
            return None, f"{operand.plugin_id}@{tf.value} unavailable"
        number = to_number(snap.values.get(operand.field))
        return number, f"{operand.plugin_id}.{operand.field}={number}"


# ------------------------------------------------------------------ handlers


def _eval_all(
    run: _Run, node: ast.AllNode, path: str, pos: int
) -> reasons.ReasonNode:
    """Evaluates ALL.

    Args:
        run: Evaluation state.
        node: Node.
        path: JSON pointer.
        pos: Frame index.

    Returns:
        Reason node.
    """
    children = [
        run.node(child, f"{path}/children/{i}", pos)
        for i, child in enumerate(node.children)
    ]
    result = tristate.all_of(c.result for c in children)
    return run.reason(
        node, path, result, _count_detail(children), children=children
    )


def _eval_any(
    run: _Run, node: ast.AnyNode, path: str, pos: int
) -> reasons.ReasonNode:
    """Evaluates ANY.

    Args:
        run: Evaluation state.
        node: Node.
        path: JSON pointer.
        pos: Frame index.

    Returns:
        Reason node.
    """
    children = [
        run.node(child, f"{path}/children/{i}", pos)
        for i, child in enumerate(node.children)
    ]
    result = tristate.any_of(c.result for c in children)
    return run.reason(
        node, path, result, _count_detail(children), children=children
    )


def _count_detail(children: Sequence[reasons.ReasonNode]) -> str:
    """Summarizes child results.

    Args:
        children: Child traces.

    Returns:
        Text such as ``"2 of 3 true, 1 unknown"``.
    """
    true_count = sum(1 for c in children if c.result is T.TRUE)
    unknown = sum(1 for c in children if c.result is T.UNKNOWN)
    text = f"{true_count} of {len(children)} true"
    return f"{text}, {unknown} unknown" if unknown else text


def _eval_not(
    run: _Run, node: ast.NotNode, path: str, pos: int
) -> reasons.ReasonNode:
    """Evaluates NOT.

    Args:
        run: Evaluation state.
        node: Node.
        path: JSON pointer.
        pos: Frame index.

    Returns:
        Reason node.
    """
    child = run.node(node.child, f"{path}/child", pos)
    result = tristate.negate(child.result)
    return run.reason(
        node, path, result, f"not {child.result.value}", children=[child]
    )


def _eval_bias(
    run: _Run, node: ast.BiasNode, path: str, pos: int
) -> reasons.ReasonNode:
    """Evaluates BIAS from closed-bar ``daily_bias`` snapshots.

    Args:
        run: Evaluation state.
        node: Node.
        path: JSON pointer.
        pos: Frame index.

    Returns:
        Reason node.
    """
    per_tf: list[tristate.TriState] = []
    evidence: dict[str, object] = {}
    for tf in node.timeframes:
        snap = run.snapshot(pos, catalog.DAILY_BIAS, tf)
        if snap is None:
            per_tf.append(T.UNKNOWN)
            evidence[tf.value] = "UNKNOWN"
            continue
        trend = snap.values.get("trend")
        ok = trend == node.value
        if node.require_bos:
            ok = (
                ok
                and snap.values.get("last_event") == "BOS"
                and (snap.values.get("last_event_direction") == node.value)
            )
        per_tf.append(T.of(ok))
        evidence[tf.value] = trend
    if node.match == "K_OF_N":
        k = node.k or len(node.timeframes)
        result = tristate.at_least(per_tf, k)
        detail = (
            f"{sum(r is T.TRUE for r in per_tf)} of {len(per_tf)} "
            f"timeframes {node.value}, need {k}"
        )
    else:
        result = tristate.all_of(per_tf)
        detail = (
            f"all of {[tf.value for tf in node.timeframes]} "
            f"{node.value}: {result.value}"
        )
    return run.reason(node, path, result, detail, evidence)


def _zone_bounds(
    snap: structures.IndicatorSnapshot, zone: str, range_id: str | None
) -> tuple[decimal.Decimal | None, decimal.Decimal | None]:
    """Finds zone bounds from AOIs or published values.

    Args:
        snap: ``premium_discount_ote`` snapshot.
        zone: Zone name.
        range_id: Current dealing range id.

    Returns:
        ``(lower, upper)``; ``None`` parts when unknown.
    """
    for aoi in snap.aois:
        if (
            aoi.kind.value == zone
            and aoi.state.is_live
            and (range_id is None or aoi.range_id == range_id)
        ):
            return aoi.lower, aoi.upper
    values = snap.values
    if zone == "PREMIUM":
        return to_number(values.get("eq")), to_number(values.get("range_high"))
    if zone == "DISCOUNT":
        return to_number(values.get("range_low")), to_number(values.get("eq"))
    if zone == "OTE":
        return (
            to_number(values.get("ote_lower")),
            to_number(values.get("ote_upper")),
        )
    return None, None


def _eval_price_in_zone(
    run: _Run, node: ast.PriceInZoneNode, path: str, pos: int
) -> reasons.ReasonNode:
    """Evaluates PRICE_IN_ZONE with optional range-group binding.

    Args:
        run: Evaluation state.
        node: Node.
        path: JSON pointer.
        pos: Frame index.

    Returns:
        Reason node.
    """
    snap = run.snapshot(pos, catalog.PREMIUM_DISCOUNT_OTE, node.tf)
    if snap is None:
        return run.reason(
            node,
            path,
            T.UNKNOWN,
            f"dealing range on {node.tf.value} unavailable",
        )
    raw_range = snap.values.get("range_id")
    range_id = raw_range if isinstance(raw_range, str) else None
    evidence: dict[str, object] = {"range_id": range_id}
    if node.range_group:
        bound = run.group_binding(node.range_group, node.tf)
        evidence["range_group"] = node.range_group
        evidence["bound_range_id"] = bound
        if bound is None or range_id is None:
            return run.reason(
                node, path, T.UNKNOWN, "range group binding unknown", evidence
            )
        if bound != range_id:
            return run.reason(
                node,
                path,
                T.FALSE,
                f"range {range_id} differs from group range {bound}",
                evidence,
            )
    lower, upper = _zone_bounds(snap, node.zone, range_id)
    if lower is None or upper is None:
        return run.reason(
            node, path, T.UNKNOWN, f"{node.zone} bounds unavailable", evidence
        )
    price = run.candle_price(pos, node.price_source)
    inside = lower <= price <= upper
    evidence.update({"price": price, "lower": lower, "upper": upper})
    if inside and range_id is not None and pos == run.current:
        run.range_ids.add(range_id)
    return run.reason(
        node,
        path,
        T.of(inside),
        f"{node.price_source} {price} {'inside' if inside else 'outside'} "
        f"{node.tf.value} {node.zone} [{lower}, {upper}]",
        evidence,
    )


def _touches(
    candle: market_domain.Candle, aoi: structures.Aoi, mode: str
) -> bool:
    """Tests whether a candle touches an AOI.

    Args:
        candle: Closed candle.
        aoi: Area of interest.
        mode: ``WICK``, ``BODY`` or ``CLOSE``.

    Returns:
        True on intersection.
    """
    if mode == "CLOSE":
        return aoi.lower <= candle.close <= aoi.upper
    if mode == "BODY":
        return candle.body_low <= aoi.upper and candle.body_high >= aoi.lower
    return candle.low <= aoi.upper and candle.high >= aoi.lower


def _eligible(
    aoi: structures.Aoi,
    candle: market_domain.Candle,
    node: ast.AoiTouchNode,
) -> bool:
    """Checks whether an AOI may count as touched by a candle.

    The AOI must be confirmed at or before the candle opened, still live
    when the candle opened, live now when ``require_active`` is set, and
    meet ``min_quality``.

    Args:
        aoi: Latest visible record of the AOI.
        candle: Candidate touching candle.
        node: AOI_TOUCH node.

    Returns:
        True when eligible.
    """
    if aoi.confirmed_at_ms > candle.start_ms:
        return False
    if aoi.invalidated_at_ms is not None:
        live_at_touch = aoi.invalidated_at_ms > candle.start_ms
    else:
        live_at_touch = aoi.state.is_live
    if not live_at_touch:
        return False
    if node.require_active and not aoi.state.is_live:
        return False
    if node.min_quality is None:
        return True
    return (
        aoi.quality_score is not None and aoi.quality_score >= node.min_quality
    )


def _visible_aois(
    run: _Run,
    node: ast.AoiTouchNode,
    positions: Sequence[int],
    plugins: Sequence[str],
) -> tuple[dict[str, structures.Aoi], bool]:
    """Collects the latest record of each candidate AOI in a window.

    Args:
        run: Evaluation state.
        node: AOI_TOUCH node.
        positions: Window positions, oldest first.
        plugins: Plugins publishing the requested kinds.

    Returns:
        ``(aoi_id -> latest record, any_snapshot_missing)``.
    """
    tf = node.tf or run.signal_tf
    kinds = set(node.kinds)
    latest: dict[str, structures.Aoi] = {}
    missing = False
    for p in positions:
        for plugin in plugins:
            snap = run.snapshot(p, plugin, tf)
            if snap is None:
                missing = True
                continue
            for aoi in snap.aois:
                if (
                    aoi.kind in kinds
                    and aoi.side.value == node.side
                    and aoi.timeframe == tf
                ):
                    latest[aoi.aoi_id] = aoi
    return latest, missing


def aoi_touches(
    run: _Run, node: ast.AoiTouchNode, pos: int
) -> tuple[tristate.TriState, list[risk.TouchedAoi], str]:
    """Finds AOI touches for an AOI_TOUCH node.

    Args:
        run: Evaluation state.
        node: Node.
        pos: Frame index.

    Returns:
        ``(result, touches, detail)``.
    """
    tf = node.tf or run.signal_tf
    plugins = sorted({catalog.AOI_KIND_PLUGIN[k] for k in node.kinds})
    if any(run.snapshot(pos, plugin, tf) is None for plugin in plugins):
        return T.UNKNOWN, [], f"AOI source on {tf.value} unavailable"
    window = run.window(pos, node.within_bars)
    latest, missing = _visible_aois(run, node, window.positions, plugins)
    unknown = missing or not window.complete
    touches: list[risk.TouchedAoi] = []
    for p in reversed(window.positions):
        if not run.usable(p):
            unknown = True
            continue
        candle = run.frames[p].candle
        for aoi_id in sorted(latest):
            aoi = latest[aoi_id]
            if _eligible(aoi, candle, node) and _touches(
                candle, aoi, node.touch
            ):
                touches.append(
                    risk.TouchedAoi(aoi=aoi, bar_close_ms=candle.end_ms)
                )
    if touches:
        ids = sorted({t.aoi.aoi_id for t in touches})
        return T.TRUE, touches, f"touched {', '.join(ids)}"
    if unknown:
        return T.UNKNOWN, [], "no touch found but the window is incomplete"
    kinds = "/".join(sorted(k.value for k in node.kinds))
    return (
        T.FALSE,
        [],
        f"no {node.side} {kinds} touch in {node.within_bars + 1} bars",
    )


def _eval_aoi_touch(
    run: _Run, node: ast.AoiTouchNode, path: str, pos: int
) -> reasons.ReasonNode:
    """Evaluates AOI_TOUCH.

    Args:
        run: Evaluation state.
        node: Node.
        path: JSON pointer.
        pos: Frame index.

    Returns:
        Reason node.
    """
    result, touches, detail = aoi_touches(run, node, pos)
    evidence = {
        "aoi_ids": sorted({t.aoi.aoi_id for t in touches}),
        "touch_bars_ms": sorted({t.bar_close_ms for t in touches}),
        "touch": node.touch,
    }
    return run.reason(node, path, result, detail, evidence)


def _eval_aoi_overlap(
    run: _Run, node: ast.AoiOverlapNode, path: str, pos: int
) -> reasons.ReasonNode:
    """Evaluates AOI_OVERLAP among live AOIs at the frame.

    Args:
        run: Evaluation state.
        node: Node.
        path: JSON pointer.
        pos: Frame index.

    Returns:
        Reason node.
    """
    tf = node.tf or run.signal_tf
    kinds_a, kinds_b = set(node.kinds_a), set(node.kinds_b)
    plugins = sorted({catalog.AOI_KIND_PLUGIN[k] for k in kinds_a | kinds_b})
    live: list[structures.Aoi] = []
    for plugin in plugins:
        snap = run.snapshot(pos, plugin, tf)
        if snap is None:
            return run.reason(
                node, path, T.UNKNOWN, f"{plugin}@{tf.value} unavailable"
            )
        live.extend(
            a
            for a in snap.aois
            if a.state.is_live
            and a.side.value == node.side
            and a.timeframe == tf
        )
    pairs: list[list[str]] = []
    for a in (x for x in live if x.kind in kinds_a):
        for b in (y for y in live if y.kind in kinds_b):
            if a.aoi_id == b.aoi_id:
                continue
            overlap = min(a.upper, b.upper) - max(a.lower, b.lower)
            smaller = min(a.upper - a.lower, b.upper - b.lower)
            if overlap < 0:
                continue
            pct = _HUNDRED if smaller == 0 else overlap / smaller * _HUNDRED
            needed = node.min_overlap_pct
            if needed is None:
                ok = overlap > 0 or smaller == 0
            else:
                ok = pct >= needed
            if ok:
                pairs.append(sorted([a.aoi_id, b.aoi_id]))
    unique = sorted({tuple(pair) for pair in pairs})
    return run.reason(
        node,
        path,
        T.of(bool(unique)),
        f"{len(unique)} overlapping pair(s)",
        {"pairs": [list(pair) for pair in unique]},
    )


def _event_at(
    run: _Run,
    pos: int,
    name: structures.StructureEventName,
    tf: timeframes.Timeframe,
) -> list[structures.StructureEvent]:
    """Returns matching events confirmed in a frame.

    Args:
        run: Evaluation state.
        pos: Frame index.
        name: Event name.
        tf: Event timeframe.

    Returns:
        Matching events.
    """
    return [
        event
        for event in run.frames[pos].events
        if event.name == name and event.timeframe == tf
    ]


def _event_source_known(
    run: _Run,
    pos: int,
    name: structures.StructureEventName,
    tf: timeframes.Timeframe,
) -> bool:
    """Checks that the plugin publishing an event is running.

    Args:
        run: Evaluation state.
        pos: Frame index.
        name: Event name.
        tf: Event timeframe.

    Returns:
        True when absence of the event is meaningful.
    """
    plugin = catalog.EVENT_PLUGIN[name]
    if plugin is None:
        return run.usable(pos)
    return run.snapshot(pos, plugin, tf) is not None


def _eval_event(
    run: _Run, node: ast.EventNode, path: str, pos: int
) -> reasons.ReasonNode:
    """Evaluates EVENT within a lookback.

    Args:
        run: Evaluation state.
        node: Node.
        path: JSON pointer.
        pos: Frame index.

    Returns:
        Reason node.
    """
    tf = node.tf or run.signal_tf
    window = run.window(pos, node.within_bars)
    unknown = not window.complete or not _event_source_known(
        run, pos, node.name, tf
    )
    found: list[structures.StructureEvent] = []
    for p in window.positions:
        if not run.usable(p):
            unknown = True
            continue
        found.extend(_event_at(run, p, node.name, tf))
    evidence = {
        "event_ids": sorted(e.event_id for e in found),
        "bars_ms": sorted({e.confirmed_at_ms for e in found}),
    }
    if found:
        return run.reason(
            node,
            path,
            T.TRUE,
            f"{node.name.value} on {tf.value} found",
            evidence,
        )
    if unknown:
        return run.reason(
            node,
            path,
            T.UNKNOWN,
            f"{node.name.value} not found; window incomplete",
            evidence,
        )
    return run.reason(
        node,
        path,
        T.FALSE,
        f"no {node.name.value} on {tf.value} in {node.within_bars + 1} bars",
        evidence,
    )


def _eval_sequence(
    run: _Run, node: ast.SequenceNode, path: str, pos: int
) -> reasons.ReasonNode:
    """Evaluates SEQUENCE: ordered steps on distinct bars, final now.

    Args:
        run: Evaluation state.
        node: Node.
        path: JSON pointer.
        pos: Frame index.

    Returns:
        Reason node.
    """
    steps = [(step.name, step.tf or run.signal_tf) for step in node.steps]
    names = [name.value for name, _ in steps]
    if not run.usable(pos):
        return run.reason(node, path, T.UNKNOWN, "evaluated bar unusable")
    final_name, final_tf = steps[-1]
    if not _event_at(run, pos, final_name, final_tf):
        if not _event_source_known(run, pos, final_name, final_tf):
            return run.reason(
                node, path, T.UNKNOWN, f"{final_name.value} source unavailable"
            )
        return run.reason(
            node,
            path,
            T.FALSE,
            f"final step {final_name.value} not on this bar",
            {"steps": names},
        )
    window = run.window(pos, node.within_bars - 1)
    unknown = not window.complete
    matched: list[int] = []
    cursor = 0
    for p in window.positions[:-1]:
        if cursor == len(steps) - 1:
            break
        if not run.usable(p):
            unknown = True
            continue
        name, tf = steps[cursor]
        if _event_at(run, p, name, tf):
            matched.append(run.frames[p].close_ms)
            cursor += 1
    matched.append(run.frames[pos].close_ms)
    evidence = {"steps": names, "matched_bars_ms": matched}
    if cursor == len(steps) - 1:
        return run.reason(
            node,
            path,
            T.TRUE,
            f"sequence {' -> '.join(names)} complete",
            evidence,
        )
    if unknown:
        return run.reason(
            node,
            path,
            T.UNKNOWN,
            "earlier steps not found; window incomplete",
            evidence,
        )
    return run.reason(
        node,
        path,
        T.FALSE,
        f"missing {names[cursor]} before {final_name.value} within "
        f"{node.within_bars} bars",
        evidence,
    )


_COMPARATORS = {
    "gt": lambda a, b: a > b,
    "gte": lambda a, b: a >= b,
    "lt": lambda a, b: a < b,
    "lte": lambda a, b: a <= b,
    "eq": lambda a, b: a == b,
    "neq": lambda a, b: a != b,
}


def _eval_compare(
    run: _Run, node: ast.CompareNode, path: str, pos: int
) -> reasons.ReasonNode:
    """Evaluates COMPARE.

    Args:
        run: Evaluation state.
        node: Node.
        path: JSON pointer.
        pos: Frame index.

    Returns:
        Reason node.
    """
    left, left_text = run.operand(node.left, pos)
    right, right_text = run.operand(node.right, pos)
    evidence = {"left": left, "right": right}
    if left is None or right is None:
        return run.reason(
            node,
            path,
            T.UNKNOWN,
            f"{left_text} {node.cmp} {right_text}",
            evidence,
        )
    ok = bool(_COMPARATORS[node.cmp](left, right))
    return run.reason(
        node, path, T.of(ok), f"{left_text} {node.cmp} {right_text}", evidence
    )


def _eval_in_range(
    run: _Run, node: ast.InRangeNode, path: str, pos: int
) -> reasons.ReasonNode:
    """Evaluates IN_RANGE.

    Args:
        run: Evaluation state.
        node: Node.
        path: JSON pointer.
        pos: Frame index.

    Returns:
        Reason node.
    """
    value, text = run.operand(node.value, pos)
    low, _ = run.operand(node.low, pos)
    high, _ = run.operand(node.high, pos)
    evidence = {"value": value, "low": low, "high": high}
    if value is None or low is None or high is None:
        return run.reason(
            node, path, T.UNKNOWN, f"{text}; bounds unknown", evidence
        )
    ok = low <= value <= high if node.inclusive else low < value < high
    return run.reason(
        node, path, T.of(ok), f"{text} in [{low}, {high}]", evidence
    )


def _eval_crosses(
    run: _Run, node: ast.CrossesNode, path: str, pos: int
) -> reasons.ReasonNode:
    """Evaluates CROSSES between the previous and current bar.

    Args:
        run: Evaluation state.
        node: Node.
        path: JSON pointer.
        pos: Frame index.

    Returns:
        Reason node.
    """
    if pos < 1 or not run.window(pos, 1).complete:
        return run.reason(node, path, T.UNKNOWN, "no previous bar")
    now_l, _ = run.operand(node.left, pos)
    now_r, _ = run.operand(node.right, pos)
    prev_l, _ = run.operand(node.left, pos - 1)
    prev_r, _ = run.operand(node.right, pos - 1)
    evidence = {
        "prev_left": prev_l,
        "prev_right": prev_r,
        "left": now_l,
        "right": now_r,
    }
    if now_l is None or now_r is None or prev_l is None or prev_r is None:
        return run.reason(node, path, T.UNKNOWN, "operand unknown", evidence)
    if node.direction == "ABOVE":
        ok = prev_l <= prev_r and now_l > now_r
    else:
        ok = prev_l >= prev_r and now_l < now_r
    return run.reason(
        node,
        path,
        T.of(ok),
        f"crossed {node.direction.lower()}: {ok}",
        evidence,
    )


def _eval_persist(
    run: _Run, node: ast.PersistForNode, path: str, pos: int
) -> reasons.ReasonNode:
    """Evaluates PERSIST_FOR.

    Args:
        run: Evaluation state.
        node: Node.
        path: JSON pointer.
        pos: Frame index.

    Returns:
        Reason node.
    """
    window = run.window(pos, node.bars - 1)
    traces = [
        (p, run.node(node.child, f"{path}/child", p)) for p in window.positions
    ]
    results = [trace.result for _, trace in traces]
    if not window.complete:
        results.append(T.UNKNOWN)
    result = tristate.all_of(results)
    deciding = next(
        (t for _, t in traces if t.result is T.FALSE), traces[-1][1]
    )
    true_count = sum(1 for _, t in traces if t.result is T.TRUE)
    return run.reason(
        node,
        path,
        result,
        f"true on {true_count} of {node.bars} bars",
        {"bars_true": true_count},
        children=[deciding],
    )


def _eval_within(
    run: _Run, node: ast.WithinBarsNode, path: str, pos: int
) -> reasons.ReasonNode:
    """Evaluates WITHIN_BARS.

    Args:
        run: Evaluation state.
        node: Node.
        path: JSON pointer.
        pos: Frame index.

    Returns:
        Reason node.
    """
    window = run.window(pos, node.bars)
    traces = [
        (p, run.node(node.child, f"{path}/child", p)) for p in window.positions
    ]
    results = [trace.result for _, trace in traces]
    if not window.complete:
        results.append(T.UNKNOWN)
    result = tristate.any_of(results)
    deciding = next(
        (t for _, t in reversed(traces) if t.result is T.TRUE), traces[-1][1]
    )
    true_count = sum(1 for _, t in traces if t.result is T.TRUE)
    return run.reason(
        node,
        path,
        result,
        f"true on {true_count} of {node.bars + 1} bars",
        {"bars_true": true_count},
        children=[deciding],
    )


def session_of(start_ms: int) -> str:
    """Names the New York-time session containing a bar start.

    Windows (America/New_York, DST aware): ASIA 20:00-24:00, LONDON
    02:00-05:00, NEW_YORK 08:30-11:00.

    Args:
        start_ms: Bar start (UTC ms).

    Returns:
        ``ASIA``, ``LONDON``, ``NEW_YORK`` or ``NONE``.
    """
    local = datetime.datetime.fromtimestamp(
        start_ms / 1000, tz=zoneinfo.ZoneInfo(_NEW_YORK)
    ).time()
    for name, (begin, end) in _SESSIONS.items():
        if local >= begin and (end is None or local < end):
            return name
    return "NONE"


def _eval_session(
    run: _Run, node: ast.SessionNode, path: str, pos: int
) -> reasons.ReasonNode:
    """Evaluates SESSION.

    Args:
        run: Evaluation state.
        node: Node.
        path: JSON pointer.
        pos: Frame index.

    Returns:
        Reason node.
    """
    if not run.usable(pos):
        return run.reason(node, path, T.UNKNOWN, "bar unusable")
    session = session_of(run.frames[pos].candle.start_ms)
    ok = session in node.sessions
    return run.reason(
        node, path, T.of(ok), f"bar session {session}", {"session": session}
    )


def _eval_rr(
    run: _Run, node: ast.RiskRewardNode, path: str, pos: int
) -> reasons.ReasonNode:
    """Evaluates RISK_REWARD on the evaluated bar.

    Args:
        run: Evaluation state.
        node: Node.
        path: JSON pointer.
        pos: Frame index.

    Returns:
        Reason node.
    """
    assessment = run.assessment
    if pos != run.current or assessment is None:
        return run.reason(
            node,
            path,
            T.UNKNOWN,
            "reward/risk is projected for the evaluated bar only",
        )
    if assessment.rr is None:
        return run.reason(
            node,
            path,
            T.UNKNOWN,
            "; ".join(assessment.reasons) or "rr unavailable",
        )
    ok = assessment.rr >= node.min_rr
    return run.reason(
        node,
        path,
        T.of(ok),
        f"net R:R {assessment.rr} vs {node.min_rr}",
        {
            "rr": assessment.rr,
            "entry": assessment.entry,
            "stop": assessment.stop,
            "target": assessment.target,
        },
    )


_Handler = Callable[[_Run, Any, str, int], reasons.ReasonNode]

_HANDLERS: Mapping[type, _Handler] = {
    ast.AllNode: _eval_all,
    ast.AnyNode: _eval_any,
    ast.NotNode: _eval_not,
    ast.BiasNode: _eval_bias,
    ast.PriceInZoneNode: _eval_price_in_zone,
    ast.AoiTouchNode: _eval_aoi_touch,
    ast.AoiOverlapNode: _eval_aoi_overlap,
    ast.EventNode: _eval_event,
    ast.SequenceNode: _eval_sequence,
    ast.CompareNode: _eval_compare,
    ast.InRangeNode: _eval_in_range,
    ast.CrossesNode: _eval_crosses,
    ast.PersistForNode: _eval_persist,
    ast.WithinBarsNode: _eval_within,
    ast.SessionNode: _eval_session,
    ast.RiskRewardNode: _eval_rr,
}


def _collect_touches(
    run: _Run, node: ast.AnyRuleNode, positive: bool
) -> list[risk.TouchedAoi]:
    """Collects positive AOI touches at the current bar.

    Args:
        run: Evaluation state.
        node: Subtree root.
        positive: Polarity (False under NOT).

    Returns:
        Touches that anchor setups and invalidation stops.
    """
    if isinstance(node, ast.AoiTouchNode):
        if not positive:
            return []
        result, touches, _ = aoi_touches(run, node, run.current)
        return touches if result is T.TRUE else []
    if isinstance(node, ast.NotNode):
        return _collect_touches(run, node.child, not positive)
    collected: list[risk.TouchedAoi] = []
    for child, _ in compiler.children_of(node, ""):
        collected.extend(_collect_touches(run, child, positive))
    return collected


def _check_frames(
    plan: compiler.CompiledStrategy, frames: Sequence[structures.BarFrame]
) -> None:
    """Validates the frame window passed by the caller.

    Args:
        plan: Compiled strategy.
        frames: Frames, latest last.

    Raises:
        ValueError: On empty input, mixed series or unordered bars.
    """
    if not frames:
        raise ValueError("at least one frame is required")
    last = frames[-1]
    for previous, frame in itertools.pairwise(frames):
        if frame.candle.start_ms <= previous.candle.start_ms:
            raise ValueError("frames must be strictly ascending")
    for frame in frames:
        if frame.instrument_id != last.instrument_id:
            raise ValueError("frames mix instruments")
        if frame.timeframe != plan.definition.signal_timeframe:
            raise ValueError("frame timeframe differs from signal timeframe")


def evaluate(
    plan: compiler.CompiledStrategy,
    frames: Sequence[structures.BarFrame],
    market: MarketContext | None = None,
    evaluated_at_ms: int | None = None,
) -> Evaluation:
    """Evaluates a plan on the latest frame.

    Only the last ``plan.window_bars`` frames are read, so passing more
    history never changes the result.

    Args:
        plan: Compiled strategy.
        frames: Chronological frames of one instrument, latest last.
        market: Optional market context for MARKET operands.
        evaluated_at_ms: Wall/replay time for the data-age check; ``None``
            skips the age check (backtests).

    Returns:
        The evaluation.

    Raises:
        ValueError: If frames are empty, unordered or mixed.
    """
    _check_frames(plan, frames)
    window = tuple(frames[-plan.window_bars :])
    run = _Run(plan, window, market, evaluated_at_ms)
    current_issue = run.frame_issue(run.current)
    root: ast.AnyRuleNode = plan.definition.when
    touched = _collect_touches(run, root, True) if current_issue is None else []
    run.assessment = risk.assess(plan.definition, window, touched)
    tree = run.node(root, "/when", run.current)
    if current_issue is not None:
        tree = dataclasses.replace(
            tree,
            result=T.UNKNOWN,
            detail=f"evaluated bar unusable: {current_issue}; {tree.detail}",
        )
    if isinstance(root, ast.AllNode):
        gates = tuple(child.result for child in tree.children)
    else:
        gates = (tree.result,)
    current = window[-1]
    return Evaluation(
        result=tree.result,
        reason_tree=tree,
        gate_results=gates,
        touched=tuple(touched),
        range_ids=tuple(sorted(run.range_ids)),
        risk=run.assessment,
        current_issue=current_issue,
        data_flags=tuple(sorted(run.flags)),
        bar_start_ms=current.candle.start_ms,
        bar_close_ms=current.close_ms,
        frames=window,
    )
