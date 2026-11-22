"""Unit tests for fill simulation and metrics."""

from __future__ import annotations

import decimal

from joseph.backtest import simulator
from joseph.domain import structures
from joseph.signals.domain import signal as signal_domain
from joseph.strategy.testing import frames as frame_testing

D = decimal.Decimal


def _frames(
    specs: list[tuple[str, str, str, str]],
) -> list[structures.BarFrame]:
    """Builds frames from OHLC tuples.

    Args:
        specs: OHLC per bar.

    Returns:
        Frames.
    """
    factory = frame_testing.FrameFactory()
    return [
        factory.frame(i, frame_testing.bar(*spec))
        for i, spec in enumerate(specs)
    ]


def _entry(
    frames: list[structures.BarFrame],
    index: int,
    *,
    direction: str = "LONG",
    stop: str = "95",
    target: str = "110",
) -> signal_domain.Signal:
    """Builds an entry signal on bar ``index``.

    Args:
        frames: Frames.
        index: Signal bar.
        direction: Trade direction.
        stop: Stop price.
        target: Target price.

    Returns:
        Signal.
    """
    frame = frames[index]
    return signal_domain.Signal(
        signal_id=f"sig-{index}",
        dedupe_key=f"k{index}",
        tenant_id="t",
        strategy_id="s",
        strategy_version_id="s:v1",
        setup_id="x",
        instrument_id="binance:perp:BTCUSDT",
        direction=direction,
        timeframe="5m",
        kind="ENTRY_CONFIRMED",
        setup_state="FIRED",
        severity="ENTRY",
        bar_start_ms=frame.candle.start_ms,
        bar_close_ms=frame.close_ms,
        evaluated_at_ms=frame.close_ms,
        inputs_snapshot_hash="h",
        rule_version="r",
        evaluator_version="1",
        indicator_versions={},
        reason_tree={},
        aoi_ids=(),
        range_ids=(),
        entry_price=frame.candle.close,
        stop_price=D(stop),
        target_price=D(target),
        rr=D(2),
        risk_status="PASS",
        risk_reasons=(),
        mode="PAPER",
        data_source="LIVE",
    )


_NO_COST = simulator.SimulationParams(D(0), D(0), 50)


def test_target_hit() -> None:
    """A long reaching its target earns the planned R."""
    frames = _frames(
        [
            ("100", "101", "99", "100"),
            ("100", "105", "99", "104"),
            ("104", "111", "103", "110"),
        ]
    )
    trades = simulator.simulate([_entry(frames, 0)], frames, _NO_COST)
    assert len(trades) == 1
    trade = trades[0]
    assert (trade.exit_reason, trade.exit_price) == ("TARGET", D(110))
    assert trade.r_multiple == D(2)
    assert trade.hold_bars == 2


def test_stop_assumed_first_when_both_touched() -> None:
    """A bar touching stop and target is a loss."""
    frames = _frames([("100", "101", "99", "100"), ("100", "111", "94", "100")])
    trade = simulator.simulate([_entry(frames, 0)], frames, _NO_COST)[0]
    assert trade.exit_reason == "STOP"
    assert trade.r_multiple == D(-1)


def test_gap_through_stop_fills_at_open() -> None:
    """A later bar opening below the stop exits at that worse open."""
    frames = _frames(
        [
            ("100", "101", "99", "100"),
            ("100", "101", "99", "100"),
            ("93", "94", "92", "93"),
        ]
    )
    trade = simulator.simulate([_entry(frames, 0)], frames, _NO_COST)[0]
    assert (trade.exit_reason, trade.exit_price) == ("STOP", D(93))
    assert trade.r_multiple == D("-1.4")


def test_entry_gapping_beyond_stop_is_skipped() -> None:
    """No trade when the next open is already beyond the stop."""
    frames = _frames([("100", "101", "99", "100"), ("94", "95", "93", "94")])
    assert simulator.simulate([_entry(frames, 0)], frames, _NO_COST) == []


def test_timeout_and_end_of_data() -> None:
    """Trades exit at the close on timeout or at the end of data."""
    flat = [("100", "101", "99", "100")] * 6
    frames = _frames(flat)
    params = simulator.SimulationParams(D(0), D(0), 3)
    trade = simulator.simulate([_entry(frames, 0)], frames, params)[0]
    assert (trade.exit_reason, trade.hold_bars) == ("TIMEOUT", 3)
    long_hold = simulator.simulate([_entry(frames, 0)], frames, _NO_COST)[0]
    assert long_hold.exit_reason == "END_OF_DATA"


def test_short_trade_and_costs() -> None:
    """Shorts mirror longs; fees and slippage reduce R."""
    frames = _frames([("100", "101", "99", "100"), ("100", "101", "89", "90")])
    sig = _entry(frames, 0, direction="SHORT", stop="105", target="90")
    free = simulator.simulate([sig], frames, _NO_COST)[0]
    assert free.r_multiple == D(2)
    costly = simulator.simulate(
        [sig], frames, simulator.SimulationParams(D(10), D(5), 50)
    )[0]
    assert costly.r_multiple < free.r_multiple
    assert costly.fees_r > 0


def test_one_position_at_a_time() -> None:
    """Signals during an open trade are skipped."""
    frames = _frames(
        [("100", "101", "99", "100")] * 4 + [("100", "111", "99", "110")]
    )
    trades = simulator.simulate(
        [_entry(frames, 0), _entry(frames, 1)], frames, _NO_COST
    )
    assert [t.signal_id for t in trades] == ["sig-0"]


def test_metrics() -> None:
    """Win rate, expectancy, profit factor and drawdown in R."""

    def trade(r: str) -> simulator.Trade:
        """Builds a trade with a given R.

        Args:
            r: Net R.

        Returns:
            Trade.
        """
        return simulator.Trade(
            "s", "LONG", 0, D(1), D(0), D(2), 1, D(1), "TARGET", D(r), D(0), 2
        )

    metrics = simulator.compute_metrics(
        [trade("2"), trade("-1"), trade("-1"), trade("3")]
    )
    assert (metrics.trades, metrics.wins, metrics.losses) == (4, 2, 2)
    assert metrics.win_rate == D("0.5")
    assert metrics.expectancy_r == D("0.75")
    assert metrics.profit_factor == D("2.5")
    assert metrics.max_drawdown_r == D(2)
    empty = simulator.compute_metrics([])
    assert empty.win_rate is None and empty.max_drawdown_r == 0
    no_losses = simulator.compute_metrics([trade("1")])
    assert no_losses.profit_factor is None
