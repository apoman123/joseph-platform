"""Order blocks and breaker blocks.

Clean-room implementation of V5 §6.2-D. See README.md for semantics and
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
_HISTORY = 64
_USED_SOURCES = 256
_BODY_CAP = decimal.Decimal(2)
_DIST_CAP = decimal.Decimal(1)
_CROSS_CAP = decimal.Decimal(1)
_W_BODY = decimal.Decimal(60)
_W_DIST = decimal.Decimal(40)
_W_REMAIN = decimal.Decimal("0.40")
_W_CROSS = decimal.Decimal(60)


@dataclasses.dataclass(frozen=True, slots=True)
class Swing:
    """Latest confirmed pivot.

    Attributes:
        price: Pivot price.
        start_ms: Pivot bar start.
        broken: True once a close crossed it (one block per pivot).
    """

    price: decimal.Decimal
    start_ms: int
    broken: bool = False


@dataclasses.dataclass(frozen=True, slots=True)
class Block:
    """A live order block or breaker.

    Attributes:
        aoi: Published AOI.
        touched: Whether the first-touch decay was applied.
    """

    aoi: structures.Aoi
    touched: bool = False


@dataclasses.dataclass(frozen=True, slots=True)
class BlocksState:
    """Immutable plugin state.

    Attributes:
        header: Series bookkeeping.
        atr: ATR state.
        pivots: Pivot tracker.
        swing_high: Latest pivot high.
        swing_low: Latest pivot low.
        history: Recent bars (oldest first, current excluded).
        blocks: Live blocks.
        used_sources: Starts of candles that already formed a block.
    """

    header: protocol.SeriesHeader
    atr: ta.Atr
    pivots: ta.PivotTracker
    swing_high: Swing | None = None
    swing_low: Swing | None = None
    history: tuple[ta.BarPoint, ...] = ()
    blocks: tuple[Block, ...] = ()
    used_sources: tuple[int, ...] = ()


class OrderBreakerBlocksPlugin(base.BasePlugin[BlocksState]):
    """Order blocks from displacement breaks and breaker conversion."""

    PLUGIN_ID = "order_breaker_blocks"
    DISPLAY_NAME = "Order / breaker blocks"
    DESCRIPTION = (
        "Last opposing candle before a displacement close through a "
        "confirmed pivot, with conversion to breakers on failure."
    )
    OUTPUT_KINDS = (_KIND.OB, _KIND.BREAKER)
    OUTPUT_EVENTS = (
        _EVENTS.OB_FORMED,
        _EVENTS.BREAKER_FORMED,
        _EVENTS.AOI_INVALIDATED,
        _EVENTS.AOI_EXPIRED,
    )
    SETTINGS = (
        settings_lib.SettingSpec(
            "pivot_left",
            settings_lib.SettingKind.INT,
            3,
            "Bars left of a pivot.",
            minimum=1,
            maximum=50,
        ),
        settings_lib.SettingSpec(
            "pivot_right",
            settings_lib.SettingKind.INT,
            3,
            "Bars right of a pivot.",
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
            "min_body_atr",
            settings_lib.SettingKind.DECIMAL,
            decimal.Decimal("0.50"),
            "Minimum breakout candle body in ATR units.",
            minimum=decimal.Decimal(0),
        ),
        settings_lib.SettingSpec(
            "ob_lookback",
            settings_lib.SettingKind.INT,
            12,
            "Bars searched back for the opposing candle.",
            minimum=1,
            maximum=_HISTORY,
        ),
        settings_lib.SettingSpec(
            "retest_decay",
            settings_lib.SettingKind.DECIMAL,
            decimal.Decimal("0.70"),
            "Score multiplier on the first touch.",
            minimum=decimal.Decimal(0),
            maximum=decimal.Decimal(1),
        ),
        settings_lib.SettingSpec(
            "max_blocks",
            settings_lib.SettingKind.INT,
            20,
            "Maximum live blocks.",
            minimum=1,
            maximum=500,
        ),
    )
    STATE_TYPES = (BlocksState, Swing, Block)

    def warmup_for(
        self, settings: Mapping[str, settings_lib.SettingValue]
    ) -> int:
        """Returns bars needed for ATR and pivots.

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
    ) -> BlocksState:
        """Builds the initial state.

        Args:
            header: Fresh header.
            settings: Resolved settings.

        Returns:
            Initial state.
        """
        return BlocksState(
            header=header,
            atr=ta.Atr(length=settings_lib.get_int(settings, "atr_length")),
            pivots=ta.PivotTracker(
                left=settings_lib.get_int(settings, "pivot_left"),
                right=settings_lib.get_int(settings, "pivot_right"),
            ),
        )

    def _step(
        self,
        state: BlocksState,
        header: protocol.SeriesHeader,
        bar: protocol.BarInput,
    ) -> base.StepResult[BlocksState]:
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
        blocks = self._update_blocks(
            state.blocks,
            header,
            point,
            atr.value,
            settings,
            events=events,
            updated=updated,
            ended=ended,
        )
        pivots, pivot_high, pivot_low = state.pivots.update(point)
        swing_high = state.swing_high
        swing_low = state.swing_low
        if pivot_high is not None:
            swing_high = Swing(pivot_high.price, pivot_high.start_ms)
        if pivot_low is not None:
            swing_low = Swing(pivot_low.price, pivot_low.start_ms)
        used = list(state.used_sources)
        for bullish in (True, False):
            swing = swing_high if bullish else swing_low
            if swing is None or swing.broken:
                continue
            crossed = (
                point.close > swing.price
                if bullish
                else point.close < swing.price
            )
            if not crossed:
                continue
            swing = dataclasses.replace(swing, broken=True)
            if bullish:
                swing_high = swing
            else:
                swing_low = swing
            block = self._form_block(
                state.history,
                point,
                swing,
                bullish,
                atr.value,
                header=header,
                settings=settings,
                used=used,
            )
            if block is None:
                continue
            blocks.append(block)
            used.append(int(block.aoi.created_at_ms))
            updated.append(block.aoi)
            events.append(
                self.event(
                    header,
                    _EVENTS.OB_FORMED,
                    aoi_id=block.aoi.aoi_id,
                    score=block.aoi.quality_score,
                    meta={
                        "side": block.aoi.side.value,
                        "pivot_start_ms": swing.start_ms,
                    },
                )
            )
        max_blocks = settings_lib.get_int(settings, "max_blocks")
        while len(blocks) > max_blocks:
            dropped = blocks.pop(0)
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
            pivots=pivots,
            swing_high=swing_high,
            swing_low=swing_low,
            history=(*state.history, point)[-_HISTORY:],
            blocks=tuple(blocks),
            used_sources=tuple(used[-_USED_SOURCES:]),
        )
        return base.StepResult(
            state=new_state,
            events=tuple(events),
            updated=tuple(updated),
            ended=tuple(ended),
        )

    def _form_block(
        self,
        history: tuple[ta.BarPoint, ...],
        breakout: ta.BarPoint,
        swing: Swing,
        bullish: bool,
        atr: decimal.Decimal | None,
        *,
        header: protocol.SeriesHeader,
        settings: Mapping[str, settings_lib.SettingValue],
        used: list[int],
    ) -> Block | None:
        """Builds an order block for a qualifying displacement break.

        Args:
            history: Bars before the breakout, oldest first.
            breakout: Breakout bar.
            swing: Broken pivot.
            bullish: Break direction.
            atr: Current ATR.
            header: Advanced header.
            settings: Resolved settings.
            used: Candle starts that already formed a block.

        Returns:
            The block, or ``None`` when the break does not qualify.
        """
        if atr is None or atr <= decimals.ZERO:
            return None
        directional = (
            breakout.close > breakout.open
            if bullish
            else breakout.close < breakout.open
        )
        min_body = settings_lib.get_decimal(settings, "min_body_atr") * atr
        if not directional or breakout.body < min_body:
            return None
        lookback = settings_lib.get_int(settings, "ob_lookback")
        source = None
        for candidate in reversed(history[-lookback:]):
            if candidate.start_ms <= swing.start_ms:
                break
            opposing = (
                candidate.close < candidate.open
                if bullish
                else candidate.close > candidate.open
            )
            if opposing:
                source = candidate
                break
        if source is None or source.start_ms in used:
            return None
        distance = (
            breakout.close - swing.price
            if bullish
            else swing.price - breakout.close
        )
        quality = ta.score(
            (_W_BODY, ta.saturate(breakout.body / atr, _BODY_CAP)),
            (_W_DIST, ta.saturate(distance / atr, _DIST_CAP)),
        )
        side = _SIDE.BULLISH if bullish else _SIDE.BEARISH
        aoi = self.aoi(
            header,
            aoi_id=ids.stable_id(
                "ob",
                header.instrument_id,
                header.timeframe.value,
                side.value,
                source.start_ms,
            ),
            kind=_KIND.OB,
            side=side,
            lower=source.low,
            upper=source.high,
            created_at_ms=source.start_ms,
            quality_score=quality,
            meta={
                "pivot_price": swing.price,
                "breakout_start_ms": breakout.start_ms,
            },
        )
        return Block(aoi=aoi)

    def _update_blocks(
        self,
        blocks: Sequence[Block],
        header: protocol.SeriesHeader,
        bar: ta.BarPoint,
        atr: decimal.Decimal | None,
        settings: Mapping[str, settings_lib.SettingValue],
        *,
        events: list[structures.StructureEvent],
        updated: list[structures.Aoi],
        ended: list[structures.Aoi],
    ) -> list[Block]:
        """Applies touches, breaker conversion and breaker removal.

        Args:
            blocks: Live blocks before this bar.
            header: Advanced header.
            bar: Current bar.
            atr: Current ATR.
            settings: Resolved settings.
            events: Output events.
            updated: Output changed AOIs.
            ended: Output ended AOIs.

        Returns:
            Live blocks after this bar.
        """
        decay = settings_lib.get_decimal(settings, "retest_decay")
        survivors: list[Block] = []
        for original in blocks:
            block = original
            aoi = block.aoi
            bullish = aoi.side is _SIDE.BULLISH
            fails = bar.close < aoi.lower if bullish else bar.close > aoi.upper
            if fails:
                is_ob = aoi.kind is _KIND.OB
                terminal = _STATE.INVERTED if is_ob else _STATE.INVALIDATED
                ended.append(self.end_aoi(aoi, header, terminal))
                events.append(
                    self.event(
                        header,
                        _EVENTS.AOI_INVALIDATED,
                        aoi_id=aoi.aoi_id,
                        meta={
                            "reason": "breaker" if is_ob else "close_through"
                        },
                    )
                )
                if is_ob:
                    breaker = self._to_breaker(block, header, bar, atr)
                    survivors.append(breaker)
                    updated.append(breaker.aoi)
                    events.append(
                        self.event(
                            header,
                            _EVENTS.BREAKER_FORMED,
                            aoi_id=breaker.aoi.aoi_id,
                            score=breaker.aoi.quality_score,
                            meta={
                                "from_aoi_id": aoi.aoi_id,
                                "side": breaker.aoi.side.value,
                            },
                        )
                    )
                continue
            touches = bar.low <= aoi.upper if bullish else bar.high >= aoi.lower
            if touches and not block.touched:
                score = (
                    None
                    if aoi.quality_score is None
                    else ta.decay(aoi.quality_score, decay)
                )
                touched = self.touch_aoi(aoi, header, score)
                block = Block(aoi=touched, touched=True)
                updated.append(touched)
            survivors.append(block)
        return survivors

    def _to_breaker(
        self,
        block: Block,
        header: protocol.SeriesHeader,
        bar: ta.BarPoint,
        atr: decimal.Decimal | None,
    ) -> Block:
        """Converts a failed order block into a breaker.

        Args:
            block: Failed block.
            header: Advanced header.
            bar: Crossing bar.
            atr: Current ATR.

        Returns:
            Breaker block with the opposite side.
        """
        remaining = decimal.Decimal(block.aoi.quality_score or 0)
        cross = ta.saturate(
            bar.body / atr if atr else decimals.ZERO, _CROSS_CAP
        )
        quality = ta.score((_W_REMAIN, remaining), (_W_CROSS, cross))
        side = (
            _SIDE.BEARISH if block.aoi.side is _SIDE.BULLISH else _SIDE.BULLISH
        )
        aoi = self.aoi(
            header,
            aoi_id=ids.stable_id("breaker", block.aoi.aoi_id),
            kind=_KIND.BREAKER,
            side=side,
            lower=block.aoi.lower,
            upper=block.aoi.upper,
            created_at_ms=block.aoi.created_at_ms,
            quality_score=quality,
            meta={"from_aoi_id": block.aoi.aoi_id},
        )
        return Block(aoi=aoi)

    def _publish(
        self, state: BlocksState
    ) -> tuple[Sequence[structures.Aoi], Mapping[str, structures.ValueType]]:
        """Publishes live AOIs and values.

        Args:
            state: Current state.

        Returns:
            ``(aois, values)``.
        """
        return (
            [block.aoi for block in state.blocks],
            {"active_count": len(state.blocks)},
        )
