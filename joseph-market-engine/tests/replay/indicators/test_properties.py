"""Property tests: batch == streaming == replay, and no look-ahead.

Random multi-timeframe series are generated from a seeded 1m walk and
aggregated upward, so every timeframe is mutually consistent.
"""

from __future__ import annotations

import random

import hypothesis
from hypothesis import strategies

from joseph.domain import market
from joseph.domain import structures
from joseph.domain import timeframes
from joseph.indicators import catalog
from joseph.indicators import testing
from joseph.indicators.sdk import engine as engine_lib
from joseph.indicators.sdk import protocol

TF = timeframes.Timeframe
M1, M5, H1, H4 = TF.M1, TF.M5, TF.H1, TF.H4
REGISTRY = catalog.build_default_registry()
SETTINGS = hypothesis.settings(
    max_examples=8,
    deadline=None,
    suppress_health_check=[hypothesis.HealthCheck.too_slow],
)


def _config() -> engine_lib.EngineConfig:
    """All seven plugins on 5m plus HTF structure and gaps.

    Returns:
        Engine configuration.
    """
    plugins = {pid: engine_lib.PluginSpec((M5,)) for pid in REGISTRY.ids()}
    plugins["daily_bias"] = engine_lib.PluginSpec((M5, H1, H4))
    plugins["fvg_ifvg"] = engine_lib.PluginSpec((M5, H1))
    plugins["premium_discount_ote"] = engine_lib.PluginSpec(
        (M5,), {"swing_length": 3}
    )
    return engine_lib.EngineConfig(signal_timeframe=M5, plugins=plugins)


def _engine() -> engine_lib.IndicatorEngine:
    """Builds a fresh engine.

    Returns:
        Engine.
    """
    return engine_lib.IndicatorEngine(
        REGISTRY, _config(), testing.DEFAULT_INSTRUMENT
    )


def _series(seed: int, minutes: int) -> dict[TF, list[market.Candle]]:
    """Generates consistent 1m/5m/1h/4h data.

    Args:
        seed: Random seed.
        minutes: Number of 1m bars.

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


def _fingerprint(frames: list[structures.BarFrame]) -> list[object]:
    """Serializes frames completely for equality checks.

    Args:
        frames: Frames.

    Returns:
        JSON-compatible structure.
    """
    return [
        [
            frame.close_ms,
            {
                f"{k[0]}@{k[1].value}": s.to_contract()
                for k, s in sorted(
                    frame.snapshots.items(),
                    key=lambda kv: (kv[0][0], kv[0][1].value),
                )
            },
            [e.to_contract() for e in frame.events],
            frame.missing_bars,
        ]
        for frame in frames
    ]


def _interleave(
    data: dict[TF, list[market.Candle]], rng: random.Random
) -> list[market.Candle]:
    """Merges per-timeframe streams in a random but per-stream order.

    Args:
        data: Candles per timeframe.
        rng: Random source.

    Returns:
        Arrival order.
    """
    queues = {tf: list(candles) for tf, candles in data.items()}
    order: list[market.Candle] = []
    while any(queues.values()):
        tf = rng.choice([tf for tf, q in queues.items() if q])
        order.append(queues[tf].pop(0))
    return order


@SETTINGS
@hypothesis.given(
    seed=strategies.integers(0, 10_000),
    order_seed=strategies.integers(0, 10_000),
)
def test_streaming_any_interleaving_equals_batch(
    seed: int, order_seed: int
) -> None:
    """Arrival order across timeframes never changes the frames."""
    data = _series(seed, 480)
    batch = _engine().run(data)
    engine = _engine()
    streamed = engine.feed(_interleave(data, random.Random(order_seed)))
    streamed += engine.flush()
    assert _fingerprint(streamed) == _fingerprint(batch)


@SETTINGS
@hypothesis.given(
    seed=strategies.integers(0, 10_000), split=strategies.floats(0.1, 0.9)
)
def test_checkpoint_replay_equals_uninterrupted_run(
    seed: int, split: float
) -> None:
    """Restoring from a JSON checkpoint continues identically."""
    data = _series(seed, 480)
    ordered = _interleave(data, random.Random(seed))
    reference = _engine()
    expected = reference.feed(ordered) + reference.flush()
    cut = int(len(ordered) * split)
    first = _engine()
    head = first.feed(ordered[:cut])
    second = _engine()
    second.restore(first.checkpoint())
    tail = second.feed(ordered[cut:]) + second.flush()
    assert _fingerprint(head + tail) == _fingerprint(expected)


@SETTINGS
@hypothesis.given(
    seed=strategies.integers(0, 10_000), cut=strategies.integers(120, 460)
)
def test_future_bars_never_change_past_frames(seed: int, cut: int) -> None:
    """Frames up to time t are identical with or without later data."""
    data = _series(seed, 480)
    full = _engine().run(data)
    limit = data[M1][cut].start_ms
    prefix = {
        tf: [c for c in candles if c.end_ms <= limit]
        for tf, candles in data.items()
    }
    partial = _engine().run(prefix)
    # Frames whose higher-timeframe barrier was satisfied in the prefix.
    comparable = [f for f in partial if f.close_ms <= H4.floor(limit)]
    assert _fingerprint(comparable) == _fingerprint(full[: len(comparable)])


@SETTINGS
@hypothesis.given(seed=strategies.integers(0, 10_000))
def test_runs_are_deterministic_and_scores_bounded(seed: int) -> None:
    """Same input, same output; every score is an int in 0..100."""
    data = _series(seed, 480)
    first = _engine().run(data)
    second = _engine().run(data)
    assert _fingerprint(first) == _fingerprint(second)
    for frame in first:
        for snapshot in frame.snapshots.values():
            for aoi in snapshot.aois:
                assert aoi.quality_score is None or (
                    0 <= aoi.quality_score <= 100
                )
                assert aoi.lower <= aoi.upper
                assert aoi.confirmed_at_ms <= snapshot.as_of_ms
            assert snapshot.as_of_ms <= frame.close_ms
        for event in frame.events:
            assert event.score is None or 0 <= event.score <= 100
            assert event.confirmed_at_ms <= frame.close_ms


@SETTINGS
@hypothesis.given(seed=strategies.integers(0, 10_000))
def test_per_plugin_batch_compute_matches_engine(seed: int) -> None:
    """The plugin batch helper equals the engine's single-series output."""
    candles = testing.random_walk(300, seed=seed, timeframe=M5)
    plugin = REGISTRY.get("liquidity_sweeps")
    state = plugin.initialize(testing.DEFAULT_INSTRUMENT, M5, None)
    batch = []
    for candle in candles:
        state, _ = plugin.on_bar_closed(state, protocol.BarInput(candle))
        batch.append(plugin.snapshot(state))
    engine = engine_lib.IndicatorEngine(
        REGISTRY,
        engine_lib.EngineConfig(
            signal_timeframe=M5,
            plugins={"liquidity_sweeps": engine_lib.PluginSpec((M5,))},
        ),
        testing.DEFAULT_INSTRUMENT,
    )
    frames = engine.run({M5: candles})
    via_engine = [f.snapshot("liquidity_sweeps", M5) for f in frames]
    assert [s.to_contract() for s in batch] == [
        s.to_contract() for s in via_engine if s is not None
    ]
