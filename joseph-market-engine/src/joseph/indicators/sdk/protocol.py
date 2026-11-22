"""The indicator plugin contract (V5 §3.3 / §6.3).

A plugin is a deterministic, pure state machine over closed bars of one
series. The engine owns data plumbing and visibility; plugins own only their
algorithm. Plugins publish canonical ``Aoi`` / ``StructureEvent`` records and
scalar values inside ``IndicatorSnapshot``; no other module may depend on a
plugin's private state.

Stream names returned by ``required_streams``:

* ``"candles:chart"`` - closed bars of the series itself (always present).
* ``"intrabars:<tf>"`` - closed lower-timeframe bars inside the current bar.
* ``"aux:<tf>"`` - completed bars of another timeframe whose ``end_ms`` is
  at or before the current bar's close (the no-look-ahead rule).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from typing import Any
from typing import Protocol

from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import quality
from joseph.domain import structures
from joseph.domain import timeframes as tfs
from joseph.indicators.sdk import settings as settings_lib

CHART_STREAM = "candles:chart"
INTRABAR_PREFIX = "intrabars:"
AUX_PREFIX = "aux:"


def intrabar_stream(timeframe: tfs.Timeframe) -> str:
    """Builds an intrabar stream name.

    Args:
        timeframe: Lower timeframe.

    Returns:
        Stream name such as ``"intrabars:1m"``.
    """
    return f"{INTRABAR_PREFIX}{timeframe.value}"


def aux_stream(timeframe: tfs.Timeframe) -> str:
    """Builds an auxiliary completed-bar stream name.

    Args:
        timeframe: Other timeframe.

    Returns:
        Stream name such as ``"aux:1h"``.
    """
    return f"{AUX_PREFIX}{timeframe.value}"


def parse_stream(name: str) -> tuple[str, tfs.Timeframe | None]:
    """Splits a stream name into its kind and timeframe.

    Args:
        name: Stream name.

    Returns:
        ``("chart", None)``, ``("intrabars", tf)`` or ``("aux", tf)``.

    Raises:
        ValueError: If the name is not a known stream.
    """
    if name == CHART_STREAM:
        return "chart", None
    for prefix, kind in ((INTRABAR_PREFIX, "intrabars"), (AUX_PREFIX, "aux")):
        if name.startswith(prefix):
            return kind, tfs.parse(name[len(prefix) :])
    raise ValueError(f"unknown stream {name!r}")


@dataclasses.dataclass(frozen=True, slots=True)
class BarInput:
    """Everything a plugin may see when a bar closes.

    Attributes:
        candle: The closed bar of the series (``is_final`` is True).
        intrabars: Closed lower-timeframe bars inside ``[start, end)``,
            keyed by timeframe, oldest first.
        aux: Completed bars of other timeframes visible at the close (their
            ``end_ms <= candle.end_ms``), keyed by timeframe, oldest first.
    """

    candle: market.Candle
    intrabars: Mapping[tfs.Timeframe, tuple[market.Candle, ...]] = (
        dataclasses.field(default_factory=dict)
    )
    aux: Mapping[tfs.Timeframe, tuple[market.Candle, ...]] = dataclasses.field(
        default_factory=dict
    )


@dataclasses.dataclass(frozen=True, slots=True)
class PluginOutput:
    """Per-bar output of a plugin.

    Attributes:
        events: Events confirmed at this bar's close.
        updated_aois: AOIs created or changed on this bar (incl. ended ones).
    """

    events: tuple[structures.StructureEvent, ...] = ()
    updated_aois: tuple[structures.Aoi, ...] = ()


@dataclasses.dataclass(frozen=True, slots=True)
class SeriesHeader:
    """Bookkeeping shared by every plugin state.

    Attributes:
        instrument_id: Exact venue instrument.
        timeframe: Series timeframe.
        settings: Resolved settings as sorted ``(name, value)`` pairs.
        bars: Number of bars processed.
        bar_start_ms: Start of the last processed bar.
        as_of_ms: Close of the last processed bar.
        last_start_ms: Start of the last processed bar, ``None`` initially.
        feed_state: Provenance of the last bar.
        missing_bars: Gaps detected in the series so far.
        events: Events confirmed on the last bar.
        ended: AOIs that stopped being live on the last bar.
        coverage_pct: Optional sub-data coverage for the last bar.
    """

    instrument_id: instruments.InstrumentId
    timeframe: tfs.Timeframe
    settings: tuple[tuple[str, settings_lib.SettingValue], ...]
    bars: int = 0
    bar_start_ms: int = 0
    as_of_ms: int = 0
    last_start_ms: int | None = None
    feed_state: quality.FeedState = quality.FeedState.UNAVAILABLE
    missing_bars: int = 0
    events: tuple[structures.StructureEvent, ...] = ()
    ended: tuple[structures.Aoi, ...] = ()
    coverage_pct: str | None = None

    def settings_map(self) -> dict[str, settings_lib.SettingValue]:
        """Returns the resolved settings as a dict.

        Returns:
            Mapping of setting name to value.
        """
        return dict(self.settings)

    def advance(self, candle: market.Candle) -> SeriesHeader:
        """Records a newly processed bar.

        Args:
            candle: The closed bar.

        Returns:
            Updated header with per-bar outputs cleared.

        Raises:
            ValueError: If the bar belongs to another series, is not final,
                or is not strictly after the previous bar.
        """
        if candle.instrument_id != self.instrument_id:
            raise ValueError("bar belongs to another instrument")
        if candle.timeframe is not self.timeframe:
            raise ValueError("bar belongs to another timeframe")
        if not candle.is_final:
            raise ValueError("plugins only accept final bars")
        missing = self.missing_bars
        if self.last_start_ms is not None:
            if candle.start_ms <= self.last_start_ms:
                raise ValueError("bars must be strictly increasing")
            expected = self.timeframe.end_of(self.last_start_ms)
            if candle.start_ms > expected:
                missing += self.timeframe.bars_between(
                    expected, candle.start_ms
                )
        return dataclasses.replace(
            self,
            bars=self.bars + 1,
            bar_start_ms=candle.start_ms,
            as_of_ms=candle.end_ms,
            last_start_ms=candle.start_ms,
            feed_state=candle.feed_state,
            missing_bars=missing,
            events=(),
            ended=(),
            coverage_pct=None,
        )


class IndicatorPlugin(Protocol):
    """Structural contract every indicator plugin satisfies."""

    @property
    def plugin_id(self) -> str:
        """Returns the unique plugin id, e.g. ``fvg_ifvg``."""
        ...

    @property
    def indicator_version(self) -> str:
        """Returns the semantic version of the algorithm."""
        ...

    @property
    def schema_version(self) -> int:
        """Returns the output schema version."""
        ...

    @property
    def display_name(self) -> str:
        """Returns a human readable name."""
        ...

    @property
    def description(self) -> str:
        """Returns a one-sentence description."""
        ...

    @property
    def warmup_bars(self) -> int:
        """Returns bars needed (with default settings) before outputs."""
        ...

    @property
    def timeframes(self) -> tuple[tfs.Timeframe, ...]:
        """Returns the series timeframes the plugin supports."""
        ...

    @property
    def output_kinds(self) -> tuple[structures.AoiKind, ...]:
        """Returns the AOI kinds the plugin can publish."""
        ...

    @property
    def output_events(self) -> tuple[structures.StructureEventName, ...]:
        """Returns the event names the plugin can publish."""
        ...

    @property
    def licensing_status(self) -> str:
        """Returns the contract ``LicensingStatus``."""
        ...

    @property
    def parity_status(self) -> str:
        """Returns the contract ``ParityStatus``."""
        ...

    def input_schema(self) -> dict[str, object]:
        """Returns the JSON Schema of the plugin settings."""
        ...

    def default_settings(self) -> Mapping[str, settings_lib.SettingValue]:
        """Returns default settings."""
        ...

    def required_streams(
        self,
        settings: Mapping[str, object] | None,
        *,
        timeframe: tfs.Timeframe | None = None,
    ) -> frozenset[str]:
        """Returns the data streams needed for the given settings.

        Args:
            settings: Overrides (``None`` for defaults).
            timeframe: Series timeframe, when known (``None`` for the
                plugin descriptor), so a plugin can pick nesting intrabars.
        """
        ...

    def lookback(
        self, settings: Mapping[str, object] | None, stream: str
    ) -> int:
        """Returns how many bars of an aux stream the plugin reads.

        Args:
            settings: Overrides (``None`` for defaults).
            stream: Stream name.
        """
        ...

    def initialize(
        self,
        instrument_id: instruments.InstrumentId,
        timeframe: tfs.Timeframe,
        settings: Mapping[str, object] | None,
    ) -> Any:
        """Creates the initial state for one series.

        Args:
            instrument_id: Exact venue instrument.
            timeframe: Series timeframe.
            settings: Overrides (``None`` for defaults).
        """
        ...

    def on_bar_closed(
        self,
        state: Any,
        bar: BarInput,
    ) -> tuple[Any, PluginOutput]:
        """Applies one closed bar without mutating ``state``.

        Args:
            state: Previous state.
            bar: Closed bar and visible auxiliary data.
        """
        ...

    def snapshot(
        self,
        state: Any,
    ) -> structures.IndicatorSnapshot:
        """Publishes the state as an ``IndicatorSnapshot``.

        Args:
            state: Current state.
        """
        ...

    def dump_state(self, state: Any) -> object:
        """Encodes a JSON-compatible checkpoint.

        Args:
            state: Current state.
        """
        ...

    def restore_state(self, blob: object) -> Any:
        """Restores a checkpoint produced by ``dump_state``.

        Args:
            blob: Encoded checkpoint.
        """
        ...

    def info(self) -> dict[str, object]:
        """Returns the contract ``IndicatorPluginInfo`` object."""
        ...
