"""Plugin adapters for each ported Pine indicator.

Each adapter declares the plugin metadata, exposes the Pine inputs as
settings (same defaults and bounds as the Pine source), feeds the right data
streams to the library object and maps its outputs onto the published
values contract documented in ``joseph.domain.structures``. No indicator
rule is implemented here.
"""

from __future__ import annotations

import decimal
from collections.abc import Mapping
from collections.abc import Sequence
from typing import ClassVar

from smc_indicators import auction_value_areas
from smc_indicators import daily_bias
from smc_indicators import fvg_ifvg
from smc_indicators import liquidity_sweeps
from smc_indicators import market_structure
from smc_indicators import order_breaker_blocks
from smc_indicators import order_flow_pressure
from smc_indicators import premium_discount_ote
from smc_indicators import types as smc_types

from joseph.domain import decimals
from joseph.domain import structures
from joseph.domain import timeframes as tfs
from joseph.indicators.packaged import bridge
from joseph.indicators.packaged import convert
from joseph.indicators.sdk import protocol
from joseph.indicators.sdk import settings as settings_lib

_K = structures.AoiKind
_E = structures.StructureEventName
_S = settings_lib.SettingSpec
_INT = settings_lib.SettingKind.INT
_DEC = settings_lib.SettingKind.DECIMAL
_ENUM = settings_lib.SettingKind.ENUM
_D = decimal.Decimal
_PINE_DIR = "PineScripts/tradingview_indicators"

_STRUCTURE_EVENTS: tuple[structures.StructureEventName, ...] = (
    _E.BOS_BULL,
    _E.BOS_BEAR,
    _E.CHOCH_BULL,
    _E.CHOCH_BEAR,
    _E.INITIAL_BREAK_BULL,
    _E.INITIAL_BREAK_BEAR,
)

_EVENT_WORDS: Mapping[int, str] = {1: "BOS", 2: "CHOCH", 3: "INITIAL"}
"""Absolute Pine event code to contract ``last_event`` text."""

_BREAK_NAMES: Mapping[int, str] = {
    1: "BOS",
    2: "CHOCH",
    3: "INITIAL_BREAK",
}
"""Absolute Pine event code to contract event-name stem."""


def _int(settings: Mapping[str, settings_lib.SettingValue], name: str) -> int:
    """Shortcut for ``settings.get_int``.

    Args:
        settings: Resolved settings.
        name: Setting name.

    Returns:
        Integer value.
    """
    return settings_lib.get_int(settings, name)


def _main(payload: bridge.Payload, role: str = "main") -> object:
    """Returns a library object from a payload.

    Args:
        payload: Live payload.
        role: Object role.

    Returns:
        The object.
    """
    return payload.objects[role]


def _event_word(code: object) -> str:
    """Maps a signed Pine event code to ``BOS`` / ``CHOCH`` / ``INITIAL``.

    Args:
        code: Signed integer code.

    Returns:
        Contract text, ``NONE`` when no event happened yet.
    """
    if not isinstance(code, int) or isinstance(code, bool):
        return "NONE"
    return _EVENT_WORDS.get(abs(code), "NONE")


def _swing_spec(default: int, maximum: int = 20) -> settings_lib.SettingSpec:
    """Builds the common ``swing_length`` setting.

    Args:
        default: Pine default.
        maximum: Pine maximum.

    Returns:
        Setting spec.
    """
    return _S(
        "swing_length",
        _INT,
        default,
        "Pivot bars required on each side (Pine swingLength).",
        minimum=2,
        maximum=maximum,
    )


class MarketStructurePlugin(bridge.PackagedPlugin):
    """Current-timeframe swing structure (HH/HL/LH/LL, BOS/MSS)."""

    PLUGIN_ID = "market_structure"
    DISPLAY_NAME = "Current-timeframe structure"
    DESCRIPTION = (
        "Confirmed swing labels (HH/HL/LH/LL/EQH/EQL), BOS / MSS / initial "
        "breaks on closes, and the unbroken swing high and low."
    )
    PINE_SOURCE = f"{_PINE_DIR}/smc_current_timeframe_structure.pine"
    OUTPUT_KINDS = (_K.SWING_HIGH, _K.SWING_LOW)
    OUTPUT_EVENTS = (
        *_STRUCTURE_EVENTS,
        _E.SWING_HH,
        _E.SWING_HL,
        _E.SWING_LH,
        _E.SWING_LL,
        _E.SWING_EQH,
        _E.SWING_EQL,
    )
    SETTINGS = (_swing_spec(3), bridge.tick_spec())
    EVENT_ALIASES: ClassVar[Mapping[str, str]] = {
        "MSS_BULL": "CHOCH_BULL",
        "MSS_BEAR": "CHOCH_BEAR",
    }
    KIND_ALIASES: ClassVar[Mapping[str, str]] = {
        "PENDING_HIGH": "SWING_HIGH",
        "PENDING_LOW": "SWING_LOW",
    }

    def warmup_for(
        self, settings: Mapping[str, settings_lib.SettingValue]
    ) -> int:
        """Returns bars needed to confirm the first pivot.

        Args:
            settings: Resolved settings.

        Returns:
            ``2 * swing_length + 1``.
        """
        return 2 * _int(settings, "swing_length") + 1

    def _build(
        self,
        settings: Mapping[str, settings_lib.SettingValue],
        tick: decimal.Decimal,
        timeframe: tfs.Timeframe,
    ) -> dict[str, object]:
        """Creates the library object.

        Args:
            settings: Resolved settings.
            tick: Tick size.
            timeframe: Series timeframe.

        Returns:
            Objects by role.
        """
        del timeframe
        return {
            "main": market_structure.MarketStructure(
                float(tick), _int(settings, "swing_length")
            )
        }

    def _advance(
        self,
        payload: bridge.Payload,
        bar: protocol.BarInput,
        settings: Mapping[str, settings_lib.SettingValue],
        step: decimal.Decimal,
    ) -> bridge.LibraryStep:
        """Feeds one bar.

        Args:
            payload: Live payload.
            bar: Bar input.
            settings: Resolved settings.
            step: Price quantum.

        Returns:
            Library outputs and values.
        """
        del settings
        indicator = _main(payload)
        assert isinstance(indicator, market_structure.MarketStructure)
        result = indicator.update(convert.to_bar(bar.candle))
        raw = result.values
        code = raw.get("latest_event")
        sign = code if isinstance(code, int) else 0
        values: dict[str, structures.ValueType] = {
            "trend": convert.trend(raw.get("direction")),
            "last_event": (
                "MSS" if _event_word(code) == "CHOCH" else _event_word(code)
            ),
            "last_event_direction": convert.direction(sign),
            "event_age": _opt_int(raw.get("event_age")),
            "last_pivot_high": _price(raw.get("last_high"), step),
            "last_pivot_low": _price(raw.get("last_low"), step),
            "high_type": _opt_str(raw.get("high_type")),
            "low_type": _opt_str(raw.get("low_type")),
            "swing_alignment": _opt_str(raw.get("swing_alignment")),
            "agreement": _opt_str(raw.get("agreement")),
            "pending_high": _price(raw.get("pending_high"), step),
            "pending_low": _price(raw.get("pending_low"), step),
        }
        return bridge.LibraryStep(
            zones=result.zones, events=result.events, values=values
        )

    def _end_state(
        self,
        zone_id: str,
        events: Sequence[smc_types.Event],
        current_zone_ids: frozenset[str],
    ) -> structures.AoiState:
        """A pending level ends invalidated when broken, else superseded.

        Args:
            zone_id: Library zone id.
            events: Library events of the bar.
            current_zone_ids: Library ids still live.

        Returns:
            Terminal state.
        """
        del current_zone_ids
        for event in events:
            pivot = event.meta.get("pivot_time_ms")
            if pivot is None:
                continue
            stem = "high" if event.name.endswith("_BULL") else "low"
            if zone_id == f"pending-{stem}-{pivot}":
                return structures.AoiState.INVALIDATED
        return structures.AoiState.EXPIRED


class DailyBiasPlugin(bridge.PackagedPlugin):
    """Per-timeframe structure plus the Pine daily-bias dashboard."""

    PLUGIN_ID = "daily_bias"
    DISPLAY_NAME = "Daily bias"
    DESCRIPTION = (
        "Close-confirmed BOS / CHoCH structure on this timeframe, the "
        "1D/4H/1H consensus, previous-day high/low and New York session "
        "ranges."
    )
    PINE_SOURCE = f"{_PINE_DIR}/smc_daily_bias_dashboard.pine"
    OUTPUT_KINDS = (_K.PDH, _K.PDL, _K.SESSION_HIGH, _K.SESSION_LOW)
    OUTPUT_EVENTS = _STRUCTURE_EVENTS
    SETTINGS = (_swing_spec(3), bridge.tick_spec())
    AUX_LOOKBACK: ClassVar[int] = 64
    AUX: ClassVar[tuple[tfs.Timeframe, ...]] = (
        tfs.Timeframe.D1,
        tfs.Timeframe.H4,
        tfs.Timeframe.H1,
    )

    def warmup_for(
        self, settings: Mapping[str, settings_lib.SettingValue]
    ) -> int:
        """Returns bars needed to confirm the first pivot.

        Args:
            settings: Resolved settings.

        Returns:
            ``2 * swing_length + 1``.
        """
        return 2 * _int(settings, "swing_length") + 1

    def required_streams(
        self, settings: Mapping[str, object] | None
    ) -> frozenset[str]:
        """Chart bars plus completed 1D, 4H and 1H bars.

        Args:
            settings: Overrides.

        Returns:
            Stream names.
        """
        del settings
        return frozenset(
            {
                protocol.CHART_STREAM,
                *(protocol.aux_stream(tf) for tf in self.AUX),
            }
        )

    def lookback(
        self, settings: Mapping[str, object] | None, stream: str
    ) -> int:
        """Supplies enough completed bars to confirm HTF pivots.

        Args:
            settings: Overrides.
            stream: Stream name.

        Returns:
            Bars per aux stream.
        """
        del settings
        kind, _ = protocol.parse_stream(stream)
        return self.AUX_LOOKBACK if kind == "aux" else 0

    def _build(
        self,
        settings: Mapping[str, settings_lib.SettingValue],
        tick: decimal.Decimal,
        timeframe: tfs.Timeframe,
    ) -> dict[str, object]:
        """Creates the series tracker and the dashboard.

        Args:
            settings: Resolved settings.
            tick: Tick size.
            timeframe: Series timeframe.

        Returns:
            Objects by role.
        """
        del timeframe
        span = _int(settings, "swing_length")
        return {
            "series": daily_bias.StructureTracker(span),
            "dashboard": daily_bias.DailyBiasDashboard(
                float(tick), swing_length=span
            ),
        }

    def _advance(
        self,
        payload: bridge.Payload,
        bar: protocol.BarInput,
        settings: Mapping[str, settings_lib.SettingValue],
        step: decimal.Decimal,
    ) -> bridge.LibraryStep:
        """Feeds one bar to both objects.

        Args:
            payload: Live payload.
            bar: Bar input.
            settings: Resolved settings.
            step: Price quantum.

        Returns:
            Library outputs and values.
        """
        del settings
        tracker = _main(payload, "series")
        dashboard = _main(payload, "dashboard")
        assert isinstance(tracker, daily_bias.StructureTracker)
        assert isinstance(dashboard, daily_bias.DailyBiasDashboard)
        chart_bar = convert.to_bar(bar.candle)
        structure = tracker.update(chart_bar)
        dashboard.push_daily(self.new_aux(payload, bar, tfs.Timeframe.D1))
        dashboard.push_four_hour(self.new_aux(payload, bar, tfs.Timeframe.H4))
        dashboard.push_hourly(self.new_aux(payload, bar, tfs.Timeframe.H1))
        board = dashboard.update(chart_bar)
        raw = board.values
        events: list[smc_types.Event] = []
        if structure.event_raised and structure.last_event:
            stem = _BREAK_NAMES[abs(structure.last_event)]
            suffix = "BULL" if structure.last_event > 0 else "BEAR"
            events.append(
                smc_types.Event(
                    name=f"{stem}_{suffix}",
                    time_ms=chart_bar.time_ms,
                    price=structure.event_price,
                )
            )
        values: dict[str, structures.ValueType] = {
            "trend": convert.trend(structure.direction),
            "last_event": _event_word(structure.last_event),
            "last_event_direction": convert.direction(structure.last_event),
            "event_age": structure.event_age,
            "last_pivot_high": convert.price(structure.last_high, step),
            "last_pivot_low": convert.price(structure.last_low, step),
            "pdh": _price(raw.get("previous_day_high"), step),
            "pdl": _price(raw.get("previous_day_low"), step),
            "previous_day_mid": _price(raw.get("previous_day_mid"), step),
            "range_location": _opt_str(raw.get("range_location")),
            "session": _session(raw),
            "bias": convert.trend(raw.get("bias")),
            "agreement": _opt_str(raw.get("agreement")),
            "d1_trend": convert.trend(raw.get("d1_direction")),
            "h4_trend": convert.trend(raw.get("h4_direction")),
            "h1_trend": convert.trend(raw.get("h1_direction")),
        }
        return bridge.LibraryStep(
            zones=board.zones, events=tuple(events), values=values
        )


class FvgIfvgPlugin(bridge.PackagedPlugin):
    """Fair value gaps and their inversions."""

    PLUGIN_ID = "fvg_ifvg"
    DISPLAY_NAME = "FVG / IFVG"
    DESCRIPTION = (
        "Three-candle fair value gaps with heat scores; a close through a "
        "gap inverts it into an IFVG."
    )
    PINE_SOURCE = f"{_PINE_DIR}/smc_fvg_ifvg.pine"
    OUTPUT_KINDS = (_K.FVG, _K.IFVG)
    OUTPUT_EVENTS = (
        _E.FVG_FORMED,
        _E.IFVG_FORMED,
        _E.AOI_INVALIDATED,
        _E.AOI_EXPIRED,
    )
    SETTINGS = (
        _S(
            "min_gap_ticks",
            _INT,
            1,
            "Minimum gap in ticks.",
            minimum=1,
            maximum=100,
        ),
        _S(
            "min_gap_atr",
            _DEC,
            _D("0.10"),
            "Minimum gap in ATR(14) units (0 disables).",
            minimum=_D("0"),
            maximum=_D("5"),
        ),
        _S(
            "max_zones",
            _INT,
            30,
            "Zones kept (oldest dropped).",
            minimum=1,
            maximum=100,
        ),
        _S(
            "max_age_bars",
            _INT,
            500,
            "Zone lifetime in bars.",
            minimum=10,
            maximum=3000,
        ),
        _S(
            "first_test_factor",
            _DEC,
            _D("0.70"),
            "Score multiplier on the first midpoint test.",
            minimum=_D("0.10"),
            maximum=_D("1"),
        ),
        bridge.tick_spec(),
    )

    def warmup_for(
        self, settings: Mapping[str, settings_lib.SettingValue]
    ) -> int:
        """Returns bars until ATR(14) is available.

        Args:
            settings: Resolved settings.

        Returns:
            Warm-up bars.
        """
        del settings
        return 14

    def _build(
        self,
        settings: Mapping[str, settings_lib.SettingValue],
        tick: decimal.Decimal,
        timeframe: tfs.Timeframe,
    ) -> dict[str, object]:
        """Creates the library object.

        Args:
            settings: Resolved settings.
            tick: Tick size.
            timeframe: Series timeframe.

        Returns:
            Objects by role.
        """
        del timeframe
        return {
            "main": fvg_ifvg.FvgIfvg(
                float(tick),
                min_gap_ticks=_int(settings, "min_gap_ticks"),
                min_gap_atr=bridge.float_setting(settings, "min_gap_atr"),
                max_zones=_int(settings, "max_zones"),
                max_age_bars=_int(settings, "max_age_bars"),
                first_test_factor=bridge.float_setting(
                    settings, "first_test_factor"
                ),
            )
        }

    def _advance(
        self,
        payload: bridge.Payload,
        bar: protocol.BarInput,
        settings: Mapping[str, settings_lib.SettingValue],
        step: decimal.Decimal,
    ) -> bridge.LibraryStep:
        """Feeds one bar.

        Args:
            payload: Live payload.
            bar: Bar input.
            settings: Resolved settings.
            step: Price quantum.

        Returns:
            Library outputs and values.
        """
        del settings
        indicator = _main(payload)
        assert isinstance(indicator, fvg_ifvg.FvgIfvg)
        result = indicator.update(convert.to_bar(bar.candle))
        return _zone_step(result, step)


class OrderBreakerBlocksPlugin(bridge.PackagedPlugin):
    """Order blocks and breaker blocks."""

    PLUGIN_ID = "order_breaker_blocks"
    DISPLAY_NAME = "Order / breaker blocks"
    DESCRIPTION = (
        "The last opposite candle before a displacement break of a swing; "
        "a close through the block converts it into a breaker."
    )
    PINE_SOURCE = f"{_PINE_DIR}/smc_order_breaker_blocks.pine"
    OUTPUT_KINDS = (_K.OB, _K.BREAKER)
    OUTPUT_EVENTS = (
        _E.OB_FORMED,
        _E.BREAKER_FORMED,
        _E.AOI_INVALIDATED,
        _E.AOI_EXPIRED,
    )
    SETTINGS = (
        _swing_spec(3),
        _S(
            "search_bars",
            _INT,
            12,
            "Bars searched for the block candle.",
            minimum=2,
            maximum=50,
        ),
        _S(
            "min_break_body_atr",
            _DEC,
            _D("0.50"),
            "Minimum break-candle body in ATR(14) units.",
            minimum=_D("0"),
            maximum=_D("5"),
        ),
        _S(
            "max_zones",
            _INT,
            30,
            "Zones kept (oldest dropped).",
            minimum=1,
            maximum=100,
        ),
        _S(
            "max_age_bars",
            _INT,
            500,
            "Zone lifetime in bars.",
            minimum=10,
            maximum=3000,
        ),
        _S(
            "first_test_factor",
            _DEC,
            _D("0.70"),
            "Score multiplier on the first touch.",
            minimum=_D("0.10"),
            maximum=_D("1"),
        ),
        bridge.tick_spec(),
    )

    def warmup_for(
        self, settings: Mapping[str, settings_lib.SettingValue]
    ) -> int:
        """Returns bars until ATR(14) and the first pivot exist.

        Args:
            settings: Resolved settings.

        Returns:
            Warm-up bars.
        """
        return max(14, 2 * _int(settings, "swing_length") + 1)

    def _build(
        self,
        settings: Mapping[str, settings_lib.SettingValue],
        tick: decimal.Decimal,
        timeframe: tfs.Timeframe,
    ) -> dict[str, object]:
        """Creates the library object.

        Args:
            settings: Resolved settings.
            tick: Tick size.
            timeframe: Series timeframe.

        Returns:
            Objects by role.
        """
        del timeframe
        return {
            "main": order_breaker_blocks.OrderBreakerBlocks(
                float(tick),
                swing_length=_int(settings, "swing_length"),
                search_bars=_int(settings, "search_bars"),
                min_break_body_atr=bridge.float_setting(
                    settings, "min_break_body_atr"
                ),
                max_zones=_int(settings, "max_zones"),
                max_age_bars=_int(settings, "max_age_bars"),
                first_test_factor=bridge.float_setting(
                    settings, "first_test_factor"
                ),
            )
        }

    def _advance(
        self,
        payload: bridge.Payload,
        bar: protocol.BarInput,
        settings: Mapping[str, settings_lib.SettingValue],
        step: decimal.Decimal,
    ) -> bridge.LibraryStep:
        """Feeds one bar.

        Args:
            payload: Live payload.
            bar: Bar input.
            settings: Resolved settings.
            step: Price quantum.

        Returns:
            Library outputs and values.
        """
        del settings
        indicator = _main(payload)
        assert isinstance(indicator, order_breaker_blocks.OrderBreakerBlocks)
        result = indicator.update(convert.to_bar(bar.candle))
        return _zone_step(result, step)


class LiquiditySweepsPlugin(bridge.PackagedPlugin):
    """Buy-side / sell-side liquidity pools and their sweeps."""

    PLUGIN_ID = "liquidity_sweeps"
    DISPLAY_NAME = "Liquidity sweeps"
    DESCRIPTION = (
        "Swing-high (BSL) and swing-low (SSL) pools merged into EQH / EQL "
        "within tolerance; a wick through and close back is a sweep."
    )
    PINE_SOURCE = f"{_PINE_DIR}/smc_liquidity_sweeps.pine"
    OUTPUT_KINDS = (_K.BSL, _K.SSL, _K.EQH, _K.EQL)
    OUTPUT_EVENTS = (
        _E.BSL_SWEEP,
        _E.SSL_SWEEP,
        _E.BSL_BREAK,
        _E.SSL_BREAK,
        _E.AOI_EXPIRED,
    )
    SETTINGS = (
        _swing_spec(3),
        _S(
            "equal_ticks",
            _INT,
            2,
            "Equal-level tolerance in ticks.",
            minimum=0,
            maximum=100,
        ),
        _S(
            "equal_atr",
            _DEC,
            _D(0),
            "Equal-level tolerance in ATR(14) units.",
            minimum=_D("0"),
            maximum=_D("2"),
        ),
        _S(
            "equal_window",
            _INT,
            100,
            "Bars between merged pivots.",
            minimum=1,
            maximum=500,
        ),
        _S(
            "sweep_ticks",
            _INT,
            1,
            "Minimum penetration in ticks.",
            minimum=1,
            maximum=100,
        ),
        _S(
            "full_depth_atr",
            _DEC,
            _D("0.75"),
            "Penetration (ATR units) scoring full depth.",
            minimum=_D("0.1"),
            maximum=_D("5"),
        ),
        _S(
            "full_reclaim_atr",
            _DEC,
            _D("0.50"),
            "Reclaim (ATR units) scoring a full reclaim.",
            minimum=_D("0.1"),
            maximum=_D("5"),
        ),
        _S("max_active", _INT, 60, "Live pools kept.", minimum=1, maximum=100),
        _S(
            "max_swept",
            _INT,
            100,
            "Swept pools kept for display.",
            minimum=1,
            maximum=150,
        ),
        _S(
            "max_age_bars",
            _INT,
            1000,
            "Pool lifetime in bars.",
            minimum=10,
            maximum=5000,
        ),
        bridge.tick_spec(),
    )

    def warmup_for(
        self, settings: Mapping[str, settings_lib.SettingValue]
    ) -> int:
        """Returns bars until ATR(14) and the first pivot exist.

        Args:
            settings: Resolved settings.

        Returns:
            Warm-up bars.
        """
        return max(14, 2 * _int(settings, "swing_length") + 1)

    def _key(self, zone: smc_types.Zone) -> str:
        """A pool keeps its identity when it becomes EQH / EQL.

        Args:
            zone: Library zone.

        Returns:
            The library zone id.
        """
        return zone.zone_id

    def _build(
        self,
        settings: Mapping[str, settings_lib.SettingValue],
        tick: decimal.Decimal,
        timeframe: tfs.Timeframe,
    ) -> dict[str, object]:
        """Creates the library object.

        Args:
            settings: Resolved settings.
            tick: Tick size.
            timeframe: Series timeframe.

        Returns:
            Objects by role.
        """
        del timeframe
        return {
            "main": liquidity_sweeps.LiquiditySweeps(
                float(tick),
                swing_length=_int(settings, "swing_length"),
                equal_ticks=_int(settings, "equal_ticks"),
                equal_atr=bridge.float_setting(settings, "equal_atr"),
                equal_window=_int(settings, "equal_window"),
                sweep_ticks=_int(settings, "sweep_ticks"),
                full_depth_atr=bridge.float_setting(settings, "full_depth_atr"),
                full_reclaim_atr=bridge.float_setting(
                    settings, "full_reclaim_atr"
                ),
                max_active=_int(settings, "max_active"),
                max_swept=_int(settings, "max_swept"),
                max_age_bars=_int(settings, "max_age_bars"),
            )
        }

    def _advance(
        self,
        payload: bridge.Payload,
        bar: protocol.BarInput,
        settings: Mapping[str, settings_lib.SettingValue],
        step: decimal.Decimal,
    ) -> bridge.LibraryStep:
        """Feeds one bar.

        Args:
            payload: Live payload.
            bar: Bar input.
            settings: Resolved settings.
            step: Price quantum.

        Returns:
            Library outputs and values.
        """
        del settings
        indicator = _main(payload)
        assert isinstance(indicator, liquidity_sweeps.LiquiditySweeps)
        result = indicator.update(convert.to_bar(bar.candle))
        live = [zone for zone in result.zones if zone.state != "swept"]
        return bridge.LibraryStep(
            zones=live,
            events=result.events,
            values={"active_count": len(live)},
        )


class OrderFlowPressurePlugin(bridge.PackagedPlugin):
    """Intrabar delta estimate with failure and breakout signals."""

    PLUGIN_ID = "order_flow_pressure"
    DISPLAY_NAME = "Order-flow pressure"
    DESCRIPTION = (
        "Signed intrabar volume share (candle-direction estimate) with "
        "failed-auction and breakout signals beyond the 20-bar range."
    )
    PINE_SOURCE = f"{_PINE_DIR}/smc_order_flow_pressure.pine"
    OUTPUT_EVENTS = (
        _E.BUYER_FAILURE,
        _E.SELLER_FAILURE,
        _E.BUYER_BREAKOUT,
        _E.SELLER_BREAKOUT,
    )
    SETTINGS = (
        _S(
            "intrabar_tf",
            _ENUM,
            "1m",
            "Lower timeframe used for the delta estimate.",
            choices=("1m", "3m", "5m", "15m"),
        ),
        _S(
            "min_intrabars",
            _INT,
            5,
            "Intrabars required.",
            minimum=1,
            maximum=100,
        ),
        _S(
            "min_coverage",
            _DEC,
            _D("0.80"),
            "Share of chart volume the intrabars must cover.",
            minimum=_D("0.50"),
            maximum=_D("1"),
        ),
        _S(
            "lookback_bars",
            _INT,
            20,
            "Prior-range and volume lookback.",
            minimum=5,
            maximum=100,
        ),
        _S(
            "min_relative_volume",
            _DEC,
            _D("1.50"),
            "Volume versus the prior average.",
            minimum=_D("1"),
            maximum=_D("5"),
        ),
        _S(
            "min_delta_share",
            _DEC,
            _D("0.20"),
            "Minimum |delta| share of covered volume.",
            minimum=_D("0.05"),
            maximum=_D("0.90"),
        ),
        _S(
            "min_rejection_wick",
            _DEC,
            _D("0.35"),
            "Rejection wick share of the range (failures).",
            minimum=_D("0.20"),
            maximum=_D("0.80"),
        ),
        _S(
            "min_breakout_body",
            _DEC,
            _D("0.50"),
            "Body share of the range (breakouts).",
            minimum=_D("0.20"),
            maximum=_D("0.90"),
        ),
        _S(
            "max_close_from_extreme",
            _DEC,
            _D("0.25"),
            "Maximum distance of the close from the extreme (breakouts).",
            minimum=_D("0.05"),
            maximum=_D("0.50"),
        ),
        bridge.tick_spec(),
    )

    def warmup_for(
        self, settings: Mapping[str, settings_lib.SettingValue]
    ) -> int:
        """Returns bars until the prior range exists.

        Args:
            settings: Resolved settings.

        Returns:
            ``lookback_bars + 1``.
        """
        return _int(settings, "lookback_bars") + 1

    def required_streams(
        self, settings: Mapping[str, object] | None
    ) -> frozenset[str]:
        """Chart bars plus intrabars of the configured lower timeframe.

        Args:
            settings: Overrides.

        Returns:
            Stream names.
        """
        resolved = self.resolve_settings(settings)
        intrabar = tfs.parse(settings_lib.get_str(resolved, "intrabar_tf"))
        return frozenset(
            {protocol.CHART_STREAM, protocol.intrabar_stream(intrabar)}
        )

    def _build(
        self,
        settings: Mapping[str, settings_lib.SettingValue],
        tick: decimal.Decimal,
        timeframe: tfs.Timeframe,
    ) -> dict[str, object]:
        """Creates the library object.

        Args:
            settings: Resolved settings.
            tick: Tick size.
            timeframe: Series timeframe.

        Returns:
            Objects by role.
        """
        del timeframe
        return {
            "main": order_flow_pressure.OrderFlowPressure(
                float(tick),
                min_intrabars=_int(settings, "min_intrabars"),
                min_coverage=bridge.float_setting(settings, "min_coverage"),
                lookback_bars=_int(settings, "lookback_bars"),
                min_relative_volume=bridge.float_setting(
                    settings, "min_relative_volume"
                ),
                min_delta_share=bridge.float_setting(
                    settings, "min_delta_share"
                ),
                min_rejection_wick=bridge.float_setting(
                    settings, "min_rejection_wick"
                ),
                min_breakout_body=bridge.float_setting(
                    settings, "min_breakout_body"
                ),
                max_close_from_extreme=bridge.float_setting(
                    settings, "max_close_from_extreme"
                ),
            )
        }

    def _advance(
        self,
        payload: bridge.Payload,
        bar: protocol.BarInput,
        settings: Mapping[str, settings_lib.SettingValue],
        step: decimal.Decimal,
    ) -> bridge.LibraryStep:
        """Feeds one bar and its intrabars.

        Args:
            payload: Live payload.
            bar: Bar input.
            settings: Resolved settings.
            step: Price quantum.

        Returns:
            Library outputs and values.
        """
        indicator = _main(payload)
        assert isinstance(indicator, order_flow_pressure.OrderFlowPressure)
        intrabar_tf = tfs.parse(settings_lib.get_str(settings, "intrabar_tf"))
        intrabars = [
            convert.to_bar(candle)
            for candle in bar.intrabars.get(intrabar_tf, ())
        ]
        result = indicator.update(convert.to_bar(bar.candle), intrabars)
        raw = result.values
        coverage = convert.ratio(_opt_float(raw.get("coverage_percent")), 2)
        values: dict[str, structures.ValueType] = {
            "delta_pct": convert.ratio(_opt_float(raw.get("delta_percent")), 2),
            "coverage_pct": coverage,
            "intrabar_count": _opt_int(raw.get("intrabar_count")) or 0,
            "rel_volume": convert.ratio(
                _opt_float(raw.get("relative_volume")), 4
            ),
            "method": "candle_direction",
            "prior_high": _price(raw.get("prior_high"), step),
            "prior_low": _price(raw.get("prior_low"), step),
        }
        return bridge.LibraryStep(
            zones=(),
            events=result.events,
            values=values,
            coverage_pct=(
                decimals.format_decimal(coverage)
                if coverage is not None
                else None
            ),
        )


class AuctionValueAreasPlugin(bridge.PackagedPlugin):
    """Volume-profile value areas of the last 24 hours and 20 bars."""

    PLUGIN_ID = "auction_value_areas"
    DISPLAY_NAME = "Auction value areas"
    DESCRIPTION = (
        "Value area high, point of control and value area low of the "
        "completed 1H window and of the recent chart bars."
    )
    PINE_SOURCE = f"{_PINE_DIR}/smc_auction_value_areas.pine"
    TIMEFRAMES = (
        tfs.Timeframe.M1,
        tfs.Timeframe.M3,
        tfs.Timeframe.M5,
        tfs.Timeframe.M15,
        tfs.Timeframe.M30,
    )
    OUTPUT_KINDS = (_K.VAH, _K.POC, _K.VAL)
    OUTPUT_EVENTS = (_E.VALUE_AREA_UPDATED,)
    SETTINGS = (
        _S(
            "hourly_bars",
            _INT,
            24,
            "Completed 1H bars profiled.",
            minimum=4,
            maximum=48,
        ),
        _S(
            "chart_bars",
            _INT,
            20,
            "Completed chart bars profiled.",
            minimum=5,
            maximum=120,
        ),
        _S(
            "profile_rows",
            _INT,
            32,
            "Price rows per profile.",
            minimum=16,
            maximum=64,
        ),
        _S(
            "value_area_percent",
            _INT,
            70,
            "Volume share of the value area.",
            minimum=50,
            maximum=90,
        ),
        bridge.tick_spec(),
    )

    def warmup_for(
        self, settings: Mapping[str, settings_lib.SettingValue]
    ) -> int:
        """Returns bars until the chart profile is complete.

        Args:
            settings: Resolved settings.

        Returns:
            ``chart_bars + 1``.
        """
        return _int(settings, "chart_bars") + 1

    def required_streams(
        self, settings: Mapping[str, object] | None
    ) -> frozenset[str]:
        """Chart bars plus completed 1H bars.

        Args:
            settings: Overrides.

        Returns:
            Stream names.
        """
        del settings
        return frozenset(
            {protocol.CHART_STREAM, protocol.aux_stream(tfs.Timeframe.H1)}
        )

    def lookback(
        self, settings: Mapping[str, object] | None, stream: str
    ) -> int:
        """Supplies the hourly window.

        Args:
            settings: Overrides.
            stream: Stream name.

        Returns:
            Bars per aux stream.
        """
        kind, _ = protocol.parse_stream(stream)
        if kind != "aux":
            return 0
        return _int(self.resolve_settings(settings), "hourly_bars") + 2

    def _build(
        self,
        settings: Mapping[str, settings_lib.SettingValue],
        tick: decimal.Decimal,
        timeframe: tfs.Timeframe,
    ) -> dict[str, object]:
        """Creates the library object.

        Args:
            settings: Resolved settings.
            tick: Tick size.
            timeframe: Series timeframe.

        Returns:
            Objects by role.
        """
        return {
            "main": auction_value_areas.AuctionValueAreas(
                float(tick),
                timeframe_seconds=timeframe.duration_ms // 1000,
                hourly_bars=_int(settings, "hourly_bars"),
                chart_bars=_int(settings, "chart_bars"),
                profile_rows=_int(settings, "profile_rows"),
                value_area_percent=_int(settings, "value_area_percent"),
            )
        }

    def _advance(
        self,
        payload: bridge.Payload,
        bar: protocol.BarInput,
        settings: Mapping[str, settings_lib.SettingValue],
        step: decimal.Decimal,
    ) -> bridge.LibraryStep:
        """Feeds hourly bars and one chart bar.

        Args:
            payload: Live payload.
            bar: Bar input.
            settings: Resolved settings.
            step: Price quantum.

        Returns:
            Library outputs and values.
        """
        del settings
        indicator = _main(payload)
        assert isinstance(indicator, auction_value_areas.AuctionValueAreas)
        indicator.push_hourly(self.new_aux(payload, bar, tfs.Timeframe.H1))
        result = indicator.update(convert.to_bar(bar.candle))
        values = {
            published: _price(result.values.get(library), step)
            for published, library in (
                ("vah", "chart_vah"),
                ("poc", "chart_poc"),
                ("val", "chart_val"),
                ("vah_1h", "hourly_vah"),
                ("poc_1h", "hourly_poc"),
                ("val_1h", "hourly_val"),
            )
        }
        return bridge.LibraryStep(
            zones=result.zones, events=result.events, values=values
        )


class PremiumDiscountOtePlugin(bridge.PackagedPlugin):
    """Dealing range premium / discount and OTE zones (LuxAlgo-derived)."""

    PLUGIN_ID = "premium_discount_ote"
    DISPLAY_NAME = "Premium / discount / OTE"
    DESCRIPTION = (
        "Swing dealing range with premium, discount, equilibrium and the "
        "0.62-0.79 optimal trade entry band. Derived from LuxAlgo "
        "(CC BY-NC-SA 4.0)."
    )
    PINE_SOURCE = f"{_PINE_DIR}/smc_premium_discount_ote_zones.pine"
    OUTPUT_KINDS = (_K.PREMIUM, _K.DISCOUNT, _K.EQUILIBRIUM, _K.OTE)
    OUTPUT_EVENTS = (_E.RANGE_RESET,)
    EVENT_ALIASES: ClassVar[Mapping[str, str]] = {
        "DEALING_RANGE_NEW": "RANGE_RESET",
    }
    SETTINGS = (
        _swing_spec(10, maximum=50),
        _S(
            "break_confirmation",
            _ENUM,
            "Close",
            "What confirms a range break: the close or the wick.",
            choices=("Close", "Wick"),
        ),
        bridge.tick_spec(),
    )

    def warmup_for(
        self, settings: Mapping[str, settings_lib.SettingValue]
    ) -> int:
        """Returns bars needed for the first pivots.

        Args:
            settings: Resolved settings.

        Returns:
            ``2 * swing_length + 1``.
        """
        return 2 * _int(settings, "swing_length") + 1

    def _build(
        self,
        settings: Mapping[str, settings_lib.SettingValue],
        tick: decimal.Decimal,
        timeframe: tfs.Timeframe,
    ) -> dict[str, object]:
        """Creates the library object.

        Args:
            settings: Resolved settings.
            tick: Tick size.
            timeframe: Series timeframe.

        Returns:
            Objects by role.
        """
        del timeframe
        confirmation = settings_lib.get_str(settings, "break_confirmation")
        return {
            "main": premium_discount_ote.PremiumDiscountOte(
                float(tick),
                swing_length=_int(settings, "swing_length"),
                break_confirmation=(
                    "Wick" if confirmation == "Wick" else "Close"
                ),
            )
        }

    def _advance(
        self,
        payload: bridge.Payload,
        bar: protocol.BarInput,
        settings: Mapping[str, settings_lib.SettingValue],
        step: decimal.Decimal,
    ) -> bridge.LibraryStep:
        """Feeds one bar.

        Args:
            payload: Live payload.
            bar: Bar input.
            settings: Resolved settings.
            step: Price quantum.

        Returns:
            Library outputs and values.
        """
        del settings
        indicator = _main(payload)
        assert isinstance(indicator, premium_discount_ote.PremiumDiscountOte)
        result = indicator.update(convert.to_bar(bar.candle))
        raw = result.values
        premium = _opt_bool(raw.get("in_premium"))
        values: dict[str, structures.ValueType] = {
            "range_id": _opt_str(raw.get("range_id")),
            "direction": convert.direction(raw.get("direction")),
            "range_high": _price(raw.get("range_high"), step),
            "range_low": _price(raw.get("range_low"), step),
            "eq": _price(raw.get("eq"), step),
            "ote_lower": _price(raw.get("ote_bottom"), step),
            "ote_upper": _price(raw.get("ote_top"), step),
            "in_premium": premium,
            "in_discount": None if premium is None else not premium,
            "in_ote": _opt_bool(raw.get("in_ote")),
            "position_pct": convert.ratio(
                _opt_float(raw.get("position_percent")), 2
            ),
            "zone": _opt_str(raw.get("zone_text")),
            "read": _opt_str(raw.get("read")),
        }
        return bridge.LibraryStep(
            zones=result.zones, events=result.events, values=values
        )


def _zone_step(
    result: smc_types.Result, step: decimal.Decimal
) -> bridge.LibraryStep:
    """Builds a step for zone-only indicators (``active_count`` + ATR).

    Args:
        result: Library result.
        step: Price quantum.

    Returns:
        Library step.
    """
    return bridge.LibraryStep(
        zones=result.zones,
        events=result.events,
        values={
            "active_count": len(result.zones),
            "atr": _price(result.values.get("atr"), step),
        },
    )


def _session(raw: Mapping[str, smc_types.Value]) -> str:
    """Names the dashboard session active on the bar.

    Args:
        raw: Dashboard values.

    Returns:
        ``ASIA``, ``LONDON``, ``NEW_YORK`` or ``NONE``.
    """
    for name, key in (
        ("ASIA", "asia_active"),
        ("LONDON", "london_active"),
        ("NEW_YORK", "new_york_active"),
    ):
        if raw.get(key) is True:
            return name
    return "NONE"


def _price(
    value: smc_types.Value, step: decimal.Decimal
) -> decimal.Decimal | None:
    """Converts an optional numeric library value to a price.

    Args:
        value: Library value.
        step: Price quantum.

    Returns:
        Decimal price or ``None``.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return convert.price(float(value), step)


def _opt_float(value: smc_types.Value) -> float | None:
    """Returns a numeric library value as float.

    Args:
        value: Library value.

    Returns:
        Float or ``None``.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _opt_int(value: smc_types.Value) -> int | None:
    """Returns an integer library value.

    Args:
        value: Library value.

    Returns:
        Integer or ``None``.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _opt_str(value: smc_types.Value) -> str | None:
    """Returns a text library value.

    Args:
        value: Library value.

    Returns:
        Text or ``None``.
    """
    return value if isinstance(value, str) else None


def _opt_bool(value: smc_types.Value) -> bool | None:
    """Returns a boolean library value.

    Args:
        value: Library value.

    Returns:
        Boolean or ``None``.
    """
    return value if isinstance(value, bool) else None


def default_plugins() -> tuple[bridge.PackagedPlugin, ...]:
    """Instantiates every packaged plugin in display order.

    Returns:
        Plugins.
    """
    return (
        DailyBiasPlugin(),
        MarketStructurePlugin(),
        FvgIfvgPlugin(),
        OrderBreakerBlocksPlugin(),
        LiquiditySweepsPlugin(),
        OrderFlowPressurePlugin(),
        AuctionValueAreasPlugin(),
        PremiumDiscountOtePlugin(),
    )
