"""Unit tests for pipeline helpers: requirement merging, gate, context."""

from __future__ import annotations

import dataclasses
import decimal

from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import quality
from joseph.domain import timeframes
from joseph.pipeline import frames
from joseph.pipeline import market_context
from joseph.pipeline import qualification
from joseph.screening.domain import policy
from joseph.strategy.domain import compiler
from joseph.strategy.domain import tristate

TF = timeframes.Timeframe
_BTC = instruments.InstrumentId.parse("binance:perp:BTCUSDT")


def test_merge_requirements_unions_plugins_per_signal_timeframe() -> None:
    """Two plans on 5m share one engine computing every plugin."""
    a = compiler.EngineRequirements(
        TF.M5, {"fvg_ifvg": frozenset({TF.M5})}, history_bars=50
    )
    b = compiler.EngineRequirements(
        TF.M5,
        {"fvg_ifvg": frozenset({TF.H1}), "daily_bias": frozenset({TF.H4})},
        history_bars=120,
    )
    c = compiler.EngineRequirements(TF.H1, {}, history_bars=10)
    merged = frames.merge_requirements([a, b, c])
    assert set(merged) == {TF.M5, TF.H1}
    assert merged[TF.M5].plugins["fvg_ifvg"] == frozenset({TF.M5, TF.H1})
    assert merged[TF.M5].plugins["daily_bias"] == frozenset({TF.H4})
    assert merged[TF.M5].history_bars == 120


def test_fetch_windows_extend_higher_timeframes() -> None:
    """Higher timeframes are fetched further back for pivot warm-up."""
    start = TF.M5.floor(1_795_000_000_000)
    windows = frames.fetch_windows({TF.M1, TF.M5, TF.H4}, TF.M5, start)
    assert windows[TF.M1] == start and windows[TF.M5] == start
    assert windows[TF.H4] < start - 60 * TF.H4.duration_ms


class _FakeService:
    """Qualification service stub returning fixed records."""

    def __init__(self, statuses: dict[str, policy.Status]) -> None:
        """Initializes the stub.

        Args:
            statuses: Status per instrument id.
        """
        self.statuses = statuses
        self.fail = False

    async def report(self, venue: instruments.Venue) -> object:
        """Returns a report-like object.

        Args:
            venue: Venue (unused).

        Returns:
            Object with ``records``.

        Raises:
            RuntimeError: When ``fail`` is set.
        """
        del venue
        if self.fail:
            raise RuntimeError("venue down")

        @dataclasses.dataclass
        class Record:
            """Minimal record."""

            instrument_id: str
            status: policy.Status

        @dataclasses.dataclass
        class Report:
            """Minimal report."""

            records: list[Record]

        return Report([Record(k, v) for k, v in self.statuses.items()])


class _Clock:
    """Settable clock."""

    def __init__(self) -> None:
        """Initializes at t=0."""
        self.now = 0

    def now_ms(self) -> int:
        """Returns the time."""
        return self.now


async def test_gate_is_tristate_and_honors_minimum_minutes() -> None:
    """Missing is UNKNOWN, rejected is FALSE, recent qualification waits."""
    service = _FakeService(
        {
            "binance:perp:A": policy.Status.QUALIFIED,
            "binance:perp:B": policy.Status.REJECTED,
            "binance:perp:C": policy.Status.INSUFFICIENT_DATA,
        }
    )
    clock = _Clock()
    gate = qualification.CachedQualificationGate(service, clock)  # type: ignore[arg-type]
    await gate.refresh([instruments.Venue.BINANCE])
    t = tristate.TriState
    assert gate.qualification("binance:perp:A", 0, 0) is t.TRUE
    assert gate.qualification("binance:perp:A", 60_000, 5) is t.FALSE
    assert gate.qualification("binance:perp:A", 300_000, 5) is t.TRUE
    assert gate.qualification("binance:perp:B", 0, 0) is t.FALSE
    assert gate.qualification("binance:perp:C", 0, 0) is t.UNKNOWN
    assert gate.qualification("binance:perp:Z", 0, 0) is t.UNKNOWN
    assert gate.ranked(instruments.Venue.BINANCE)[0] == "binance:perp:A"
    service.fail = True
    await gate.refresh([instruments.Venue.BINANCE])
    assert gate.is_qualified("binance:perp:A")


def _snapshot(ts: int, oi: str) -> market.MarketSnapshot:
    """Builds a snapshot with open interest.

    Args:
        ts: Observation time.
        oi: Open interest notional.

    Returns:
        Snapshot.
    """
    return market.MarketSnapshot(
        instrument_id=_BTC,
        feed_state=quality.FeedState.LIVE,
        source="test",
        as_of_ms=ts,
        best_bid=decimal.Decimal("99"),
        best_ask=decimal.Decimal("101"),
        open_interest=market.OpenInterest(
            _BTC, decimal.Decimal(1), decimal.Decimal(oi), "test", ts
        ),
    )


def test_market_context_computes_oi_change_and_respects_time() -> None:
    """OI change is relative to the oldest kept observation; no look-ahead."""
    recorder = market_context.SnapshotMarketContext(max_age_ms=10**9)
    recorder.record(_snapshot(1_000, "100"))
    recorder.record(_snapshot(2_000, "90"))
    context = recorder.context(str(_BTC), 2_000)
    assert context is not None
    assert context.value_at("oi_change_pct", 2_000) == decimal.Decimal(-10)
    early = recorder.context(str(_BTC), 1_500)
    assert early is not None
    assert early.value_at("oi_change_pct", 1_500) is None
    assert recorder.context("binance:perp:NONE", 1) is None
