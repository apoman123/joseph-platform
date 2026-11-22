"""Fair value gaps and their inversions (FVG / IFVG).

Clean-room implementation of V5 §6.2-B. See README.md for semantics and
assumptions.
"""

from __future__ import annotations

import dataclasses
import decimal
from collections.abc import Mapping
from collections.abc import Sequence

from joseph.domain import decimals
from joseph.domain import ids
from joseph.domain import structures
from joseph.indicators.sdk import base
from joseph.indicators.sdk import protocol
from joseph.indicators.sdk import settings as settings_lib
from joseph.indicators.sdk import ta

_EVENTS = structures.StructureEventName
_KIND = structures.AoiKind
_SIDE = structures.ZoneSide
_STATE = structures.AoiState
_GAP_CAP = decimal.Decimal("1.0")
_BODY_CAP = decimal.Decimal("1.5")
_CROSS_CAP = decimal.Decimal("1.0")
_W_GAP = decimal.Decimal(55)
_W_BODY = decimal.Decimal(30)
_W_CLOSE = decimal.Decimal(15)
_W_IFVG_GAP = decimal.Decimal(40)
_W_IFVG_CROSS = decimal.Decimal(60)


@dataclasses.dataclass(frozen=True, slots=True)
class Zone:
    """A live FVG or IFVG.

    Attributes:
        aoi: Published AOI record.
        mid: Midline (50%) price.
        formed_bar: Header bar count when the zone was confirmed.
        gap_fraction: Saturated gap/ATR at formation (for IFVG scoring).
        mid_tested: Whether the first midline retest decay was applied.
    """

    aoi: structures.Aoi
    mid: decimal.Decimal
    formed_bar: int
    gap_fraction: decimal.Decimal
    mid_tested: bool = False


@dataclasses.dataclass(frozen=True, slots=True)
class FvgState:
    """Immutable plugin state.

    Attributes:
        header: Series bookkeeping.
        atr: ATR(14) state.
        recent: The two bars before the current one.
        zones: Live zones, oldest first.
    """

    header: protocol.SeriesHeader
    atr: ta.Atr
    recent: tuple[ta.BarPoint, ...] = ()
    zones: tuple[Zone, ...] = ()


class FvgIfvgPlugin(base.BasePlugin[FvgState]):
    """Three-candle fair value gaps with inversion tracking."""

    PLUGIN_ID = "fvg_ifvg"
    DISPLAY_NAME = "FVG / IFVG"
    DESCRIPTION = (
        "Three-candle fair value gaps, midline retests and inversion into "
        "IFVGs on a close beyond the far edge."
    )
    OUTPUT_KINDS = (_KIND.FVG, _KIND.IFVG)
    OUTPUT_EVENTS = (
        _EVENTS.FVG_FORMED,
        _EVENTS.IFVG_FORMED,
        _EVENTS.AOI_INVALIDATED,
        _EVENTS.AOI_EXPIRED,
    )
    SETTINGS = (
        settings_lib.SettingSpec(
            "atr_length",
            settings_lib.SettingKind.INT,
            14,
            "ATR length (Wilder).",
            minimum=1,
            maximum=200,
        ),
        settings_lib.SettingSpec(
            "min_gap_atr",
            settings_lib.SettingKind.DECIMAL,
            decimal.Decimal("0.10"),
            "Minimum gap height in ATR units.",
            minimum=decimal.Decimal(0),
        ),
        settings_lib.SettingSpec(
            "tick_size",
            settings_lib.SettingKind.DECIMAL,
            decimal.Decimal(0),
            "Instrument tick size; the gap must be at least one tick.",
            minimum=decimal.Decimal(0),
        ),
        settings_lib.SettingSpec(
            "retest_decay",
            settings_lib.SettingKind.DECIMAL,
            decimal.Decimal("0.70"),
            "Score multiplier on the first midline retest.",
            minimum=decimal.Decimal(0),
            maximum=decimal.Decimal(1),
        ),
        settings_lib.SettingSpec(
            "max_zones",
            settings_lib.SettingKind.INT,
            20,
            "Maximum live zones retained (oldest expire).",
            minimum=1,
            maximum=500,
        ),
        settings_lib.SettingSpec(
            "expire_bars",
            settings_lib.SettingKind.INT,
            0,
            "Expire zones after this many bars (0 = never).",
            minimum=0,
        ),
    )
    STATE_TYPES = (FvgState, Zone)

    def warmup_for(
        self, settings: Mapping[str, settings_lib.SettingValue]
    ) -> int:
        """Returns bars needed for ATR seeding.

        Args:
            settings: Resolved settings.

        Returns:
            ATR length.
        """
        return settings_lib.get_int(settings, "atr_length")

    def _initial_state(
        self,
        header: protocol.SeriesHeader,
        settings: Mapping[str, settings_lib.SettingValue],
    ) -> FvgState:
        """Builds the initial state.

        Args:
            header: Fresh header.
            settings: Resolved settings.

        Returns:
            Initial state.
        """
        return FvgState(
            header=header,
            atr=ta.Atr(length=settings_lib.get_int(settings, "atr_length")),
        )

    def _step(
        self,
        state: FvgState,
        header: protocol.SeriesHeader,
        bar: protocol.BarInput,
    ) -> base.StepResult[FvgState]:
        """Applies one closed bar.

        Args:
            state: Previous state.
            header: Advanced header.
            bar: Bar input.

        Returns:
            Step result.
        """
        settings = header.settings_map()
        point = ta.BarPoint.of(bar.candle)
        atr = state.atr.update(bar.candle)
        events: list[structures.StructureEvent] = []
        updated: list[structures.Aoi] = []
        ended: list[structures.Aoi] = []
        zones = self._update_zones(
            state.zones,
            header,
            point,
            atr.value,
            settings,
            events=events,
            updated=updated,
            ended=ended,
        )
        if len(state.recent) == 2 and atr.value is not None:
            formed = self._detect(
                state.recent, point, header, atr.value, settings
            )
            if formed is not None:
                zones.append(formed)
                updated.append(formed.aoi)
                events.append(
                    self.event(
                        header,
                        _EVENTS.FVG_FORMED,
                        price=formed.mid,
                        aoi_id=formed.aoi.aoi_id,
                        score=formed.aoi.quality_score,
                        meta={"side": formed.aoi.side.value},
                    )
                )
        max_zones = settings_lib.get_int(settings, "max_zones")
        while len(zones) > max_zones:
            dropped = zones.pop(0)
            expired = self.end_aoi(dropped.aoi, header, _STATE.EXPIRED)
            ended.append(expired)
            events.append(
                self.event(
                    header,
                    _EVENTS.AOI_EXPIRED,
                    aoi_id=expired.aoi_id,
                    meta={"reason": "retention_cap"},
                )
            )
        new_state = dataclasses.replace(
            state,
            atr=atr,
            recent=(*state.recent, point)[-2:],
            zones=tuple(zones),
        )
        return base.StepResult(
            state=new_state,
            events=tuple(events),
            updated=tuple(updated),
            ended=tuple(ended),
        )

    def _detect(
        self,
        recent: tuple[ta.BarPoint, ...],
        current: ta.BarPoint,
        header: protocol.SeriesHeader,
        atr: decimal.Decimal,
        settings: Mapping[str, settings_lib.SettingValue],
    ) -> Zone | None:
        """Detects a gap confirmed by the current (third) bar.

        Args:
            recent: The two previous bars ``(c2, c1)``.
            current: Current bar ``c0``.
            header: Advanced header.
            atr: ATR including the current bar.
            settings: Resolved settings.

        Returns:
            A new zone, or ``None``.
        """
        first, middle = recent
        if current.low > first.high:
            side, lower, upper = _SIDE.BULLISH, first.high, current.low
        elif current.high < first.low:
            side, lower, upper = _SIDE.BEARISH, current.high, first.low
        else:
            return None
        gap = upper - lower
        tick = settings_lib.get_decimal(settings, "tick_size")
        min_gap = settings_lib.get_decimal(settings, "min_gap_atr") * atr
        if gap < tick or gap < min_gap:
            return None
        gap_fraction = ta.saturate(gap / atr if atr else gap, _GAP_CAP)
        body_fraction = ta.saturate(
            middle.body / atr if atr else decimals.ZERO, _BODY_CAP
        )
        if middle.range > decimals.ZERO:
            location = (
                (middle.close - middle.low)
                if side is _SIDE.BULLISH
                else (middle.high - middle.close)
            ) / middle.range
        else:
            location = decimals.ZERO
        quality = ta.score(
            (_W_GAP, gap_fraction),
            (_W_BODY, body_fraction),
            (_W_CLOSE, location),
        )
        mid = ta.quantize_price((lower + upper) / 2)
        aoi = self.aoi(
            header,
            aoi_id=ids.stable_id(
                "fvg",
                header.instrument_id,
                header.timeframe.value,
                side.value,
                first.start_ms,
            ),
            kind=_KIND.FVG,
            side=side,
            lower=lower,
            upper=upper,
            created_at_ms=first.start_ms,
            quality_score=quality,
            meta={"mid": mid, "gap": gap},
        )
        return Zone(
            aoi=aoi, mid=mid, formed_bar=header.bars, gap_fraction=gap_fraction
        )

    def _update_zones(
        self,
        zones: Sequence[Zone],
        header: protocol.SeriesHeader,
        bar: ta.BarPoint,
        atr: decimal.Decimal | None,
        settings: Mapping[str, settings_lib.SettingValue],
        *,
        events: list[structures.StructureEvent],
        updated: list[structures.Aoi],
        ended: list[structures.Aoi],
    ) -> list[Zone]:
        """Applies retests, inversions, invalidations and expiry.

        Args:
            zones: Live zones before this bar.
            header: Advanced header.
            bar: Current bar.
            atr: Current ATR (may be ``None``).
            settings: Resolved settings.
            events: Output list for events.
            updated: Output list for changed AOIs.
            ended: Output list for ended AOIs.

        Returns:
            Live zones after this bar.
        """
        decay = settings_lib.get_decimal(settings, "retest_decay")
        expire_bars = settings_lib.get_int(settings, "expire_bars")
        survivors: list[Zone] = []
        for original in zones:
            zone = original
            aoi = zone.aoi
            bullish = aoi.side is _SIDE.BULLISH
            is_fvg = aoi.kind is _KIND.FVG
            # Close beyond the far edge: FVG inverts, IFVG is invalidated.
            closes_through = (
                bar.close < aoi.lower if bullish else bar.close > aoi.upper
            )
            if closes_through:
                terminal = _STATE.INVERTED if is_fvg else _STATE.INVALIDATED
                done = self.end_aoi(aoi, header, terminal)
                ended.append(done)
                events.append(
                    self.event(
                        header,
                        _EVENTS.AOI_INVALIDATED,
                        aoi_id=aoi.aoi_id,
                        meta={
                            "reason": "inverted" if is_fvg else "close_through"
                        },
                    )
                )
                if is_fvg:
                    inverted = self._invert(zone, header, bar, atr)
                    survivors.append(inverted)
                    updated.append(inverted.aoi)
                    events.append(
                        self.event(
                            header,
                            _EVENTS.IFVG_FORMED,
                            price=inverted.mid,
                            aoi_id=inverted.aoi.aoi_id,
                            score=inverted.aoi.quality_score,
                            meta={
                                "from_aoi_id": aoi.aoi_id,
                                "side": inverted.aoi.side.value,
                            },
                        )
                    )
                continue
            if expire_bars and header.bars - zone.formed_bar >= expire_bars:
                done = self.end_aoi(aoi, header, _STATE.EXPIRED)
                ended.append(done)
                events.append(
                    self.event(
                        header,
                        _EVENTS.AOI_EXPIRED,
                        aoi_id=aoi.aoi_id,
                        meta={"reason": "age"},
                    )
                )
                continue
            reaches_mid = (
                bar.low <= zone.mid if bullish else bar.high >= zone.mid
            )
            if reaches_mid and not zone.mid_tested:
                score = (
                    None
                    if aoi.quality_score is None
                    else ta.decay(aoi.quality_score, decay)
                )
                touched = self.touch_aoi(aoi, header, score)
                zone = dataclasses.replace(zone, aoi=touched, mid_tested=True)
                updated.append(touched)
            survivors.append(zone)
        return survivors

    def _invert(
        self,
        zone: Zone,
        header: protocol.SeriesHeader,
        bar: ta.BarPoint,
        atr: decimal.Decimal | None,
    ) -> Zone:
        """Builds the IFVG created when an FVG closes through its far edge.

        Args:
            zone: The inverted FVG.
            header: Advanced header.
            bar: Crossing bar.
            atr: Current ATR.

        Returns:
            New IFVG zone with the opposite side.
        """
        cross = ta.saturate(
            bar.body / atr if atr else decimals.ZERO, _CROSS_CAP
        )
        quality = ta.score(
            (_W_IFVG_GAP, zone.gap_fraction), (_W_IFVG_CROSS, cross)
        )
        side = (
            _SIDE.BEARISH if zone.aoi.side is _SIDE.BULLISH else _SIDE.BULLISH
        )
        aoi = self.aoi(
            header,
            aoi_id=ids.stable_id("ifvg", zone.aoi.aoi_id),
            kind=_KIND.IFVG,
            side=side,
            lower=zone.aoi.lower,
            upper=zone.aoi.upper,
            created_at_ms=bar.start_ms,
            quality_score=quality,
            meta={"mid": zone.mid, "from_aoi_id": zone.aoi.aoi_id},
        )
        return Zone(
            aoi=aoi,
            mid=zone.mid,
            formed_bar=header.bars,
            gap_fraction=zone.gap_fraction,
        )

    def _publish(
        self, state: FvgState
    ) -> tuple[Sequence[structures.Aoi], Mapping[str, structures.ValueType]]:
        """Publishes live AOIs and values.

        Args:
            state: Current state.

        Returns:
            ``(aois, values)``.
        """
        return (
            [zone.aoi for zone in state.zones],
            {"active_count": len(state.zones)},
        )
