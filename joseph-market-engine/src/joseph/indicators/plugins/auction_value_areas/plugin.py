"""Rolling value areas (VAH / POC / VAL) from candle volume profiles.

Clean-room implementation of V5 §6.2-F (candle uniform approximation; a
future trade-based profile must be a separate ``method=trade`` plugin). See
README.md for assumptions.
"""

from __future__ import annotations

import dataclasses
import decimal
import itertools
from collections.abc import Mapping
from collections.abc import Sequence

from joseph.domain import decimals
from joseph.domain import ids
from joseph.domain import structures
from joseph.domain import timeframes
from joseph.indicators.sdk import base
from joseph.indicators.sdk import protocol
from joseph.indicators.sdk import settings as settings_lib
from joseph.indicators.sdk import ta

_EVENTS = structures.StructureEventName
_KIND = structures.AoiKind
_HTF = timeframes.Timeframe.H1
_HTF_STREAM = protocol.aux_stream(_HTF)


@dataclasses.dataclass(frozen=True, slots=True)
class Profile:
    """A computed value area.

    Attributes:
        vah: Value-area high.
        poc: Point of control (bin midpoint).
        val: Value-area low.
        window_start_ms: Start of the first bar in the window.
        window_end_ms: End of the last bar in the window.
    """

    vah: decimal.Decimal
    poc: decimal.Decimal
    val: decimal.Decimal
    window_start_ms: int
    window_end_ms: int


def build_profile(
    bars: Sequence[ta.BarPoint], bins: int, value_area_pct: decimal.Decimal
) -> Profile | None:
    """Builds a volume profile with uniform per-bar distribution.

    Each bar's volume is spread over the bins its high-low range overlaps,
    proportionally to the overlap. A zero-range bar puts its volume into
    the containing bin. The value area grows from the POC toward the larger
    adjacent bin (ties expand upward) until it holds ``value_area_pct``.

    Args:
        bars: Contiguous completed bars, oldest first.
        bins: Number of price bins.
        value_area_pct: Target share of volume, e.g. ``70``.

    Returns:
        The profile, or ``None`` when there is no volume or no bars.
    """
    if not bars:
        return None
    low = min(bar.low for bar in bars)
    high = max(bar.high for bar in bars)
    total_volume = sum((bar.volume for bar in bars), decimals.ZERO)
    if total_volume <= decimals.ZERO:
        return None
    start, end = bars[0].start_ms, bars[-1].end_ms
    if high == low:
        level = ta.quantize_price(high)
        return Profile(level, level, level, start, end)
    width = (high - low) / bins
    volumes = [decimals.ZERO] * bins
    for bar in bars:
        if bar.volume <= decimals.ZERO:
            continue
        span = bar.high - bar.low
        if span == decimals.ZERO:
            index = min(int((bar.high - low) / width), bins - 1)
            volumes[index] += bar.volume
            continue
        first = min(int((bar.low - low) / width), bins - 1)
        last = min(int((bar.high - low) / width), bins - 1)
        for index in range(first, last + 1):
            bin_low = low + width * index
            bin_high = bin_low + width
            overlap = min(bin_high, bar.high) - max(bin_low, bar.low)
            if overlap > decimals.ZERO:
                volumes[index] += bar.volume * overlap / span
    center = decimal.Decimal(bins - 1) / 2
    poc = max(
        range(bins),
        key=lambda i: (volumes[i], -abs(decimal.Decimal(i) - center), -i),
    )
    target = total_volume * value_area_pct / decimals.HUNDRED
    covered = volumes[poc]
    lo = hi = poc
    while covered < target and (lo > 0 or hi < bins - 1):
        up = volumes[hi + 1] if hi + 1 < bins else None
        down = volumes[lo - 1] if lo > 0 else None
        if down is None or (up is not None and up >= down):
            hi += 1
            covered += volumes[hi]
        else:
            lo -= 1
            covered += volumes[lo]
    return Profile(
        vah=ta.quantize_price(low + width * (hi + 1)),
        poc=ta.quantize_price(low + width * poc + width / 2),
        val=ta.quantize_price(low + width * lo),
        window_start_ms=start,
        window_end_ms=end,
    )


def _contiguous(bars: Sequence[ta.BarPoint]) -> bool:
    """Checks that each bar starts where the previous ended.

    Args:
        bars: Bars, oldest first.

    Returns:
        True when there are no gaps.
    """
    return all(a.end_ms == b.start_ms for a, b in itertools.pairwise(bars))


@dataclasses.dataclass(frozen=True, slots=True)
class AvaState:
    """Immutable plugin state.

    Attributes:
        header: Series bookkeeping.
        chart_bars: Recent chart bars, oldest first.
        chart_profile: Chart-timeframe profile.
        htf_profile: 1H profile.
        htf_last_start: Start of the newest 1H bar used.
        aois: Published VAH/POC/VAL AOIs for both profiles.
    """

    header: protocol.SeriesHeader
    chart_bars: tuple[ta.BarPoint, ...] = ()
    chart_profile: Profile | None = None
    htf_profile: Profile | None = None
    htf_last_start: int | None = None
    aois: tuple[structures.Aoi, ...] = ()


class AuctionValueAreasPlugin(base.BasePlugin[AvaState]):
    """VAH/POC/VAL from 24 completed 1H bars and 20 chart bars."""

    PLUGIN_ID = "auction_value_areas"
    DISPLAY_NAME = "Auction value areas"
    DESCRIPTION = (
        "Rolling candle-volume profiles (1H and chart timeframe) with "
        "value-area high, point of control and value-area low."
    )
    OUTPUT_KINDS = (_KIND.VAH, _KIND.POC, _KIND.VAL)
    OUTPUT_EVENTS = (_EVENTS.VALUE_AREA_UPDATED,)
    SETTINGS = (
        settings_lib.SettingSpec(
            "htf_bars",
            settings_lib.SettingKind.INT,
            24,
            "Completed 1H bars in the higher-timeframe profile.",
            minimum=1,
            maximum=500,
        ),
        settings_lib.SettingSpec(
            "chart_bars",
            settings_lib.SettingKind.INT,
            20,
            "Completed chart bars in the chart profile.",
            minimum=1,
            maximum=500,
        ),
        settings_lib.SettingSpec(
            "bins",
            settings_lib.SettingKind.INT,
            32,
            "Price bins.",
            minimum=2,
            maximum=512,
        ),
        settings_lib.SettingSpec(
            "value_area_pct",
            settings_lib.SettingKind.DECIMAL,
            decimal.Decimal(70),
            "Value-area share of volume (percent).",
            minimum=decimal.Decimal(1),
            maximum=decimal.Decimal(100),
        ),
    )
    STATE_TYPES = (AvaState, Profile)

    def warmup_for(
        self, settings: Mapping[str, settings_lib.SettingValue]
    ) -> int:
        """Returns bars needed for the chart profile.

        Args:
            settings: Resolved settings.

        Returns:
            Chart window length.
        """
        return settings_lib.get_int(settings, "chart_bars")

    def required_streams(
        self, settings: Mapping[str, object] | None
    ) -> frozenset[str]:
        """Returns chart bars plus completed 1H bars.

        Args:
            settings: Overrides.

        Returns:
            Stream names.
        """
        del settings
        return frozenset({protocol.CHART_STREAM, _HTF_STREAM})

    def lookback(
        self, settings: Mapping[str, object] | None, stream: str
    ) -> int:
        """Returns the number of 1H bars needed.

        Args:
            settings: Overrides.
            stream: Stream name.

        Returns:
            ``htf_bars`` for the 1H stream, otherwise 0.
        """
        if stream != _HTF_STREAM:
            return 0
        return settings_lib.get_int(self.resolve_settings(settings), "htf_bars")

    def _initial_state(
        self,
        header: protocol.SeriesHeader,
        settings: Mapping[str, settings_lib.SettingValue],
    ) -> AvaState:
        """Builds the initial state.

        Args:
            header: Fresh header.
            settings: Resolved settings.

        Returns:
            Initial state.
        """
        del settings
        return AvaState(header=header)

    def _step(
        self,
        state: AvaState,
        header: protocol.SeriesHeader,
        bar: protocol.BarInput,
    ) -> base.StepResult[AvaState]:
        """Applies one closed bar.

        Args:
            state: Previous state.
            header: Advanced header.
            bar: Bar input with completed 1H bars.

        Returns:
            Step result.
        """
        settings = header.settings_map()
        bins = settings_lib.get_int(settings, "bins")
        share = settings_lib.get_decimal(settings, "value_area_pct")
        chart_n = settings_lib.get_int(settings, "chart_bars")
        htf_n = settings_lib.get_int(settings, "htf_bars")
        chart_bars = (*state.chart_bars, ta.BarPoint.of(bar.candle))[-chart_n:]
        chart_profile = None
        if len(chart_bars) == chart_n and _contiguous(chart_bars):
            chart_profile = build_profile(chart_bars, bins, share)

        events: list[structures.StructureEvent] = []
        htf_profile = state.htf_profile
        htf_last = state.htf_last_start
        htf_bars = tuple(ta.BarPoint.of(c) for c in bar.aux.get(_HTF, ()))[
            -htf_n:
        ]
        newest = htf_bars[-1].start_ms if htf_bars else None
        if newest != htf_last:
            htf_last = newest
            htf_profile = None
            if len(htf_bars) == htf_n and _contiguous(htf_bars):
                htf_profile = build_profile(htf_bars, bins, share)
            if htf_profile is not None:
                events.append(
                    self.event(
                        header,
                        _EVENTS.VALUE_AREA_UPDATED,
                        price=htf_profile.poc,
                        meta={
                            "profile": "1h",
                            "vah": htf_profile.vah,
                            "val": htf_profile.val,
                        },
                    )
                )

        aois, updated, ended = self._aois(
            state,
            header,
            chart_profile,
            htf_profile,
            htf_changed=htf_last != state.htf_last_start,
        )
        new_state = dataclasses.replace(
            state,
            chart_bars=chart_bars,
            chart_profile=chart_profile,
            htf_profile=htf_profile,
            htf_last_start=htf_last,
            aois=aois,
        )
        return base.StepResult(
            state=new_state,
            events=tuple(events),
            updated=updated,
            ended=ended,
        )

    def _aois(
        self,
        state: AvaState,
        header: protocol.SeriesHeader,
        chart_profile: Profile | None,
        htf_profile: Profile | None,
        *,
        htf_changed: bool,
    ) -> tuple[
        tuple[structures.Aoi, ...],
        tuple[structures.Aoi, ...],
        tuple[structures.Aoi, ...],
    ]:
        """Rebuilds line AOIs for profiles that changed.

        Args:
            state: Previous state.
            header: Advanced header.
            chart_profile: New chart profile.
            htf_profile: New 1H profile.
            htf_changed: Whether the 1H profile was recomputed.

        Returns:
            ``(live_aois, updated, ended)``.
        """
        previous: dict[object, list[structures.Aoi]] = {}
        for aoi in state.aois:
            previous.setdefault(aoi.meta.get("profile"), []).append(aoi)
        live: list[structures.Aoi] = []
        updated: list[structures.Aoi] = []
        ended: list[structures.Aoi] = []
        for name, profile, changed in (
            ("chart", chart_profile, True),
            ("1h", htf_profile, htf_changed),
        ):
            old = previous.get(name, [])
            if not changed:
                live.extend(old)
                continue
            if profile is None:
                ended.extend(
                    self.end_aoi(aoi, header, structures.AoiState.EXPIRED)
                    for aoi in old
                )
                continue
            for kind, level in (
                (_KIND.VAH, profile.vah),
                (_KIND.POC, profile.poc),
                (_KIND.VAL, profile.val),
            ):
                aoi = self.aoi(
                    header,
                    aoi_id=ids.stable_id(
                        "ava",
                        header.instrument_id,
                        header.timeframe.value,
                        name,
                        kind.value,
                    ),
                    kind=kind,
                    side=structures.ZoneSide.NEUTRAL,
                    lower=level,
                    upper=level,
                    created_at_ms=profile.window_start_ms,
                    meta={
                        "profile": name,
                        "window_end_ms": profile.window_end_ms,
                    },
                )
                live.append(aoi)
                updated.append(aoi)
        return tuple(live), tuple(updated), tuple(ended)

    def _publish(
        self, state: AvaState
    ) -> tuple[Sequence[structures.Aoi], Mapping[str, structures.ValueType]]:
        """Publishes live AOIs and values.

        Args:
            state: Current state.

        Returns:
            ``(aois, values)``.
        """
        chart = state.chart_profile
        htf = state.htf_profile
        return state.aois, {
            "vah": None if chart is None else chart.vah,
            "poc": None if chart is None else chart.poc,
            "val": None if chart is None else chart.val,
            "vah_1h": None if htf is None else htf.vah,
            "poc_1h": None if htf is None else htf.poc,
            "val_1h": None if htf is None else htf.val,
        }
