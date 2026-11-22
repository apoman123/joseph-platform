"""Unit tests for the resumable stream hub."""

from __future__ import annotations

import pytest

from joseph.domain import clock as clock_lib
from joseph.platform import streaming


def _hub(retention: int = 5, max_queue: int = 100) -> streaming.StreamHub:
    """Builds a hub with a manual clock.

    Args:
        retention: Ring size.
        max_queue: Per-subscriber queue bound.

    Returns:
        Hub.
    """
    return streaming.StreamHub(clock_lib.ManualClock(1), retention, max_queue)


async def test_publish_assigns_monotonic_sequences_and_fans_out() -> None:
    """Subscribers receive only their channels, in sequence order."""
    hub = _hub()
    sub = hub.open(None)
    await hub.add_channels(sub, ["candles:mock:perp:BTCUSD:1m"])
    hub.publish("candles:mock:perp:BTCUSD:1m", "candle.updated", {"a": 1})
    hub.publish("candles:mock:perp:ETHUSD:1m", "candle.updated", {"a": 2})
    hub.publish("candles:mock:perp:BTCUSD:1m", "candle.updated", {"a": 3})
    received = [sub.queue.get_nowait() for _ in range(sub.queue.qsize())]
    assert [e.sequence for e in received] == [1, 3]
    assert hub.last_sequence == 3


async def test_replay_within_retention_and_snapshot_required_beyond() -> None:
    """Resume replays newer envelopes or demands a snapshot."""
    hub = _hub(retention=3)
    sub = hub.open(None)
    await hub.add_channels(sub, ["feed_health"])
    for i in range(5):
        hub.publish("feed_health", "feed.health", {"i": i})
    replay = hub.replay(sub, 3)
    assert replay is not None
    assert [e.sequence for e in replay] == [4, 5]
    assert hub.replay(sub, 5) == []
    assert hub.replay(sub, 0) is None


async def test_signals_are_tenant_scoped() -> None:
    """Tenant-scoped envelopes reach only their tenant."""
    hub = _hub()
    tenant_a = hub.open("tenant-a")
    tenant_b = hub.open("tenant-b")
    anonymous = hub.open(None)
    for sub in (tenant_a, tenant_b, anonymous):
        await hub.add_channels(sub, ["signals"])
    hub.publish("signals", "signal.created", {"s": 1}, tenant_id="tenant-a")
    assert tenant_a.queue.qsize() == 1
    assert tenant_b.queue.qsize() == 0
    assert anonymous.queue.qsize() == 0
    assert hub.replay(tenant_b, 0) == []
    with pytest.raises(ValueError):
        hub.publish("signals", "signal.created", {})


async def test_slow_subscriber_overflows_to_snapshot_required() -> None:
    """Backpressure marks a slow client instead of buffering forever."""
    hub = _hub(max_queue=2)
    sub = hub.open(None)
    await hub.add_channels(sub, ["qualification"])
    for i in range(3):
        hub.publish("qualification", "qualification.changed", {"i": i})
    assert sub.overflowed


async def test_channel_hooks_fire_on_first_and_last_subscriber() -> None:
    """Open/close hooks let feeds start and stop with demand."""
    hub = _hub()
    opened: list[str] = []
    closed: list[str] = []

    async def on_open(channel: str) -> None:
        """Records an open.

        Args:
            channel: Channel key.
        """
        opened.append(channel)

    async def on_close(channel: str) -> None:
        """Records a close.

        Args:
            channel: Channel key.
        """
        closed.append(channel)

    hub.add_channel_hooks(on_open, on_close)
    first, second = hub.open(None), hub.open(None)
    await hub.add_channels(first, ["feed_health"])
    await hub.add_channels(second, ["feed_health"])
    await hub.close(first)
    assert closed == []
    await hub.close(second)
    assert opened == ["feed_health"]
    assert closed == ["feed_health"]
    assert hub.active_channels() == set()


def test_control_envelopes_are_unsequenced() -> None:
    """Control messages never advance a client's resume point."""
    hub = _hub()
    hub.publish("feed_health", "feed.health", {})
    assert hub.control("heartbeat", {}).sequence == 0


def test_channel_keys() -> None:
    """Channel keys follow the contract and separate feed modes."""
    assert (
        streaming.channel_key(
            {
                "channel": "candles",
                "instrument_id": "binance:perp:BTCUSDT",
                "timeframe": "5m",
            }
        )
        == "candles:binance:perp:BTCUSDT:5m"
    )
    assert streaming.candle_channel("x", "5m", "REST_POLL").endswith(
        ":REST_POLL"
    )
    with pytest.raises(ValueError):
        streaming.channel_key({"channel": "candles"})
    with pytest.raises(ValueError):
        streaming.channel_key({"channel": "orders"})
