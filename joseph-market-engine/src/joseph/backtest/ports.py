# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Ports of the backtest context."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from joseph.domain import instruments
from joseph.domain import structures
from joseph.domain import timeframes
from joseph.strategy.domain import compiler


class FrameProvider(Protocol):
    """Builds historical ``BarFrame``s with the production indicator engine.

    ``pipeline.frames.EngineFrameProvider`` implements it with the indicator
    engine over stored candles, applying the same closed-bar visibility
    rules as live.
    """

    async def frames(
        self,
        instrument_id: instruments.InstrumentId,
        signal_tf: timeframes.Timeframe,
        requirements: compiler.EngineRequirements,
        from_ms: int,
        to_ms: int,
    ) -> Sequence[structures.BarFrame]:
        """Returns closed signal frames whose bars start in a range.

        Args:
            instrument_id: Exact venue instrument.
            signal_tf: Signal timeframe.
            requirements: Plugins and timeframes to compute.
            from_ms: Inclusive start of the range, epoch ms.
            to_ms: Exclusive end, epoch ms.

        Returns:
            Frames, oldest first (warm-up bars may precede ``from_ms``).
        """
        ...
