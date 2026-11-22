"""Unit tests for the multi-timeframe indicator engine."""

from __future__ import annotations

import dataclasses

import pytest

from joseph.domain import errors
from joseph.domain import timeframes
from joseph.indicators import catalog
from joseph.indicators import testing
from joseph.indicators.sdk import engine as engine_lib

TF = timeframes.Timeframe
M1, M5, H1, H4 = TF.M1, TF.M5, TF.H1, TF.H4


def _data(minutes: int = 900, seed: int = 21) -> dict[TF, list]:  # type: ignore[type-arg]
    """Builds consistent 1m/5m/1h/4h candles by aggregation.

    Args:
        minutes: Number of 1m bars.
        seed: Random seed.

    Returns:
        Candles per timeframe.
    """
    m1 = testing.random_walk(minutes, seed=seed)
    return {
        M1: m1,
        M5: testing.aggregate(m1, M5),
        H1: testing.aggregate(m1, H1),
        H4: testing.aggregate(m1, H4),
    }


def _engine(
    plugins: dict[str, engine_lib.PluginSpec] | None = None,
) -> engine_lib.IndicatorEngine:
    """Builds an engine with a 5m signal timeframe.

    Args:
        plugins: Plugin specs (defaults to MTF daily bias + order flow).

    Returns:
        The engine.
    """
    specs = plugins or {
        "daily_bias": engine_lib.PluginSpec((M5, H1, H4)),
        "order_flow_pressure": engine_lib.PluginSpec((M5,)),
        "auction_value_areas": engine_lib.PluginSpec((M5,)),
    }
    return engine_lib.IndicatorEngine(
        catalog.build_default_registry(),
        engine_lib.EngineConfig(signal_timeframe=M5, plugins=specs),
        testing.DEFAULT_INSTRUMENT,
    )


def _fingerprint(frames: list) -> list:  # type: ignore[type-arg]
    """Serializes frames for equality checks.

    Args:
        frames: Bar frames.

    Returns:
        Comparable JSON-compatible structure.
    """
    return [
        (
            frame.close_ms,
            sorted(
                (
                    key[0],
                    key[1].value,
                    snap.to_contract()["as_of_ms"],
                    str(snap.to_contract()["values"]),
                    len(snap.aois),
                )
                for key, snap in frame.snapshots.items()
            ),
            [event.to_contract() for event in frame.events],
            frame.missing_bars,
        )
        for frame in frames
    ]


def test_required_timeframes_include_dependencies() -> None:
    """Intrabar and aux timeframes are consumed automatically."""
    assert _engine().required_timeframes == frozenset({M1, M5, H1, H4})


def test_one_frame_per_closed_signal_bar() -> None:
    """Batch mode yields a frame for every 5m bar."""
    data = _data()
    frames = _engine().run(data)
    assert [f.close_ms for f in frames] == [c.end_ms for c in data[M5]]


def test_higher_timeframe_snapshots_are_never_from_open_bars() -> None:
    """A 5m frame only sees the last 1h/4h bar closed at or before it."""
    frames = _engine().run(_data())
    for frame in frames:
        for tf in (H1, H4):
            snapshot = frame.snapshot("daily_bias", tf)
            expected = tf.floor(frame.close_ms)
            if snapshot is None:
                assert expected < testing.DEFAULT_START_MS + tf.duration_ms
                continue
            assert snapshot.as_of_ms == expected
            assert snapshot.as_of_ms <= frame.close_ms


def test_hourly_events_appear_in_the_frame_closing_with_the_hour() -> None:
    """HTF events land in the 5m frame whose close equals the HTF close."""
    frames = _engine().run(_data(minutes=3000, seed=4))
    htf_events = [
        (frame.close_ms, event)
        for frame in frames
        for event in frame.events
        if event.timeframe is not M5
    ]
    assert htf_events, "expected at least one higher-timeframe event"
    for close_ms, event in htf_events:
        assert event.confirmed_at_ms == close_ms


def test_late_hourly_candle_waits_instead_of_leaking() -> None:
    """Feeding 1h data late yields identical frames (watermark gating)."""
    data = _data()
    batch = _engine().run(data)
    engine = _engine()
    streamed = engine.feed(
        sorted(data[M1] + data[M5] + data[H4], key=lambda c: c.end_ms)
    )
    assert len(streamed) < len(batch)
    streamed += engine.feed(data[H1])
    streamed += engine.flush()
    assert _fingerprint(streamed) == _fingerprint(batch)


def test_duplicates_revisions_and_out_of_order_are_ignored() -> None:
    """Re-deliveries never produce extra frames or rewrite history."""
    data = _data(minutes=120)
    engine = _engine({"fvg_ifvg": engine_lib.PluginSpec((M5,))})
    frames = engine.run({M5: data[M5]})
    last = data[M5][-1]
    assert engine.on_candle(last) == []
    assert engine.on_candle(dataclasses.replace(last, revision=1)) == []
    assert engine.on_candle(data[M5][0]) == []
    assert engine.stats.duplicates == 1
    assert engine.stats.revisions_ignored == 1
    assert engine.stats.out_of_order == 1
    assert len(frames) == len(data[M5])


def test_non_final_candles_are_ignored() -> None:
    """Unconfirmed bars never reach plugins."""
    engine = _engine({"fvg_ifvg": engine_lib.PluginSpec((M5,))})
    open_bar = testing.make_candle(
        M5, testing.DEFAULT_START_MS, 1, 2, 0.5, 1.5, is_final=False
    )
    assert engine.on_candle(open_bar) == []
    assert engine.stats.ignored_non_final == 1


def test_missing_signal_bars_are_counted() -> None:
    """Gaps in the signal series surface as missing_bars."""
    data = _data(minutes=120)
    engine = _engine({"fvg_ifvg": engine_lib.PluginSpec((M5,))})
    candles = data[M5][:5] + data[M5][7:]
    frames = engine.run({M5: candles})
    assert frames[-1].missing_bars == 2


def test_stalled_intrabar_feed_blocks_until_flush() -> None:
    """Missing intrabars hold frames back; flush forces them out."""
    data = _data(minutes=60)
    engine = _engine({"order_flow_pressure": engine_lib.PluginSpec((M5,))})
    assert engine.feed(data[M5]) == []
    frames = engine.flush()
    assert len(frames) == len(data[M5])
    assert engine.stats.forced_items > 0
    assert (
        frames[-1].snapshot("order_flow_pressure", M5).values["delta_pct"]
        is None
    )


def test_checkpoint_restore_continues_identically() -> None:
    """Restoring mid-stream reproduces the remaining frames exactly."""
    data = _data(minutes=1500, seed=8)
    ordered = sorted(
        (c for series in data.values() for c in series),
        key=lambda c: (c.end_ms, c.timeframe.duration_ms),
    )
    reference = _engine()
    expected = reference.feed(ordered) + reference.flush()
    split = len(ordered) // 2
    first = _engine()
    head = first.feed(ordered[:split])
    blob = first.checkpoint()
    second = _engine()
    second.restore(blob)
    tail = second.feed(ordered[split:]) + second.flush()
    assert _fingerprint(head + tail) == _fingerprint(expected)


def test_invalid_configurations_are_rejected() -> None:
    """Unsupported timeframes and non-nesting intrabars fail validation."""
    with pytest.raises(errors.ValidationFailedError):
        _engine({"order_flow_pressure": engine_lib.PluginSpec((M1,))})
    with pytest.raises(errors.ValidationFailedError):
        _engine(
            {
                "order_flow_pressure": engine_lib.PluginSpec(
                    (TF.M3,), {"intrabar_tf": "5m"}
                )
            }
        )
    with pytest.raises(errors.NotFoundError):
        _engine({"nope": engine_lib.PluginSpec((M5,))})


def test_foreign_instrument_is_rejected() -> None:
    """Candles of another instrument raise."""
    other = testing.make_candle(
        M5,
        testing.DEFAULT_START_MS,
        1,
        2,
        0.5,
        1.5,
        instrument_id=testing.DEFAULT_INSTRUMENT.parse("bitget:perp:BTCUSDT"),
    )
    with pytest.raises(ValueError):
        _engine().on_candle(other)
