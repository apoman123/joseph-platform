# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Records market snapshots as evaluator ``MarketContext`` observations.

``oi_change_pct`` is the percent change of open-interest notional against
the oldest observation kept in the window (24 hours by default). This is a
documented ASSUMPTION: the original design plan (V5) leaves the comparison
horizon to the rule author.
"""

from __future__ import annotations

import collections
import decimal

from joseph.domain import market
from joseph.strategy.domain import evaluator

_HUNDRED = decimal.Decimal(100)


class SnapshotMarketContext:
    """Rolling in-memory market observations per instrument.

    Implements the strategy ``MarketContextProvider`` port.
    """

    def __init__(
        self,
        *,
        window_ms: int = 86_400_000,
        max_points: int = 2_000,
        max_age_ms: int = 300_000,
    ) -> None:
        """Initializes the recorder.

        Args:
            window_ms: Observation history kept per instrument, in ms; also
                the horizon of ``oi_change_pct``.
            max_points: Hard cap of observations per instrument; the oldest
                are dropped first.
            max_age_ms: Maximum age, in ms, of an observation the evaluator
                accepts for a bar close.
        """
        self._window_ms = window_ms
        self._max_age_ms = max_age_ms
        self._points: dict[str, collections.deque[evaluator.MarketPoint]] = (
            collections.defaultdict(
                lambda: collections.deque(maxlen=max_points)
            )
        )

    def record(self, snapshot: market.MarketSnapshot) -> None:
        """Stores one snapshot as an observation.

        Args:
            snapshot: Latest market snapshot.
        """
        key = str(snapshot.instrument_id)
        points = self._points[key]
        oi = (
            None
            if snapshot.open_interest is None
            else snapshot.open_interest.notional_usd
        )
        while points and snapshot.as_of_ms - points[0].ts_ms > self._window_ms:
            points.popleft()
        baseline = next(
            (
                p.open_interest_usd
                for p in points
                if p.open_interest_usd is not None
            ),
            None,
        )
        change = (
            None
            if oi is None or baseline is None or baseline == 0
            else (oi - baseline) / baseline * _HUNDRED
        )
        points.append(
            evaluator.MarketPoint(
                ts_ms=snapshot.as_of_ms,
                funding_8h_pct=(
                    None
                    if snapshot.funding is None
                    else snapshot.funding.normalized_8h_pct
                ),
                open_interest_usd=oi,
                oi_change_pct=change,
                spread_pct=snapshot.spread_pct,
                turnover_24h_quote=snapshot.turnover_24h_quote,
            )
        )

    def context(
        self, instrument_id: str, at_ms: int
    ) -> evaluator.MarketContext | None:
        """Returns observations up to ``at_ms``.

        Args:
            instrument_id: Canonical instrument id.
            at_ms: Bar close.

        Returns:
            Context with the observations taken at or before ``at_ms`` (may
            be empty), or ``None`` when nothing was observed for the
            instrument.
        """
        points = self._points.get(instrument_id)
        if not points:
            return None
        return evaluator.MarketContext(
            points=tuple(p for p in points if p.ts_ms <= at_ms),
            max_age_ms=self._max_age_ms,
        )
