# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Tests for the shared live-venue helpers (limits, HTTP, WS, codecs)."""

from __future__ import annotations

import decimal
import json
from typing import Any

import httpx
import pytest
import respx

from joseph_execution.adapters import vault
from joseph_execution.adapters.persistence import sql
from joseph_execution.adapters.venues import base
from joseph_execution.adapters.venues import catalog
from joseph_execution.adapters.venues import credentials
from joseph_execution.adapters.venues import directory
from joseph_execution.adapters.venues import http
from joseph_execution.adapters.venues import ratelimit
from joseph_execution.adapters.venues import wire
from joseph_execution.adapters.venues import ws
from joseph_execution.domain import accounts
from joseph_execution.domain import enums
from joseph_execution.domain import errors
from joseph_execution.platform import clock as clock_lib
from joseph_execution.platform import config
from joseph_execution.ports import trading
from joseph_execution.testing import venue_fakes

D = decimal.Decimal


def _limiter(
    clock: venue_fakes.ManualMonotonic, max_wait: float = 5.0
) -> ratelimit.WindowLimiter:
    """Builds a limiter with a 3-per-10s budget.

    Args:
        clock: Manual monotonic clock; its ``sleep`` advances it.
        max_wait: Longest wait, in seconds, before the limiter refuses.

    Returns:
        A limiter named ``test`` with one ``weight`` budget.
    """
    return ratelimit.WindowLimiter(
        "test",
        [ratelimit.Budget("weight", 3, 10.0)],
        max_wait_s=max_wait,
        monotonic=clock,
        sleep=clock.sleep,
    )


async def test_limiter_waits_for_the_window_then_refuses_beyond_cap() -> None:
    """The limiter sleeps until budget frees and refuses long waits."""
    clock = venue_fakes.ManualMonotonic()
    limiter = _limiter(clock, max_wait=20.0)
    for _ in range(3):
        await limiter.acquire({"weight": 1})
    clock.now += 4.0
    await limiter.acquire({"weight": 1})
    assert clock.sleeps == [pytest.approx(6.0)]
    assert limiter.waits == 1
    strict = _limiter(venue_fakes.ManualMonotonic(), max_wait=1.0)
    for _ in range(3):
        await strict.acquire({"weight": 1})
    with pytest.raises(errors.RateLimitedError) as info:
        await strict.acquire({"weight": 1})
    assert info.value.code == "RATE_LIMITED"
    assert info.value.retryable
    with pytest.raises(ValueError):
        await strict.acquire({"other": 1})
    with pytest.raises(ValueError):
        await strict.acquire({"weight": 4})


async def test_limiter_penalty_and_observed_usage() -> None:
    """A venue 429 pauses all budgets; reported usage is never lowered."""
    clock = venue_fakes.ManualMonotonic()
    limiter = _limiter(clock, max_wait=60.0)
    limiter.observe("weight", 2)
    assert limiter.used("weight") == 2
    limiter.observe("weight", 1)
    assert limiter.used("weight") == 2
    limiter.penalize(30.0)
    await limiter.acquire({"weight": 1})
    assert clock.sleeps[0] == pytest.approx(30.0)
    await limiter.acquire({"weight": 0})


@respx.mock
async def test_http_maps_transport_failures_to_safety_semantics() -> None:
    """A connect error means not sent, a timeout unknown; a 429 pauses."""
    clock = venue_fakes.ManualMonotonic()
    limiter = ratelimit.WindowLimiter(
        "test",
        [ratelimit.Budget("weight", 100, 10.0)],
        max_wait_s=60.0,
        monotonic=clock,
        sleep=clock.sleep,
    )
    client = http.VenueHttp("test", "https://venue.test", limiter)
    respx.get("https://venue.test/refused").mock(
        side_effect=httpx.ConnectError("refused")
    )
    respx.get("https://venue.test/slow").mock(
        side_effect=httpx.ReadTimeout("slow")
    )
    respx.get("https://venue.test/cut").mock(
        side_effect=httpx.RemoteProtocolError("cut")
    )
    respx.get("https://venue.test/busy").mock(
        return_value=httpx.Response(429, headers={"Retry-After": "7"})
    )
    respx.get("https://venue.test/ok").mock(
        return_value=httpx.Response(200, text='{"px": 1.10}')
    )
    with pytest.raises(errors.AdapterUnavailableError) as refused:
        await client.request("GET", "/refused")
    assert not isinstance(refused.value, errors.AdapterTimeoutError)
    with pytest.raises(errors.AdapterTimeoutError):
        await client.request("GET", "/slow")
    with pytest.raises(errors.AdapterTimeoutError):
        await client.request("GET", "/cut")
    with pytest.raises(errors.RateLimitedError) as busy:
        await client.request("GET", "/busy", query="signature=abc")
    assert busy.value.retry_after_s == 7.0
    assert "signature" not in busy.value.message
    reply = await client.request("GET", "/ok")
    assert clock.sleeps == [pytest.approx(7.0)]
    assert reply.payload == {"px": D("1.10")}
    assert "venue.test" in repr(client)
    await client.aclose()


def test_cloid_and_compact_codecs_round_trip() -> None:
    """Joseph client ids survive the 128-bit and 32-character encodings."""
    primary = "j" + "0123456789abcdef0123456789abcd"
    for cid in (primary, f"{primary}-sl", f"{primary}-tp3", f"{primary}-tp35"):
        cloid = wire.cloid_of(cid)
        assert len(cloid) == 34 and cloid.startswith("0x")
        assert wire.client_id_of_cloid(cloid) == cid
        compact = wire.compact_id_of(cid)
        assert len(compact) <= 32
        assert wire.client_id_of_compact(compact) == cid
    foreign = wire.cloid_of("manual-order-1")
    assert (
        foreign.startswith("0xff") and wire.client_id_of_cloid(foreign) is None
    )
    assert wire.client_id_of_cloid("0x12") is None
    assert wire.cloid_of(f"{primary}-tp300").startswith("0xff")
    assert wire.compact_id_of("short_id") == "short_id"
    hashed = wire.compact_id_of("x" * 40)
    assert hashed.startswith("h") and wire.client_id_of_compact(hashed) is None
    assert wire.compact_id_of(f"{primary}-tp99").startswith("h")
    index = wire.index_of(primary, 48)
    assert 1 <= index < 2**48
    assert index == wire.index_of(primary, 48)


def test_wire_symbols_and_decimals() -> None:
    """Instrument ids map to venue symbols; foreign markets are refused."""
    assert wire.symbol_of("binance", "binance:perp:BTCUSDT") == "BTCUSDT"
    assert wire.instrument_of("mexc", "BTC_USDT") == "mexc:perp:BTC_USDT"
    with pytest.raises(errors.AdapterUnavailableError):
        wire.symbol_of("binance", "bitget:perp:BTCUSDT")
    with pytest.raises(errors.AdapterUnavailableError):
        wire.symbol_of("binance", "binance:spot:BTCUSDT")
    assert wire.text(D("95000.00")) == "95000"
    assert wire.dec(None) == 0 and wire.opt_dec("") is None
    assert wire.dec(D("0.10")) == D("0.1")


async def test_credentials_are_redacted_and_environment_parsed() -> None:
    """Credential values never appear in reprs; environment selects nets."""
    creds = credentials.from_fields(
        {"api_key": "pub", "api_secret": "TOP-SECRET-1", "environment": "demo"}
    )
    assert creds.sandbox is True
    assert "TOP-SECRET-1" not in repr(creds)
    assert creds.missing(("api_key", "passphrase")) == ["passphrase"]
    assert credentials.from_fields({"environment": "mainnet"}).sandbox is False
    assert credentials.from_fields({}).sandbox is None
    with pytest.raises(errors.ExecutionError):
        credentials.from_fields({"environment": "staging"})
    static = credentials.StaticCredentials({"a": {"api_key": "k"}})
    assert "k" not in repr(static).replace("accounts", "")
    assert (await static.load("a")) is not None
    assert await static.load("b") is None
    assert await credentials.NoCredentials().load("a") is None


async def test_vault_source_reads_sealed_envelopes_and_revocation() -> None:
    """The vault source decrypts per call and sees revocations at once."""
    factory = sql.SqlUnitOfWorkFactory(sql.create_engine("sqlite://"))
    box = vault.AesGcmVault(b"v" * 32)
    source = credentials.VaultCredentialSource(factory, box)
    account = accounts.Account(
        "acct-1",
        "t",
        "binance",
        "x",
        enums.AccountStatus.CONNECTED,
        (),
        "USD",
        1,
        credential_id="cred-1",
    )
    with factory() as uow:
        uow.accounts.add(account)
        uow.credentials.add(
            "cred-1", "t", "acct-1", box.seal({"api_key": "pub"}), 1
        )
    loaded = await source.load("acct-1")
    assert loaded is not None and loaded.get("api_key") == "pub"
    assert "REDACTED" in repr(source)
    assert await source.load("missing") is None
    with factory() as uow:
        uow.credentials.delete_for_account("acct-1")
    assert await source.load("acct-1") is None


def test_order_directory_projects_live_orders() -> None:
    """The directory finds OMS orders by client id and lists recent ones."""
    factory = sql.SqlUnitOfWorkFactory(sql.create_engine("sqlite://"))
    found = directory.RepositoryOrderDirectory(factory)
    assert found.find("a", "c") is None
    assert directory.EmptyDirectory().find("a", "c") is None
    assert directory.EmptyDirectory().recent("a", 0) == []
    assert found.recent("a", 0) == []


async def _stream_url() -> str:
    """Returns the test stream URL."""
    return "wss://venue.test/ws"


def _spec(
    parse_frames: list[str],
    resync_items: list[ws.StreamUpdate],
    opens: list[int],
    **kwargs: object,
) -> ws.PrivateStreamSpec:
    """Builds a stream spec over JSON frames.

    Args:
        parse_frames: Receives the text of every frame parsed.
        resync_items: Updates that each resync replays.
        opens: Receives a 1 per ``on_open`` call.
        **kwargs: ``PrivateStreamSpec`` fields that replace the defaults.

    Returns:
        A spec named ``test stream`` for ``wss://venue.test/ws``.
    """

    async def on_open(connection: ws.WsConnection) -> None:
        """Counts the open in ``opens`` and subscribes.

        Args:
            connection: Freshly opened connection.
        """
        opens.append(1)
        await connection.send('{"op":"subscribe"}')

    def parse(frame: str | bytes) -> list[ws.StreamUpdate]:
        """Parses a JSON frame and records its text in ``parse_frames``.

        Args:
            frame: JSON text or bytes.

        Returns:
            A reconnect request for ``reconnect`` frames, else one fill whose
            venue fill id is the frame's ``id``.

        Raises:
            KeyError: The frame sets ``bad``.
            VenuePermissionError: The frame sets ``deny``.
        """
        message = json.loads(frame)
        parse_frames.append(str(frame))
        if message.get("reconnect"):
            return [ws.StreamUpdate(reconnect=True)]
        if message.get("bad"):
            raise KeyError("bad frame")
        if message.get("deny"):
            raise errors.VenuePermissionError("permission missing", venue="t")
        return [
            ws.StreamUpdate(
                fill=trading.VenueFill(
                    str(message["id"]),
                    "c",
                    D(1),
                    D(1),
                    D(0),
                    "USDT",
                    "taker",
                    1,
                )
            )
        ]

    async def resync() -> list[ws.StreamUpdate]:
        """Returns the snapshot."""
        return list(resync_items)

    fields: dict[str, object] = {
        "name": "test stream",
        "url": _stream_url,
        "on_open": on_open,
        "parse": parse,
        "resync": resync,
    }
    fields.update(kwargs)
    return ws.PrivateStreamSpec(**fields)  # type: ignore[arg-type]  # dict[str, object] holds each field's own type


async def test_stream_reconnects_resubscribes_and_resyncs() -> None:
    """Dropped streams back off, resubscribe and replay the snapshot."""
    clock = venue_fakes.ManualMonotonic()
    connector = venue_fakes.FakeConnector(
        [
            venue_fakes.Script(['{"id": 1}', '{"bad": 1}'], OSError("drop")),
            venue_fakes.Script([], refuse=OSError("refused")),
            venue_fakes.Script(['{"id": 2}', '{"reconnect": 1}']),
            venue_fakes.Script(['{"id": 3}']),
        ],
        clock,
    )
    snapshot = [
        ws.StreamUpdate(position=trading.VenuePosition("i", D(1), None))
    ]
    frames: list[str] = []
    opens: list[int] = []
    stats = ws.StreamStats()
    stream = ws.run_private_stream(
        _spec(frames, snapshot, opens),
        connect=connector,
        monotonic=clock,
        sleep=clock.sleep,
        jitter=lambda: 1.0,
        stats=stats,
    )
    items: list[Any] = await venue_fakes.collect(stream, 6)
    kinds = [
        (
            "resync" if replay else "live",
            u.fill.exchange_fill_id if u.fill else "pos",
        )
        for u, replay in items
    ]
    assert kinds == [
        ("resync", "pos"),
        ("live", "1"),
        ("resync", "pos"),
        ("live", "2"),
        ("resync", "pos"),
        ("live", "3"),
    ]
    assert len(opens) == 3
    assert all(c.sent == ['{"op":"subscribe"}'] for c in connector.connections)
    assert stats.parse_errors == 1 and stats.failures == 2
    assert clock.sleeps[:2] == [0.5, 1.0]


async def test_stream_pings_keeps_alive_and_gives_up() -> None:
    """Pings and keepalives run on schedule; silence and failures end."""
    clock = venue_fakes.ManualMonotonic()
    keepalives: list[int] = []

    async def keepalive() -> None:
        """Counts keepalives."""
        keepalives.append(1)

    connector = venue_fakes.FakeConnector(
        [venue_fakes.Script([]) for _ in range(3)], clock
    )
    stats = ws.StreamStats()
    stream = ws.run_private_stream(
        _spec(
            [],
            [],
            [],
            ping_message="ping",
            ping_interval_s=20.0,
            idle_timeout_s=100.0,
            keepalive=keepalive,
            keepalive_interval_s=50.0,
        ),
        connect=connector,
        monotonic=clock,
        sleep=clock.sleep,
        max_failures=3,
        stats=stats,
    )
    with pytest.raises(errors.AdapterUnavailableError):
        await venue_fakes.collect(stream, 1)
    assert stats.pings >= 3 and keepalives
    assert "ping" in connector.connections[0].sent
    assert stats.failures == 3 and stats.connects == 3


async def test_stream_permission_errors_and_session_age() -> None:
    """Login refusals propagate; sessions are recycled at max age."""
    clock = venue_fakes.ManualMonotonic()
    connector = venue_fakes.FakeConnector(
        [venue_fakes.Script(['{"deny": 1}'])], clock
    )
    stream = ws.run_private_stream(
        _spec([], [], []), connect=connector, monotonic=clock, sleep=clock.sleep
    )
    with pytest.raises(errors.VenuePermissionError):
        await venue_fakes.collect(stream, 1)
    aged = venue_fakes.FakeConnector(
        [venue_fakes.Script([]), venue_fakes.Script(['{"id": 9}'])], clock
    )
    stream = ws.run_private_stream(
        _spec([], [], [], max_session_s=10.0, idle_timeout_s=1000.0),
        connect=aged,
        monotonic=clock,
        sleep=clock.sleep,
    )
    items = await venue_fakes.collect(stream, 1)
    assert len(aged.urls) == 2 and len(items) == 1


async def test_catalog_without_credentials_is_fail_closed() -> None:
    """No credential source means fail-closed adapters; a clock is needed."""
    adapters = catalog.build_live_adapters()
    assert all(
        isinstance(a, base.FailClosedVenueAdapter) for a in adapters.values()
    )
    caps = await adapters["binance"].capabilities("a")
    assert "No live trading adapter or credentials" in caps.notes[-1]
    with pytest.raises(ValueError):
        catalog.build_live_adapters(credentials.NoCredentials())
    live_adapters = catalog.build_live_adapters(
        credentials.NoCredentials(),
        clock_lib.ManualClock(),
        sandbox_venues=("bitget",),
        urls={"binance": {"rest_url": "https://proxy.test"}},
    )
    assert sorted(live_adapters) == sorted(catalog.DOCUMENTED)
    assert "proxy.test" not in repr(live_adapters["binance"])


def test_venue_settings_from_environment() -> None:
    """``JOSEPH_VENUE_*`` variables parse; bad values are refused."""
    settings = config.from_env(
        {
            "JOSEPH_VENUE_SANDBOX": "binance, bitget",
            "JOSEPH_VENUE_URLS": "binance.testnet_rest_url=https://t.test",
            "JOSEPH_VENUE_MARKET_SLIPPAGE_BPS": "25",
            "JOSEPH_LIVE_STREAMS": "false",
        }
    )
    assert settings.venues.sandbox_venues == ("binance", "bitget")
    assert settings.venues.urls == {
        "binance": {"testnet_rest_url": "https://t.test"}
    }
    assert settings.venues.market_slippage_bps == 25
    assert settings.venues.streams is False
    assert "sandbox_venues" in repr(settings)
    every = config.from_env({"JOSEPH_VENUE_SANDBOX": "all"})
    assert every.venues.sandbox_venues == config.CRYPTO_VENUES
    with pytest.raises(ValueError):
        config.from_env({"JOSEPH_VENUE_SANDBOX": "kraken"})
    with pytest.raises(ValueError):
        config.from_env({"JOSEPH_VENUE_URLS": "binance=https://x"})
    assert config.from_env({}).live_trading_enabled is False
