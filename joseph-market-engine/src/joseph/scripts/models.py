# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Strict public contracts and source validation.

Strict public contracts and source validation for managed Python scripts.
"""

from __future__ import annotations

import ast
import json
import keyword
from typing import Annotated
from typing import Literal
from typing import TypeAlias

import pydantic

from joseph.domain import errors
from joseph.domain import instruments
from joseph.domain import timeframes

InstrumentIdStr: TypeAlias = Annotated[
    str,
    pydantic.Field(
        pattern=(
            r"^(bybit|okx|coinbase|kraken|deriv|binance|hyperliquid|lighter"
            r"|bitget|mexc|twse|tpex|nasdaq|nyse|cboe|treasury|mock)"
            r":(perp|spot|future|stock|etf|rate):[A-Za-z0-9._/-]{1,64}$"
        )
    ),
]
"""Canonical instrument id text, e.g. ``binance:perp:BTCUSDT``."""

ScriptKind: TypeAlias = Literal[
    "indicator", "strategy", "library", "filter", "alert"
]
"""Visual studies, simulations, reusable libraries, market filters or alerts."""

ScriptId: TypeAlias = Annotated[
    str, pydantic.Field(pattern=r"^[a-z][a-z0-9-]{0,62}$")
]
"""Stable tenant-local script slug, one to sixty-three characters."""

ScriptRef: TypeAlias = Annotated[
    str,
    pydantic.Field(
        pattern=(
            r"^(?:[a-z][a-z0-9-]{0,62}|builtin:[a-z][a-z0-9_-]{0,63}"
            r"|plugin:[a-z0-9_.-]{1,64}:[a-z][a-z0-9_-]{0,63})$"
        )
    ),
]
"""A tenant script slug, ``builtin:<id>`` or ``plugin:<entry>:<id>``."""


def _bounded_inputs(
    value: dict[str, pydantic.JsonValue],
) -> dict[str, pydantic.JsonValue]:
    """Rejects nonfinite, excessively nested or oversized JSON input objects.

    Args:
        value: Declared user input dictionary, including nested JSON values.

    Returns:
        The unchanged finite JSON object, at most sixty-four KiB when encoded.

    Raises:
        ValueError: If serialization fails or the encoded input exceeds its
            bound.
    """
    try:
        size = len(json.dumps(value, allow_nan=False).encode())
    except (ValueError, TypeError, RecursionError):
        raise ValueError("inputs require finite, bounded JSON values") from None
    if size > 65536:
        raise ValueError("inputs exceed sixty-four KiB")
    return value


JsonInputs: TypeAlias = Annotated[
    dict[str, pydantic.JsonValue], pydantic.AfterValidator(_bounded_inputs)
]
"""Finite JSON input object bounded to sixty-four KiB per run or declaration."""


class LibraryRef(pydantic.BaseModel):
    """Pins one reusable library to an immutable tenant-owned version.

    Attributes:
        script_id: Tenant-local library slug.
        version: Positive immutable version number; never resolves latest.
    """

    model_config = pydantic.ConfigDict(extra="forbid", strict=True)
    script_id: ScriptId
    version: Annotated[int, pydantic.Field(ge=1)]


class SeriesRequest(pydantic.BaseModel):
    """Declares another symbol/resolution before isolated indicator execution.

    Attributes:
        instrument_id: Optional canonical symbol; defaults to the chart symbol.
        tf: Requested resolution, with confirmed-bar visibility enforced by
            host; "auto" picks the chart's lower timeframe for intrabars.
        limit: Maximum closed source bars, one through five hundred (five
            thousand for intrabars).
        intrabars: Lower-timeframe bars inside the newest chart bars.
        tf_input: Input whose value, unless "auto", replaces ``tf``.
        unless: Optional bar field; intrabars are fetched only for chart
            bars that lack it.
        unless_input: Boolean input that switches ``unless`` on.
    """

    model_config = pydantic.ConfigDict(extra="forbid", strict=True)
    instrument_id: InstrumentIdStr | None = None
    tf: (
        Annotated[timeframes.Timeframe, pydantic.Field(strict=False)]
        | Literal["auto"]
    )
    limit: Annotated[int, pydantic.Field(ge=1, le=5000)] = 500
    intrabars: bool = False
    tf_input: Annotated[str, pydantic.Field(max_length=64)] = ""
    unless: Literal["", "quote_turnover", "trade_count", "taker_buy_volume"] = (
        ""
    )
    unless_input: Annotated[str, pydantic.Field(max_length=64)] = ""

    @pydantic.model_validator(mode="after")
    def _bounded(self) -> SeriesRequest:
        """Bounds ordinary requests to five hundred bars.

        Returns:
            The request.

        Raises:
            ValueError: If an ordinary request exceeds five hundred bars or
                a non-intrabar request uses "auto" or ``unless``.
        """
        if not self.intrabars and (
            self.limit > 500 or self.tf == "auto" or self.unless
        ):
            raise ValueError(
                "only intrabar requests may use auto, unless or more than "
                "five hundred bars"
            )
        return self


class ScriptInput(pydantic.BaseModel):
    """Creates an immutable script version with explicit type and dependencies.

    Attributes:
        script_id: Stable tenant-local slug; must match the path on updates.
        kind: One of the five script roles; immutable after creation.
        name: Display title, one to one hundred twenty characters.
        source: UTF-8 Python source, at most sixty-four KiB.
        overlay: Whether indicator/strategy plots share the main price pane.
        defaults: Default JSON input values overridden by an individual run.
        libraries: Python import aliases mapped to pinned library versions.
        expected_version: Optimistic lock for edits; required for a new version.
        requests: Up to four declared cross-symbol/timeframe source aliases.
    """

    model_config = pydantic.ConfigDict(extra="forbid", strict=True)
    script_id: ScriptId
    kind: ScriptKind
    name: Annotated[str, pydantic.Field(min_length=1, max_length=120)]
    source: Annotated[str, pydantic.Field(min_length=1, max_length=65536)]
    overlay: bool = True
    defaults: JsonInputs = pydantic.Field(default_factory=dict)
    libraries: dict[str, LibraryRef] = pydantic.Field(default_factory=dict)
    expected_version: Annotated[int, pydantic.Field(ge=1)] | None = None
    requests: dict[str, SeriesRequest] = pydantic.Field(
        default_factory=dict, max_length=4
    )


class ScriptVersion(ScriptInput):
    """A tenant-visible immutable, content-addressed script version.

    Attributes:
        version: Positive monotonically increasing version number.
        source_hash: SHA-256 of the source and dependency/input configuration.
        created_at_ms: Creation time in UTC epoch milliseconds.
    """

    version: int
    source_hash: str
    created_at_ms: int


class ScriptRun(pydantic.BaseModel):
    """Requests a reproducible run using closed, real backend candles.

    Attributes:
        version: Pinned script version, or latest when omitted.
        instrument_id: Canonical venue:market:symbol identifier.
        tf: Supported chart timeframe, one second through one calendar month.
        limit: Maximum closed bars, from two through five thousand.
        from_ms: Optional inclusive UTC epoch millisecond lower boundary.
        to_ms: Optional exclusive UTC epoch millisecond visibility boundary.
        window_from_ms: Optional start of the displayed window; intrabar
            requests then cover only chart bars from there on.
        include_open: Evaluate the provisional last bar for indicators only;
            alerts and simulated orders always require closed bars.
        inputs: JSON input values overriding the version's defaults.
        initial_equity: Positive initial simulated cash in quote units.
        commission_bps: Commission in basis points per simulated fill.
        slippage_bps: Adverse simulated slippage in basis points per fill.
    """

    model_config = pydantic.ConfigDict(extra="forbid", strict=True)
    version: Annotated[int, pydantic.Field(ge=1)] | None = None
    instrument_id: InstrumentIdStr
    tf: timeframes.Timeframe = pydantic.Field(strict=False)
    limit: Annotated[int, pydantic.Field(ge=2, le=5000)] = 500
    from_ms: Annotated[int, pydantic.Field(ge=0)] | None = None
    to_ms: Annotated[int, pydantic.Field(ge=0)] | None = None
    window_from_ms: Annotated[int, pydantic.Field(ge=0)] | None = None
    include_open: bool = False
    inputs: JsonInputs = pydantic.Field(default_factory=dict)
    initial_equity: Annotated[
        float, pydantic.Field(gt=0, le=1e12, allow_inf_nan=False)
    ] = 10000.0
    commission_bps: Annotated[
        float, pydantic.Field(ge=0, le=1000, allow_inf_nan=False)
    ] = 0.0
    slippage_bps: Annotated[
        float, pydantic.Field(ge=0, le=1000, allow_inf_nan=False)
    ] = 0.0


class ScriptBatchItem(pydantic.BaseModel):
    """One study of a batched run.

    Attributes:
        script_id: Tenant slug, ``builtin:<id>`` or ``plugin:<entry>:<id>``.
        version: Pinned tenant version, or latest when omitted.
        inputs: Input overrides of this study.
    """

    model_config = pydantic.ConfigDict(extra="forbid", strict=True)
    script_id: ScriptRef
    version: Annotated[int, pydantic.Field(ge=1)] | None = None
    inputs: JsonInputs = pydantic.Field(default_factory=dict)


class ScriptBatchRun(ScriptRun):
    """Runs several studies over one fetch of the same chart candles.

    The shared fields select the candles and simulation settings; top-level
    ``inputs`` are merged under each study's own.

    Attributes:
        runs: One through sixteen studies, run in order.
    """

    runs: Annotated[
        list[ScriptBatchItem], pydantic.Field(min_length=1, max_length=16)
    ]


class PlotPoint(pydantic.BaseModel):
    """One script-produced scalar at a closed bar.

    Attributes:
        time_ms: Bar start in UTC epoch milliseconds.
        value: Finite numeric output, or None during warm-up.
        color: Optional literal per-bar hex color for conditional plot coloring.
    """

    model_config = pydantic.ConfigDict(extra="forbid", allow_inf_nan=False)
    time_ms: int
    value: float | None
    color: (
        Annotated[
            str,
            pydantic.Field(
                pattern=r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$"
            ),
        ]
        | None
    ) = None


class PlotSeries(pydantic.BaseModel):
    """Named output series displayed by the terminal.

    Attributes:
        name: Public series name, one to sixty-four characters.
        points: At most five thousand chronological closed-bar values.
    """

    model_config = pydantic.ConfigDict(extra="forbid")
    name: Annotated[str, pydantic.Field(min_length=1, max_length=64)]
    points: Annotated[list[PlotPoint], pydantic.Field(max_length=5000)]


class SimulatedFill(pydantic.BaseModel):
    """An isolated strategy simulation fill, never an executable broker order.

    Attributes:
        time_ms: Fill bar start in UTC epoch milliseconds.
        side: Buy or sell after converting the signed position target.
        quantity: Positive base-asset quantity filled.
        price: Fill price including adverse slippage, in quote/base units.
        fee: Nonnegative commission in quote units.
        effect: Entry, exit or reversal for unambiguous chart trade markers.
    """

    model_config = pydantic.ConfigDict(extra="forbid", allow_inf_nan=False)
    time_ms: int
    side: Literal["buy", "sell"]
    quantity: Annotated[float, pydantic.Field(gt=0)]
    price: Annotated[float, pydantic.Field(gt=0)]
    fee: Annotated[float, pydantic.Field(ge=0)]
    effect: Literal["entry", "exit", "reversal"] = "entry"


class StrategyOrder(pydantic.BaseModel):
    """A strategy order queued on a closed bar and its simulated outcome.

    Attributes:
        time_ms: Start of the closed bar on which the order was queued.
        action: long or short entry target, or close.
        quantity: Target absolute base quantity; zero for close.
        reference: Close of the bar the order was queued on, the planned
            entry of live signals.
        stop: Protective stop declared with an entry, if any.
        target: Profit target declared with an entry, if any.
        comment: Plain-text reason given by the strategy.
        status: pending (no later bar yet), filled, rejected for
            insufficient simulated equity, or unchanged (the position
            already matched).
        fill_time_ms: Start of the bar whose open filled the order.
    """

    model_config = pydantic.ConfigDict(extra="forbid", allow_inf_nan=False)
    time_ms: int
    action: Literal["long", "short", "close"]
    quantity: Annotated[float, pydantic.Field(ge=0)]
    reference: Annotated[float, pydantic.Field(gt=0)]
    stop: Annotated[float, pydantic.Field(gt=0)] | None = None
    target: Annotated[float, pydantic.Field(gt=0)] | None = None
    comment: Annotated[str, pydantic.Field(max_length=120)] = ""
    status: Literal["pending", "filled", "rejected", "unchanged"]
    fill_time_ms: int | None = None


class StrategyTrade(pydantic.BaseModel):
    """A simulated round trip reconstructed from verified fills.

    Attributes:
        direction: LONG or SHORT.
        entry_time_ms: Start of the bar of the opening fill.
        exit_time_ms: Start of the bar of the closing fill; None while open.
        quantity: Largest absolute base quantity held.
        entry_price: Quantity-weighted average opening price.
        exit_price: Quantity-weighted average closing price; None while open.
        fees: Commissions of every fill of the trade.
        pnl: Realized profit after fees; None while open.
        return_pct: pnl relative to the opening notional; None while open.
    """

    model_config = pydantic.ConfigDict(extra="forbid", allow_inf_nan=False)
    direction: Literal["LONG", "SHORT"]
    entry_time_ms: int
    exit_time_ms: int | None = None
    quantity: Annotated[float, pydantic.Field(gt=0)]
    entry_price: Annotated[float, pydantic.Field(gt=0)]
    exit_price: Annotated[float, pydantic.Field(gt=0)] | None = None
    fees: Annotated[float, pydantic.Field(ge=0)] = 0.0
    pnl: float | None = None
    return_pct: float | None = None


class ScriptAlertEvent(pydantic.BaseModel):
    """One user-authored alert condition matched on a source bar.

    Attributes:
        name: Stable condition name used for per-bar delivery deduplication.
        message: Plain-text message, at most five hundred characters.
        time_ms: Source bar start, UTC epoch milliseconds.
        severity: Notification priority used by delivery filters and quiet
            hours.
    """

    model_config = pydantic.ConfigDict(extra="forbid")
    name: Annotated[str, pydantic.Field(min_length=1, max_length=64)]
    message: Annotated[str, pydantic.Field(min_length=1, max_length=500)]
    time_ms: int
    severity: Literal["INFO", "WARNING", "ENTRY", "CRITICAL"] = "INFO"


class ScriptVisual(pydantic.BaseModel):
    """A bounded declarative drawing; never executable browser code.

    Attributes:
        id: Stable visual id unique within this script instance.
        kind: Plot/drawing category interpreted by the chart renderer.
        data: JSON coordinates, text and presentation; renderers read only
            documented fields and never inject HTML, CSS URLs or JavaScript.
    """

    model_config = pydantic.ConfigDict(extra="forbid")
    id: Annotated[str, pydantic.Field(min_length=1, max_length=100)]
    kind: Literal[
        "line",
        "polyline",
        "box",
        "label",
        "table",
        "fill",
        "hline",
        "linefill",
        "barcolor",
        "bgcolor",
        "shape",
        "candle",
        "bar",
    ]
    data: dict[str, pydantic.JsonValue]


class ScriptResult(pydantic.BaseModel):
    """Reproducible plots and optional simulated strategy performance.

    Attributes:
        script_id: Tenant-owned script slug.
        version: Immutable script version evaluated.
        kind: Indicator, strategy, filter or alert; libraries are import-only.
        source_hash: Content hash including default inputs and dependency pins.
        instrument_id: Canonical source instrument identifier.
        tf: Source bar resolution.
        bars_count: Number of source bars evaluated, including provisional bars
            only when explicitly requested for an indicator.
        feed_state: Source provenance; DEMO data remains explicitly marked.
        data_source: Upstream provider provenance label.
        complete: Whether the requested history has no closed-bar gaps.
        overlay: Whether plots share the main price pane.
        plots: At most sixty-four scalar output series.
        fills: Simulated next-bar-open fills; empty for indicators.
        orders: Queued strategy orders and their simulated outcomes; empty
            for other roles.
        trades: Round trips reconstructed by the host from verified fills.
        position_size: Signed simulated position after the last bar.
        equity_curve: Marked simulated quote equity; empty for indicators.
        metrics: Finite numeric simulation statistics, including fees and
            unfilled final-bar intents; no performance is fabricated.
        matched: Boolean decision for a filter, otherwise None.
        alerts: Previewed alert events; delivery requires an enabled alert job.
        visuals: Bounded declarative drawings, fills, coloring and tables.
        bar_closes: Confirmed source bar starts mapped to actual evaluated
            closes;
            alert checkpoints never consult a later, different market window.
        plot_styles: Per-series input/style settings used by the renderer.
        bar_visuals: Per-bar colors, symbols and custom OHLC series, separately
            bounded from persistent drawing objects.
    """

    model_config = pydantic.ConfigDict(extra="forbid", allow_inf_nan=False)
    script_id: str
    version: int
    kind: Literal["indicator", "strategy", "filter", "alert"]
    source_hash: str
    instrument_id: str
    tf: str
    bars_count: int
    feed_state: str
    data_source: str
    complete: bool
    overlay: bool
    plots: Annotated[list[PlotSeries], pydantic.Field(max_length=64)]
    fills: Annotated[list[SimulatedFill], pydantic.Field(max_length=4000)]
    orders: Annotated[list[StrategyOrder], pydantic.Field(max_length=4000)] = (
        pydantic.Field(default_factory=list)
    )
    trades: Annotated[list[StrategyTrade], pydantic.Field(max_length=4000)] = (
        pydantic.Field(default_factory=list)
    )
    position_size: float = 0.0
    equity_curve: Annotated[list[PlotPoint], pydantic.Field(max_length=5000)]
    metrics: dict[str, Annotated[float, pydantic.Field(allow_inf_nan=False)]]
    matched: bool | None = None
    alerts: Annotated[
        list[ScriptAlertEvent], pydantic.Field(max_length=2000)
    ] = pydantic.Field(default_factory=list)
    visuals: Annotated[list[ScriptVisual], pydantic.Field(max_length=500)] = (
        pydantic.Field(default_factory=list)
    )
    plot_styles: dict[str, dict[str, pydantic.JsonValue]] = pydantic.Field(
        default_factory=dict
    )
    bar_closes: dict[str, int] = pydantic.Field(default_factory=dict)
    bar_visuals: Annotated[
        list[ScriptVisual], pydantic.Field(max_length=16000)
    ] = pydantic.Field(default_factory=list)


FilterMetric: TypeAlias = Literal["order_book", "open_interest", "funding"]
"""Market data a scan loads beyond the snapshot (``ctx.snapshot`` keys)."""


class ScanCondition(pydantic.BaseModel):
    """One filter of a composed scan; an instrument must match every one.

    Attributes:
        script_id: Owned Filter slug, ``builtin:<id>`` or a plugin id.
        version: Tenant version, latest when omitted; catalog scripts pin
            their installed source hash instead.
        inputs: JSON values overriding the filter's defaults.
    """

    model_config = pydantic.ConfigDict(extra="forbid", strict=True)
    script_id: ScriptRef
    version: Annotated[int, pydantic.Field(ge=1)] | None = None
    inputs: JsonInputs = pydantic.Field(default_factory=dict)


class ScanConditionPin(pydantic.BaseModel):
    """A scan condition resolved and pinned at submission.

    Attributes:
        script_id: Filter identifier.
        version: Tenant version, or 0 for a catalog script.
        source_hash: Exact source identity evaluated.
        title: Display name of the filter.
        inputs: Effective inputs (defaults plus overrides).
    """

    model_config = pydantic.ConfigDict(extra="forbid")
    script_id: str
    version: int
    source_hash: str
    title: str
    inputs: dict[str, pydantic.JsonValue] = pydantic.Field(default_factory=dict)


class ScanInput(pydantic.BaseModel):
    """Selects a real market universe for an isolated Python filter job.

    Attributes:
        version: Optional immutable filter version; defaults to the latest.
        venue: Exchange/market venue to scan, or all enabled venues.
        market_type: Optional instrument market category, such as stock or perp.
        query: Optional symbol/name search within that market.
        max_symbols: Explicit maximum instruments, one through ten thousand.
        history_bars: Closed bars per instrument; zero uses snapshots only.
        tf: Resolution used when historical bars are requested.
        inputs: JSON values overriding the pinned filter's defaults.
        conditions: Further filters an instrument must also match (AND);
            the only filters of a scan submitted without a path script.
        metrics: Data to load beyond the snapshot; catalog filters add the
            metrics they declare.
        order_by: "symbol" scans instruments by id; "turnover" loads every
            quote first and scans the highest 24h turnover first, so
            max_symbols keeps the most active instruments.
    """

    model_config = pydantic.ConfigDict(extra="forbid", strict=True)
    version: Annotated[int, pydantic.Field(ge=1)] | None = None
    venue: Annotated[instruments.Venue, pydantic.Field(strict=False)] | None = (
        None
    )
    market_type: (
        Annotated[instruments.MarketType, pydantic.Field(strict=False)] | None
    ) = None
    query: Annotated[str, pydantic.Field(max_length=120)] = ""
    max_symbols: Annotated[int, pydantic.Field(ge=1, le=10000)] = 100
    history_bars: Annotated[int, pydantic.Field(ge=0, le=500)] = 0
    tf: timeframes.Timeframe = pydantic.Field(
        default=timeframes.Timeframe.D1, strict=False
    )
    inputs: JsonInputs = pydantic.Field(default_factory=dict)
    conditions: Annotated[
        list[ScanCondition], pydantic.Field(max_length=10)
    ] = pydantic.Field(default_factory=list)
    metrics: Annotated[list[FilterMetric], pydantic.Field(max_length=3)] = (
        pydantic.Field(default_factory=list)
    )
    order_by: Literal["symbol", "turnover"] = "symbol"


class ScanJob(pydantic.BaseModel):
    """Progress and reproducible results for an asynchronous market scan.

    Attributes:
        job_id: Opaque tenant-owned scan identifier.
        script_id: Filter script slug.
        version: Immutable filter version pinned at submission.
        status: Pending, running, complete, failed or cancelled job state.
        created_at_ms: Submission time, UTC epoch milliseconds.
        total: Number of eligible instruments selected by the explicit scan
            bound.
        scanned: Instruments evaluated or reported unavailable so far.
        matches: Matching instrument records with real source snapshots.
        errors: Per-instrument filter failures (script errors).
        error: Job-level error, otherwise None.
        source_hash: Immutable source/dependency configuration hash.
        universe_count: Total eligible market instruments before max_symbols.
        conditions: Every pinned filter, in evaluation order; the first is
            also described by script_id, version and source_hash.
        unavailable: Instruments without the data a filter needs (missing
            quote, history or metric), with the reason; never matches.
    """

    model_config = pydantic.ConfigDict(extra="forbid")
    job_id: str
    script_id: str
    version: int
    status: Literal["pending", "running", "complete", "failed", "cancelled"]
    created_at_ms: int
    total: int = 0
    scanned: int = 0
    matches: list[dict[str, pydantic.JsonValue]] = pydantic.Field(
        default_factory=list
    )
    errors: list[dict[str, str]] = pydantic.Field(default_factory=list)
    error: str | None = None
    source_hash: str
    universe_count: int = 0
    conditions: list[ScanConditionPin] = pydantic.Field(default_factory=list)
    unavailable: list[dict[str, str]] = pydantic.Field(default_factory=list)


class AlertJobInput(pydantic.BaseModel):
    """Explicitly enables a pinned Python condition on future closed bars.

    Attributes:
        script_id: Owned alert, indicator or strategy script publishing
            conditions.
        version: Positive immutable tenant version, pinned rather than
            following edits; 0 for a built-in or plugin script, whose
            installed source hash is pinned instead.
        instrument_id: Canonical instrument whose new closed bars are evaluated.
        tf: Source resolution, one second through one calendar month.
        inputs: JSON values overriding the pinned version's defaults.
        subscription_ids: Explicit owned notification destinations; UI is
            implicit.
        enabled: Whether evaluation and external notification routing are
            active.
        history_bars: Context depth, two through two thousand closed bars.
    """

    model_config = pydantic.ConfigDict(extra="forbid", strict=True)
    script_id: ScriptRef
    version: Annotated[int, pydantic.Field(ge=0)]
    instrument_id: InstrumentIdStr
    tf: timeframes.Timeframe = pydantic.Field(strict=False)
    inputs: JsonInputs = pydantic.Field(default_factory=dict)
    subscription_ids: Annotated[list[str], pydantic.Field(max_length=50)] = (
        pydantic.Field(default_factory=list)
    )
    enabled: bool = False
    history_bars: Annotated[int, pydantic.Field(ge=2, le=2000)] = 500


class AlertJob(AlertJobInput):
    """Persistent Python alert evaluation state and visible diagnostics.

    Attributes:
        job_id: Opaque tenant-owned alert identifier.
        created_at_ms: Creation time in UTC epoch milliseconds.
        last_processed_ms: Latest successfully evaluated bar close/checkpoint.
        last_error: Visible missing-data, provider or user-code failure, if any.
        last_checked_ms: Last evaluation attempt in UTC epoch milliseconds.
        emitted: Number of distinct conditions recorded for this job.
        source_hash: Source identity pinned at save; a catalog script whose
            installed source changed is not evaluated until saved again.
        title: Display name of the condition script at save.
    """

    job_id: str
    created_at_ms: int
    last_processed_ms: int
    last_error: str | None = None
    last_checked_ms: int | None = None
    emitted: int = 0
    source_hash: str | None = None
    title: str = ""


def _base_name(base: ast.expr) -> str:
    """Reads a class base name without executing a Python expression.

    Args:
        base: Parsed base expression from an untrusted source declaration.

    Returns:
        Identifier or attribute name, otherwise an empty string.
    """
    if isinstance(base, ast.Name):
        return base.id
    if isinstance(base, ast.Attribute):
        return base.attr
    return ""


def validate_source(script: ScriptInput) -> None:
    """Checks script declarations and exports without executing user code.

    Args:
        script: Strict script input with Python source and library aliases.

    Returns:
        None after checking the declaration, entry point and size bounds.

    Raises:
        errors.ValidationFailedError: For malformed Python, an inconsistent
            kind, missing entry point/exports or invalid dependency aliases.
    """
    if len(script.source.encode()) > 65536 or len(script.libraries) > 16:
        raise errors.ValidationFailedError(
            "script source or dependency limit exceeded"
        )
    try:
        tree = ast.parse(script.source, filename=f"{script.script_id}.py")
    except (SyntaxError, ValueError, RecursionError) as exc:
        line = getattr(exc, "lineno", None)
        raise errors.ValidationFailedError(
            f"invalid Python source at line {line or 1}"
        ) from None
    if sum(1 for _ in ast.walk(tree)) > 10000:
        raise errors.ValidationFailedError("script is too complex")
    bases = {"Indicator", "Strategy", "Library", "Filter", "Alert"}
    classes = [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and any(_base_name(base) in bases for base in node.bases)
    ]
    expected = script.kind.title()
    if len(classes) != 1:
        raise errors.ValidationFailedError(
            "declare exactly one class inheriting the selected "
            "script abstract base"
        )
    selected = classes[0]
    names = {
        base.id if isinstance(base, ast.Name) else base.attr
        for base in selected.bases
        if isinstance(base, (ast.Name, ast.Attribute))
    }
    if expected not in names or len(names & bases) != 1:
        raise errors.ValidationFailedError(
            f"{script.kind} scripts must inherit {expected} exclusively"
        )
    method = {"library": "exports", "filter": "matches"}.get(
        script.kind, "on_bar"
    )
    implementations = [
        node
        for node in selected.body
        if isinstance(node, ast.FunctionDef) and node.name == method
    ]
    count = 1 if script.kind == "library" else 2
    if len(implementations) != 1 or len(implementations[0].args.args) != count:
        raise errors.ValidationFailedError(
            f"implement {method} with the required self/context arguments"
        )
    for alias in (*script.libraries, *script.requests):
        if (
            len(alias) > 64
            or not alias.isidentifier()
            or alias.startswith("_")
            or keyword.iskeyword(alias)
        ):
            raise errors.ValidationFailedError(
                "source aliases must be public Python identifiers of at "
                "most 64 characters"
            )


class ScriptBatchEntry(pydantic.BaseModel):
    """The outcome of one study of a batched run.

    Attributes:
        script_id: Requested script id.
        result: Validated result, or None when the study failed.
        error: Why the study failed, otherwise None.
    """

    model_config = pydantic.ConfigDict(extra="forbid")
    script_id: str
    result: ScriptResult | None = None
    error: str | None = None


class ScriptBatchResult(pydantic.BaseModel):
    """Per-study outcomes of a batched run, in request order.

    Attributes:
        results: One entry per requested study.
    """

    model_config = pydantic.ConfigDict(extra="forbid")
    results: list[ScriptBatchEntry]
