"""Liquidity pools (BSL/SSL/EQH/EQL), sweeps and breaks.

Clean-room implementation of V5 §6.2-C. See README.md for semantics and
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
_HALF = decimal.Decimal(50)
_CAP = decimal.Decimal(1)


@dataclasses.dataclass(frozen=True, slots=True)
class Pool:
    """A resting liquidity pool at a confirmed swing level.

    Attributes:
        aoi: Published line AOI (``lower == upper == level``).
        level: Pool price.
        high_side: True for buy-side pools (above highs).
        pivot_count: Number of merged pivots (>= 2 means EQH/EQL).
    """

    aoi: structures.Aoi
    level: decimal.Decimal
    high_side: bool
    pivot_count: int = 1


@dataclasses.dataclass(frozen=True, slots=True)
class LiquidityState:
    """Immutable plugin state.

    Attributes:
        header: Series bookkeeping.
        atr: ATR state.
        pivots: Pivot tracker.
        pools: Live pools, oldest first.
    """

    header: protocol.SeriesHeader
    atr: ta.Atr
    pivots: ta.PivotTracker
    pools: tuple[Pool, ...] = ()


class LiquiditySweepsPlugin(base.BasePlugin[LiquidityState]):
    """Swing liquidity pools with sweep (reclaim) vs break detection."""

    PLUGIN_ID = "liquidity_sweeps"
    DISPLAY_NAME = "Liquidity sweeps"
    DESCRIPTION = (
        "Buy-side and sell-side liquidity at confirmed swings, equal "
        "highs/lows merging, and sweeps that close back inside."
    )
    OUTPUT_KINDS = (_KIND.BSL, _KIND.SSL, _KIND.EQH, _KIND.EQL)
    OUTPUT_EVENTS = (
        _EVENTS.BSL_SWEEP,
        _EVENTS.SSL_SWEEP,
        _EVENTS.BSL_BREAK,
        _EVENTS.SSL_BREAK,
    )
    SETTINGS = (
        settings_lib.SettingSpec(
            "pivot_left",
            settings_lib.SettingKind.INT,
            3,
            "Bars left of a swing.",
            minimum=1,
            maximum=50,
        ),
        settings_lib.SettingSpec(
            "pivot_right",
            settings_lib.SettingKind.INT,
            3,
            "Bars right of a swing (confirmation delay).",
            minimum=1,
            maximum=50,
        ),
        settings_lib.SettingSpec(
            "atr_length",
            settings_lib.SettingKind.INT,
            14,
            "ATR length (Wilder).",
            minimum=1,
            maximum=200,
        ),
        settings_lib.SettingSpec(
            "equal_tolerance_atr",
            settings_lib.SettingKind.DECIMAL,
            decimal.Decimal("0.10"),
            "Equal-level tolerance and minimum penetration in ATR units.",
            minimum=decimal.Decimal(0),
        ),
        settings_lib.SettingSpec(
            "tick_size",
            settings_lib.SettingKind.DECIMAL,
            decimal.Decimal(0),
            "Instrument tick size (minimum penetration floor).",
            minimum=decimal.Decimal(0),
        ),
        settings_lib.SettingSpec(
            "max_pools",
            settings_lib.SettingKind.INT,
            30,
            "Maximum live pools per side.",
            minimum=1,
            maximum=500,
        ),
    )
    STATE_TYPES = (LiquidityState, Pool)

    def warmup_for(
        self, settings: Mapping[str, settings_lib.SettingValue]
    ) -> int:
        """Returns bars needed for ATR and the first pivot.

        Args:
            settings: Resolved settings.

        Returns:
            Warmup bars.
        """
        return max(
            settings_lib.get_int(settings, "atr_length"),
            settings_lib.get_int(settings, "pivot_left")
            + settings_lib.get_int(settings, "pivot_right")
            + 1,
        )

    def _initial_state(
        self,
        header: protocol.SeriesHeader,
        settings: Mapping[str, settings_lib.SettingValue],
    ) -> LiquidityState:
        """Builds the initial state.

        Args:
            header: Fresh header.
            settings: Resolved settings.

        Returns:
            Initial state.
        """
        return LiquidityState(
            header=header,
            atr=ta.Atr(length=settings_lib.get_int(settings, "atr_length")),
            pivots=ta.PivotTracker(
                left=settings_lib.get_int(settings, "pivot_left"),
                right=settings_lib.get_int(settings, "pivot_right"),
            ),
        )

    def _step(
        self,
        state: LiquidityState,
        header: protocol.SeriesHeader,
        bar: protocol.BarInput,
    ) -> base.StepResult[LiquidityState]:
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
        tolerance = (
            decimals.ZERO
            if atr.value is None
            else settings_lib.get_decimal(settings, "equal_tolerance_atr")
            * atr.value
        )
        min_penetration = max(
            settings_lib.get_decimal(settings, "tick_size"), tolerance
        )
        events: list[structures.StructureEvent] = []
        updated: list[structures.Aoi] = []
        ended: list[structures.Aoi] = []
        pools: list[Pool] = []
        for pool in state.pools:
            outcome = self._check_pool(
                pool, point, min_penetration, atr.value, header
            )
            if outcome is None:
                pools.append(pool)
                continue
            event, done = outcome
            events.append(event)
            ended.append(done)
        pivots, pivot_high, pivot_low = state.pivots.update(point)
        for pivot, high_side in ((pivot_high, True), (pivot_low, False)):
            if pivot is None:
                continue
            pools, changed = self._add_pivot(
                pools, pivot, high_side, tolerance, header
            )
            updated.append(changed)
        pools, capped = self._cap(
            pools, settings_lib.get_int(settings, "max_pools"), header
        )
        ended.extend(capped)
        new_state = dataclasses.replace(
            state, atr=atr, pivots=pivots, pools=tuple(pools)
        )
        return base.StepResult(
            state=new_state,
            events=tuple(events),
            updated=tuple(updated),
            ended=tuple(ended),
        )

    def _check_pool(
        self,
        pool: Pool,
        bar: ta.BarPoint,
        min_penetration: decimal.Decimal,
        atr: decimal.Decimal | None,
        header: protocol.SeriesHeader,
    ) -> tuple[structures.StructureEvent, structures.Aoi] | None:
        """Checks whether the bar sweeps or breaks a pool.

        Args:
            pool: Live pool (confirmed before this bar).
            bar: Current bar.
            min_penetration: Minimum wick penetration.
            atr: Current ATR.
            header: Advanced header.

        Returns:
            ``(event, ended_aoi)`` or ``None`` when the pool stays live.
        """
        if pool.high_side:
            penetration = bar.high - pool.level
            reclaim = pool.level - bar.close
        else:
            penetration = pool.level - bar.low
            reclaim = bar.close - pool.level
        if penetration <= decimals.ZERO or penetration < min_penetration:
            return None
        if reclaim > decimals.ZERO:
            name = _EVENTS.BSL_SWEEP if pool.high_side else _EVENTS.SSL_SWEEP
            terminal = _STATE.SWEPT
        elif reclaim < decimals.ZERO:
            name = _EVENTS.BSL_BREAK if pool.high_side else _EVENTS.SSL_BREAK
            terminal = _STATE.INVALIDATED
        else:
            return None
        quality = None
        if atr:
            quality = ta.score(
                (_HALF, ta.saturate(penetration / atr, _CAP)),
                (_HALF, ta.saturate(max(reclaim, decimals.ZERO) / atr, _CAP)),
            )
        event = self.event(
            header,
            name,
            price=pool.level,
            aoi_id=pool.aoi.aoi_id,
            score=quality if terminal is _STATE.SWEPT else None,
            meta={"penetration": penetration, "pool_kind": pool.aoi.kind.value},
        )
        return event, self.end_aoi(pool.aoi, header, terminal)

    def _add_pivot(
        self,
        pools: list[Pool],
        pivot: ta.Pivot,
        high_side: bool,
        tolerance: decimal.Decimal,
        header: protocol.SeriesHeader,
    ) -> tuple[list[Pool], structures.Aoi]:
        """Adds a confirmed swing, merging equal levels into EQH/EQL.

        Args:
            pools: Live pools.
            pivot: Confirmed swing.
            high_side: True for swing highs.
            tolerance: Equal-level tolerance.
            header: Advanced header.

        Returns:
            ``(pools, changed_aoi)``.
        """
        candidates = [
            (abs(pool.level - pivot.price), index)
            for index, pool in enumerate(pools)
            if pool.high_side == high_side
            and abs(pool.level - pivot.price) <= tolerance
        ]
        if candidates:
            _, index = min(candidates)
            pool = pools[index]
            level = (max if high_side else min)(pool.level, pivot.price)
            kind = _KIND.EQH if high_side else _KIND.EQL
            aoi = dataclasses.replace(
                pool.aoi,
                kind=kind,
                lower=level,
                upper=level,
                as_of_ms=header.as_of_ms,
                meta={**pool.aoi.meta, "pivot_count": pool.pivot_count + 1},
            )
            merged = Pool(
                aoi=aoi,
                level=level,
                high_side=high_side,
                pivot_count=pool.pivot_count + 1,
            )
            return [*pools[:index], merged, *pools[index + 1 :]], aoi
        kind = _KIND.BSL if high_side else _KIND.SSL
        aoi = self.aoi(
            header,
            aoi_id=ids.stable_id(
                "liq",
                header.instrument_id,
                header.timeframe.value,
                "high" if high_side else "low",
                pivot.start_ms,
            ),
            kind=kind,
            side=_SIDE.BEARISH if high_side else _SIDE.BULLISH,
            lower=pivot.price,
            upper=pivot.price,
            created_at_ms=pivot.start_ms,
            meta={"pivot_count": 1},
        )
        return [
            *pools,
            Pool(aoi=aoi, level=pivot.price, high_side=high_side),
        ], aoi

    def _cap(
        self, pools: list[Pool], limit: int, header: protocol.SeriesHeader
    ) -> tuple[list[Pool], list[structures.Aoi]]:
        """Drops the oldest pools beyond the per-side limit.

        Args:
            pools: Live pools.
            limit: Maximum per side.
            header: Advanced header.

        Returns:
            ``(kept_pools, expired_aois)``.
        """
        expired: list[structures.Aoi] = []
        for high_side in (True, False):
            side_pools = [p for p in pools if p.high_side == high_side]
            excess = len(side_pools) - limit
            for pool in side_pools[: max(0, excess)]:
                pools.remove(pool)
                expired.append(self.end_aoi(pool.aoi, header, _STATE.EXPIRED))
        return pools, expired

    def _publish(
        self, state: LiquidityState
    ) -> tuple[Sequence[structures.Aoi], Mapping[str, structures.ValueType]]:
        """Publishes live AOIs and values.

        Args:
            state: Current state.

        Returns:
            ``(aois, values)``.
        """
        return (
            [pool.aoi for pool in state.pools],
            {"active_count": len(state.pools)},
        )
