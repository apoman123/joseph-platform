# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Property tests: batch == streaming == replay, and no look-ahead.

Random multi-timeframe series are generated from a seeded 1m walk and
aggregated upward, so every timeframe is mutually consistent. The walk
starts a day before the window the engine computes, so the daily bar of
that day is complete and the pivot points publish levels.
"""

from __future__ import annotations

import json
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
from joseph.indicators.sdk import registry as registry_lib

TF = timeframes.Timeframe
M1, M5, H1, H4, D1 = TF.M1, TF.M5, TF.H1, TF.H4, TF.D1
DAY_MINUTES = 1440
WINDOW_MINUTES = 480
SETTINGS = hypothesis.settings(
    max_examples=8,
    deadline=None,
    suppress_health_check=[hypothesis.HealthCheck.too_slow],
)


def _registry() -> registry_lib.PluginRegistry:
    """Builds the built-ins plus the test plugins.

    Returns:
        Every built-in, an echo plugin reading 1h aux bars and a zone echo.
    """
    registry = catalog.build_default_registry()
    registry.register(testing.EchoPlugin(aux_timeframe=H1))
    registry.register(testing.ZoneEchoPlugin())
    return registry


REGISTRY = _registry()


def _config() -> engine_lib.EngineConfig:
    """Builds a config running every plugin on 5m and some on 1h/4h.

    The echo plugin also runs on 1h and 4h, EMA on 1h, the regression
    channel on a short rolling window and RSI on a short length so all
    publish inside the window.

    Returns:
        The engine configuration, signalling on 5m.
    """
    plugins = {pid: engine_lib.PluginSpec((M5,)) for pid in REGISTRY.ids()}
    plugins["echo"] = engine_lib.PluginSpec((M5, H1, H4))
    plugins["ema"] = engine_lib.PluginSpec((M5, H1))
    plugins["regression_channel"] = engine_lib.PluginSpec((M5,), {"length": 12})
    plugins["rsi"] = engine_lib.PluginSpec((M5,), {"length": 5})
    return engine_lib.EngineConfig(signal_timeframe=M5, plugins=plugins)


def _engine() -> engine_lib.IndicatorEngine:
    """Builds a fresh engine over ``_config``.

    Returns:
        The engine, for the default instrument.
    """
    return engine_lib.IndicatorEngine(
        REGISTRY, _config(), testing.DEFAULT_INSTRUMENT
    )


def _series(seed: int) -> dict[TF, list[market.Candle]]:
    """Generates consistent 1m/5m/1h/4h data and the previous day's bar.

    Args:
        seed: Random-walk seed.

    Returns:
        Candles per timeframe: the 1m to 4h bars of the window, aggregated
        from one 1m walk, plus the completed daily bar before it.
    """
    m1 = testing.random_walk(DAY_MINUTES + WINDOW_MINUTES, seed=seed)
    window = m1[DAY_MINUTES:]
    return {
        M1: window,
        M5: testing.aggregate(window, M5),
        H1: testing.aggregate(window, H1),
        H4: testing.aggregate(window, H4),
        D1: testing.aggregate(m1, D1),
    }


def _fingerprint(frames: list[structures.BarFrame]) -> list[object]:
    """Serializes frames completely for equality checks.

    Args:
        frames: Frames.

    Returns:
        Per frame: close time, contract snapshots keyed by plugin and
        timeframe, contract events and the missing-bar count.
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
        rng: Random source choosing the next stream.

    Returns:
        Every candle once, in an arrival order that keeps each stream's order.
    """
    queues = {tf: list(candles) for tf, candles in data.items()}
    order: list[market.Candle] = []
    while any(queues.values()):
        tf = rng.choice([tf for tf, q in queues.items() if q])
        order.append(queues[tf].pop(0))
    return order


def test_every_plugin_publishes_in_the_window() -> None:
    """The window is long enough for every plugin to publish."""
    frames = _engine().run(_series(1))
    last = frames[-1]
    for plugin_id in REGISTRY.ids():
        snapshot = last.snapshot(plugin_id, M5)
        assert snapshot is not None, plugin_id
        assert snapshot.warmup_complete, plugin_id
        assert any(value is not None for value in snapshot.values.values())
    pivots = last.snapshot("pivot_points", M5)
    assert pivots is not None
    assert len(pivots.aois) == 7


@SETTINGS
@hypothesis.given(
    seed=strategies.integers(0, 10_000),
    order_seed=strategies.integers(0, 10_000),
)
def test_streaming_any_interleaving_equals_batch(
    seed: int, order_seed: int
) -> None:
    """Arrival order across timeframes never changes the frames."""
    data = _series(seed)
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
    data = _series(seed)
    ordered = _interleave(data, random.Random(seed))
    reference = _engine()
    expected = reference.feed(ordered) + reference.flush()
    cut = int(len(ordered) * split)
    first = _engine()
    head = first.feed(ordered[:cut])
    second = _engine()
    second.restore(json.loads(json.dumps(first.checkpoint())))
    tail = second.feed(ordered[cut:]) + second.flush()
    assert _fingerprint(head + tail) == _fingerprint(expected)


@SETTINGS
@hypothesis.given(
    seed=strategies.integers(0, 10_000),
    cut=strategies.integers(120, WINDOW_MINUTES - 20),
)
def test_future_bars_never_change_past_frames(seed: int, cut: int) -> None:
    """Frames up to time t are identical with or without later data."""
    data = _series(seed)
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
    data = _series(seed)
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
@hypothesis.given(
    seed=strategies.integers(0, 10_000),
    plugin_id=strategies.sampled_from(REGISTRY.ids()),
)
def test_per_plugin_batch_compute_matches_engine(
    seed: int, plugin_id: str
) -> None:
    """The plugin batch helper equals the engine's single-series output."""
    candles = testing.random_walk(300, seed=seed, timeframe=M5)
    plugin = REGISTRY.get(plugin_id)
    if plugin.required_streams(None, timeframe=M5) != {protocol.CHART_STREAM}:
        hypothesis.assume(False)
    state = plugin.initialize(testing.DEFAULT_INSTRUMENT, M5, None)
    batch = []
    for candle in candles:
        state, _ = plugin.on_bar_closed(state, protocol.BarInput(candle))
        batch.append(plugin.snapshot(state))
    engine = engine_lib.IndicatorEngine(
        REGISTRY,
        engine_lib.EngineConfig(
            signal_timeframe=M5,
            plugins={plugin_id: engine_lib.PluginSpec((M5,))},
        ),
        testing.DEFAULT_INSTRUMENT,
    )
    frames = engine.run({M5: candles})
    via_engine = [f.snapshot(plugin_id, M5) for f in frames]
    assert [s.to_contract() for s in batch] == [
        s.to_contract() for s in via_engine if s is not None
    ]


@SETTINGS
@hypothesis.given(
    seed=strategies.integers(0, 10_000), extra=strategies.integers(1, 40)
)
def test_appended_bars_never_change_published_history(
    seed: int, extra: int
) -> None:
    """Each plugin's snapshots of a prefix ignore any bars appended later."""
    candles = testing.random_walk(160 + extra, seed=seed, timeframe=M5)
    for plugin_id in REGISTRY.ids():
        plugin = REGISTRY.get(plugin_id)
        if plugin.required_streams(None, timeframe=M5) != {
            protocol.CHART_STREAM
        }:
            continue
        short = testing.run_bars(plugin, candles[:160])
        long = testing.run_bars(plugin, candles)
        assert [s.to_contract() for s, _ in short] == [
            s.to_contract() for s, _ in long[:160]
        ], plugin_id
