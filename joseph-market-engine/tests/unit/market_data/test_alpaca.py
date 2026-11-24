# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Unit tests for Alpaca configuration, parsing, classification and stream."""

from __future__ import annotations

import asyncio
import contextlib
import datetime
import decimal
import json
import pathlib
from collections.abc import AsyncIterator
from collections.abc import Callable
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import respx
import websockets

from joseph.domain import clock as clock_lib
from joseph.domain import errors
from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import sessions
from joseph.domain import timeframes
from joseph.market_data.adapters import alpaca
from joseph.market_data.adapters import factory
from joseph.market_data.adapters import http
from joseph.market_data.app import candle_store
from joseph.market_data.app import feed_router
from joseph.market_data.app import registry
from joseph.market_data.app import service as service_lib
from joseph.market_data.domain import classification
from joseph.market_data.domain import health
from joseph.platform import config

_D = decimal.Decimal
_KEYS = alpaca.AlpacaConfig(key_id="kid", secret_key="sek")


def test_config_reads_env_and_redacts_secrets() -> None:
    """Keys come from the environment and never appear in reprs."""
    cfg = alpaca.AlpacaConfig.from_env(
        {
            "JOSEPH_ALPACA_KEY_ID": "AKID123",
            "JOSEPH_ALPACA_SECRET_KEY": "s3cret",
            "JOSEPH_ALPACA_FEED": "DELAYED_SIP",
            "JOSEPH_ALPACA_SECONDS_HISTORY_MS": "60000",
        }
    )
    assert cfg.configured and cfg.feed is alpaca.Feed.DELAYED_SIP
    assert cfg.rest_feed == "sip" and cfg.delay_ms == 900_000
    assert cfg.seconds_history_ms == 60_000
    assert "AKID123" not in repr(cfg) and "s3cret" not in repr(cfg)
    assert cfg.headers() == {
        "APCA-API-KEY-ID": "AKID123",
        "APCA-API-SECRET-KEY": "s3cret",
    }
    empty = alpaca.AlpacaConfig.from_env({})
    assert not empty.configured and empty.headers() == {}
    assert empty.feed is alpaca.Feed.IEX and empty.delay_ms == 0
    with pytest.raises(ValueError):
        alpaca.AlpacaConfig.from_env({"JOSEPH_ALPACA_FEED": "opra"})


def test_settings_carry_alpaca_options_without_leaking_them() -> None:
    """Engine settings parse the Alpaca variables and keep them out of repr."""
    settings = config.Settings.from_env(
        {
            "JOSEPH_ALPACA_KEY_ID": "AKID123",
            "JOSEPH_ALPACA_SECRET_KEY": "s3cret",
            "JOSEPH_ALPACA_FEED": "sip",
        }
    )
    assert settings.alpaca_key_id == "AKID123"
    assert settings.alpaca_feed == "sip"
    assert "s3cret" not in repr(settings)
    with pytest.raises(ValueError, match="JOSEPH_ALPACA_FEED"):
        config.Settings.from_env({"JOSEPH_ALPACA_FEED": "nope"})


def test_timestamps_parse_nanoseconds_and_offsets() -> None:
    """RFC-3339 with nanoseconds, no fraction or an offset parses."""
    assert alpaca.rfc3339_ms("2026-11-20T14:30:00.012345678Z") == (
        1_795_185_000_012
    )
    assert alpaca.rfc3339_ms("2026-11-20T14:30:00Z") == 1_795_185_000_000
    assert alpaca.rfc3339_ms("2026-11-20T09:30:00.5-05:00") == 1_795_185_000_500
    assert alpaca.rfc3339(1_795_185_000_012) == "2026-11-20T14:30:00.012Z"
    with pytest.raises(ValueError, match="RFC-3339"):
        alpaca.rfc3339_ms("yesterday")


@pytest.mark.parametrize(
    ("raw", "symbol"),
    [
        ("AAPL", "AAPL"),
        ("BRK.B", "BRK.B"),
        ("BRK-B", "BRK.B"),
        ("brk.a", "BRK.A"),
        ("ABR$D", None),
        ("AAC.U", None),
        ("AAC.W", None),
        ("AIIA.R", None),
        ("TOOLONG", None),
    ],
)
def test_symbols_map_to_the_id_and_alpaca_form(
    raw: str, symbol: str | None
) -> None:
    """Class shares keep a dot; unverified forms are skipped."""
    assert alpaca.alpaca_symbol(raw) == symbol


def test_bond_classification_uses_list_and_etf_names() -> None:
    """Known bond ETFs and ETFs named Bond/Treasury are BOND."""
    bond = instruments.AssetClass.BOND
    assert alpaca.bond_class("TLT", "x", etf=True) is bond
    assert alpaca.bond_class("XYZ", "Total Bond Market ETF", etf=True) is bond
    assert alpaca.bond_class("XYZ", "Treasury Wine", etf=False) is not bond
    assert alpaca.bond_class("XYZ", "Bondholders Corp", etf=True) is not bond


def test_quote_book_returns_the_quote_strictly_before() -> None:
    """Out-of-order quotes are sorted; ties belong to later trades."""
    book = alpaca.QuoteBook(capacity=2)
    book.add({"t": "2026-11-20T14:30:01Z", "bp": 10.1, "ap": 10.2})
    book.add({"t": "2026-11-20T14:30:00Z", "bp": 0, "ap": 10.15})
    assert book.prevailing(1_795_185_000_000) == (None, None)
    assert book.prevailing(1_795_185_000_500) == (None, _D("10.15"))
    assert book.prevailing(1_795_185_002_000) == (_D("10.1"), _D("10.2"))
    book.add({"t": "2026-11-20T14:30:02Z", "bp": 10.3, "ap": 10.4})
    assert book.prevailing(1_795_185_000_500) == (None, None)


def _trade(i: int, price: float, conditions: list[str]) -> dict[str, Any]:
    """Builds a one-share Alpaca trade on IEX ("V"), tape C.

    Args:
        i: Trade id, also the second of 2026-11-20T14:30 (single digit).
        price: Trade price.
        conditions: Alpaca sale-condition codes; "I" marks an odd lot.

    Returns:
        The trade in Alpaca's REST shape.
    """
    return {
        "i": i,
        "x": "V",
        "p": price,
        "s": 1,
        "c": conditions,
        "t": f"2026-11-20T14:30:0{i}Z",
        "z": "C",
    }


def test_classify_keeps_odd_lots_out_of_the_tick_reference() -> None:
    """Odd lots are classified but do not move the tick-test price."""
    book = alpaca.QuoteBook()
    classifier = classification.LeeReadyClassifier()
    rows = alpaca.classify(
        [
            _trade(1, 10.0, ["@"]),
            _trade(2, 10.5, ["@", "I"]),
            _trade(3, 10.2, ["@"]),
        ],
        book,
        classifier,
    )
    assert [r.side for r in rows] == [
        market.AggressorSide.UNKNOWN,
        market.AggressorSide.BUY,
        market.AggressorSide.BUY,
    ]
    assert rows[1].trade_id == "V:2" and rows[1].tape == "C"


def test_changed_candle_ignores_receipt_time() -> None:
    """Re-built aggregates with equal values are not re-emitted."""
    aapl = instruments.InstrumentId.parse("nasdaq:stock:AAPL")
    row = {
        "o": 1,
        "h": 2,
        "l": 1,
        "c": 2,
        "v": 5,
        "n": 1,
        "t": "2026-11-20T14:30:00Z",
    }
    first = alpaca.minute_bar(row, aapl, 1, settled_before_ms=0, source="x")
    again = alpaca.minute_bar(row, aapl, 2, settled_before_ms=0, source="x")
    moved = alpaca.minute_bar(
        {**row, "c": 1.5}, aapl, 3, settled_before_ms=0, source="x"
    )
    assert first is not None and again is not None and moved is not None
    assert alpaca.changed_candle(None, first)
    assert not alpaca.changed_candle(first, again)
    assert alpaca.changed_candle(first, moved)


def test_bars_outside_sessions_are_dropped() -> None:
    """Extended-hours minutes and non-session days produce no bars."""
    aapl = instruments.InstrumentId.parse("nasdaq:stock:AAPL")
    row = {"o": 1, "h": 1, "l": 1, "c": 1, "v": 1, "n": 1}
    assert (
        alpaca.minute_bar(
            {**row, "t": "2026-11-20T21:00:00Z"},
            aapl,
            0,
            settled_before_ms=0,
            source="x",
        )
        is None
    )
    assert (
        alpaca.daily_bar(
            {**row, "t": "2026-11-26T05:00:00Z"},
            aapl,
            0,
            delay_ms=0,
            source="x",
        )
        is None
    )


async def test_source_maps_auth_errors_to_a_key_hint() -> None:
    """HTTP 401/403 explain the key; other rejections pass through."""
    with respx.mock(assert_all_called=False) as router:
        router.get("https://data.alpaca.markets/v2/a").mock(
            return_value=httpx.Response(403)
        )
        router.get("https://data.alpaca.markets/v2/b").mock(
            return_value=httpx.Response(422)
        )
        data = http.VenueHttp(
            instruments.Venue.NASDAQ,
            alpaca.DATA_URL,
            client=httpx.AsyncClient(),
            name="alpaca",
        )
        source = alpaca.AlpacaSource(
            _KEYS, data, data, clock_lib.ManualClock(0)
        )
        with pytest.raises(
            errors.VenueUnavailableError, match="check the Alpaca keys"
        ):
            await source.get("/v2/a", {})
        with pytest.raises(http.HttpStatusError) as caught:
            await source.get("/v2/b", {})
        assert caught.value.status_code == 422
        assert "alpaca rejected" in caught.value.message
        assert router.calls[0].request.headers["APCA-API-KEY-ID"] == "kid"


class _Script:
    """Scripted stream connection.

    Attributes:
        frames: Frames or errors still to deliver, in order.
        sent: Decoded JSON frames sent by the client.
    """

    def __init__(self, frames: list[str | BaseException]) -> None:
        """Initializes the connection.

        Args:
            frames: Frames (or errors) to deliver, then idle.
        """
        self.frames = list(frames)
        self.sent: list[dict[str, Any]] = []

    async def send(self, message: str) -> None:
        """Records a frame.

        Args:
            message: JSON frame sent by the client.
        """
        self.sent.append(json.loads(message))

    async def recv(self) -> str:
        """Delivers the next frame, raises a scripted error or idles.

        Returns:
            The next scripted frame; once the script is empty it idles for an
            hour and then returns an empty string.

        Raises:
            BaseException: Scripted connection errors.
        """
        if self.frames:
            frame = self.frames.pop(0)
            if isinstance(frame, BaseException):
                raise frame
            return frame
        await asyncio.sleep(3600)
        return ""


def _connector(
    scripts: list[list[str | BaseException]], opened: list[_Script]
) -> Callable[[str], Any]:
    """Builds a connector that plays one script per connection.

    Args:
        scripts: Frames per connection; each connection pops the first.
        opened: Receives each connection.

    Returns:
        An async context manager factory taking the stream URL.
    """

    @contextlib.asynccontextmanager
    async def connect(url: str) -> AsyncIterator[_Script]:
        """Opens the next scripted connection.

        Args:
            url: Ignored.

        Yields:
            The scripted connection.

        Raises:
            OSError: When the scripts are exhausted.
        """
        del url
        if not scripts:
            raise OSError("refused")
        script = _Script(scripts.pop(0))
        opened.append(script)
        yield script

    return connect


async def _no_sleep(seconds: float) -> None:
    """Skips reconnect backoff.

    Args:
        seconds: Ignored.
    """
    del seconds


_OK = [
    '[{"T":"success","msg":"connected"}]',
    '[{"T":"success","msg":"authenticated"}]',
]


async def _next(sub: Any) -> tuple[str, Any]:
    """Waits for the next non-frame event.

    Args:
        sub: Stream subscription whose queue is read.

    Returns:
        The ``(kind, payload)`` event.

    Raises:
        TimeoutError: No event arrived within 5 seconds.
    """
    while True:
        event: tuple[str, Any] = await asyncio.wait_for(sub.queue.get(), 5)
        if event[0] != "frame":
            return event


async def test_stream_multiplexes_reconnects_and_tracks_status() -> None:
    """Subscriptions share one connection, survive a drop and track halts."""
    opened: list[_Script] = []
    scripts: list[list[str | BaseException]] = [
        [
            *_OK,
            '[{"T":"error","code":405,"msg":"symbol limit exceeded"}]',
            "not json",
            '[{"T":"s","S":"AAPL","sc":"H"}]',
            websockets.ConnectionClosedError(None, None),
        ],
        [*_OK, '[{"T":"s","S":"AAPL","sc":"T"},{"T":"x","S":"AAPL"}]'],
    ]
    stream = alpaca.AlpacaStream(
        _KEYS,
        clock_lib.ManualClock(0),
        _connector(scripts, opened),
        sleep=_no_sleep,
    )
    assert stream.url.endswith("/v2/iex")
    first = await stream.subscribe("AAPL", ["statuses", "trades"])
    assert (await _next(first))[1]["sc"] == "H"
    assert (await _next(first))[0] == "reset"
    assert (await _next(first))[1]["sc"] == "T"
    assert (await _next(first))[1]["T"] == "x"
    assert not stream.halted["AAPL"]
    second = await stream.subscribe("MSFT", ["bars"])
    assert opened[-1].sent[-1] == {"action": "subscribe", "bars": ["MSFT"]}
    await stream.unsubscribe(second)
    assert opened[-1].sent[-1] == {"action": "unsubscribe", "bars": ["MSFT"]}
    await stream.unsubscribe(first)
    assert stream._task is None
    stream._dispatch({"T": "s", "S": "MSFT", "sc": "2"})
    assert stream.halted["MSFT"]


async def test_stream_gives_up_after_repeated_failures() -> None:
    """Refused connections end in an error event."""
    opened: list[_Script] = []
    stream = alpaca.AlpacaStream(
        _KEYS,
        clock_lib.ManualClock(0),
        _connector([], opened),
        sleep=_no_sleep,
        max_failures=2,
    )
    sub = await stream.subscribe("AAPL", ["trades"])
    kind, err = await _next(sub)
    assert kind == "error" and "after 2 attempts" in err.message
    await stream.close()


async def test_stream_connection_limit_is_not_retried() -> None:
    """Error 406 ends the stream immediately."""
    opened: list[_Script] = []
    limit = '[{"T":"error","code":406,"msg":"connection limit exceeded"}]'
    stream = alpaca.AlpacaStream(
        _KEYS,
        clock_lib.ManualClock(0),
        _connector([[_OK[0], limit]], opened),
    )
    sub = await stream.subscribe("AAPL", ["trades"])
    kind, err = await _next(sub)
    assert kind == "error" and "406" in err.message
    await stream.close()


# Adapter paths over the fixture routes ------------------------------------

_SESSION = sessions.XNYS.session(datetime.date(2026, 11, 20))
assert _SESSION is not None
_US_OPEN = _SESSION.open_ms
_NOW = _US_OPEN + 7 * 60_000 + 30_000
_AAPL = instruments.InstrumentId.parse("nasdaq:stock:AAPL")
_FRAMES = json.loads(
    (
        pathlib.Path(__file__).resolve().parents[3]
        / "fixtures/venues/alpaca/ws_frames.json"
    ).read_text("utf-8")
)["frames"]


@pytest.fixture
def alpaca_api(
    exchange_routes: Callable[[respx.Router], None],
) -> Iterator[respx.Router]:
    """Serves the Alpaca and Nasdaq Trader fixtures.

    Args:
        exchange_routes: Installer of the exchange fixture routes.

    Yields:
        The respx router serving the fixtures.
    """
    with respx.mock(assert_all_called=False) as router:
        exchange_routes(router)
        yield router


def _nasdaq(
    clock: clock_lib.Clock,
    *,
    cfg: alpaca.AlpacaConfig = _KEYS,
    scripts: list[list[str | BaseException]] | None = None,
) -> alpaca.AlpacaAdapter:
    """Builds a Nasdaq adapter over the fixtures.

    Args:
        clock: Clock for the adapter.
        cfg: Alpaca configuration.
        scripts: Stream frames per connection; None plays the fixture frames
            on a single connection.

    Returns:
        The Nasdaq Alpaca adapter.
    """
    adapter = factory.build_adapters(
        [instruments.Venue.NASDAQ],
        clock,
        client=httpx.AsyncClient(),
        connect=_connector(scripts or [[*_FRAMES]], []),
        alpaca_config=cfg,
    )[instruments.Venue.NASDAQ]
    assert isinstance(adapter, alpaca.AlpacaAdapter)
    return adapter


async def test_daily_and_weekly_streams_follow_daily_bars(
    alpaca_api: respx.Router,
) -> None:
    """Seeded days update from dailyBars; weeks aggregate them."""
    del alpaca_api
    clock = clock_lib.ManualClock(_NOW)
    tracker = health.FeedHealthTracker(instruments.Venue.NASDAQ, "AAPL 1d")
    stream = _nasdaq(clock).stream_candles(
        _AAPL, timeframes.Timeframe.D1, tracker
    )
    seen = [await asyncio.wait_for(anext(stream), 5) for _ in range(2)]
    await stream.aclose()  # type: ignore[attr-defined]  # stream_candles is typed AsyncIterator; its generator has aclose()
    assert seen[0].source == "alpaca.rest" and seen[1].source == "alpaca.ws"
    assert not seen[1].is_final
    weeks = _nasdaq(clock).stream_candles(_AAPL, timeframes.Timeframe.W1)
    week = await asyncio.wait_for(anext(weeks), 5)
    await weeks.aclose()  # type: ignore[attr-defined]  # stream_candles is typed AsyncIterator; its generator has aclose()
    assert week.timeframe is timeframes.Timeframe.W1 and not week.is_final


async def test_aggregated_minute_stream_and_reconnect(
    alpaca_api: respx.Router,
) -> None:
    """5m bars aggregate streamed minutes; a reconnect re-seeds them."""
    del alpaca_api
    clock = clock_lib.ManualClock(_NOW)
    drop = websockets.ConnectionClosedError(None, None)
    adapter = _nasdaq(clock, scripts=[[*_FRAMES[:5], drop], [*_FRAMES]])
    stream = adapter.stream_candles(_AAPL, timeframes.Timeframe.M5)
    first = await asyncio.wait_for(anext(stream), 5)
    assert first.start_ms == _US_OPEN + 300_000 and not first.is_final
    second = await asyncio.wait_for(anext(stream), 5)
    await stream.aclose()  # type: ignore[attr-defined]  # stream_candles is typed AsyncIterator; its generator has aclose()
    assert second.start_ms == first.start_ms


async def test_seconds_stream_resets_and_finalizes_on_idle(
    alpaca_api: respx.Router,
) -> None:
    """A reconnect starts a new segment; idle ticks finalize bars."""
    del alpaca_api
    clock = clock_lib.ManualClock(_NOW)
    drop = websockets.ConnectionClosedError(None, None)
    adapter = _nasdaq(clock, scripts=[[*_FRAMES[:4], drop], [*_FRAMES]])
    stream = adapter.stream_candles(_AAPL, timeframes.Timeframe.S1)
    seen = [await asyncio.wait_for(anext(stream), 10)]
    while not seen[-1].is_final and len(seen) < 10:
        seen.append(await asyncio.wait_for(anext(stream), 10))
    await stream.aclose()  # type: ignore[attr-defined]  # stream_candles is typed AsyncIterator; its generator has aclose()
    assert not seen[0].is_final and seen[-1].is_final


async def test_delayed_feed_and_empty_windows(alpaca_api: respx.Router) -> None:
    """The delayed feed stops 15 minutes back and asks for delayed_sip."""
    clock = clock_lib.ManualClock(_NOW)
    cfg = alpaca.AlpacaConfig(
        key_id="k", secret_key="s", feed=alpaca.Feed.DELAYED_SIP
    )
    adapter = _nasdaq(clock, cfg=cfg)
    assert adapter.capabilities().data_delay_ms == 900_000
    m1 = timeframes.Timeframe.M1
    assert await adapter.fetch_candles(_AAPL, m1, _US_OPEN, _NOW) == []
    assert await adapter.fetch_trades(_AAPL, _US_OPEN, _NOW) == []
    assert (
        await adapter.fetch_candles(
            _AAPL, timeframes.Timeframe.S1, _US_OPEN, _NOW
        )
        == []
    )
    assert await adapter.fetch_candles(_AAPL, m1, 0, 1_000, 5) == []
    await adapter.fetch_snapshot(_AAPL)
    call = alpaca_api.calls[-1].request
    assert call.url.params["feed"] == "delayed_sip"


async def test_trades_limit_snapshot_shapes_and_ids(
    alpaca_api: respx.Router,
) -> None:
    """Trade limits apply, ids follow the directory, unknown snapshots raise."""
    clock = clock_lib.ManualClock(_NOW)
    adapter = _nasdaq(clock)
    assert str(adapter.instrument_id("QQQ")) == "nasdaq:stock:QQQ"
    trades = await adapter.fetch_trades(_AAPL, _US_OPEN - 5_000, _NOW, 2)
    assert len(trades) == 2
    assert str(adapter.instrument_id("QQQ")) == "nasdaq:etf:QQQ"
    with pytest.raises(errors.MissingDataError):
        await adapter.fetch_snapshot(
            instruments.InstrumentId.parse("nasdaq:etf:IEF")
        )
    assert alpaca.parse_directory("", "", 0) == {
        instruments.Venue.NASDAQ: [],
        instruments.Venue.NYSE: [],
        instruments.Venue.CBOE: [],
    }
    assert (
        await adapter.fetch_candles(_AAPL, timeframes.Timeframe.M1, 10, 5) == []
    )
    await adapter.close()


async def test_wrapped_snapshot_bodies_are_accepted(
    exchange_routes: Callable[[respx.Router], None],
) -> None:
    """A body nested under ``snapshots`` parses like the flat form."""
    with respx.mock(assert_all_called=False) as router:
        router.route(
            host="data.alpaca.markets", path="/v2/stocks/snapshots"
        ).mock(
            return_value=httpx.Response(
                200, json={"snapshots": {"AAPL": {"latestTrade": {"p": 1}}}}
            )
        )
        exchange_routes(router)
        adapter = _nasdaq(clock_lib.ManualClock(_NOW))
        snapshot = await adapter.fetch_snapshot(_AAPL)
    assert snapshot.last == _D(1) and snapshot.as_of_ms == _NOW


async def test_stream_retries_when_auth_never_completes() -> None:
    """A handshake without authentication is retried, then reported."""
    opened: list[_Script] = []
    silent = [_OK[0], '[{"T":"x"}]', '[{"T":"y"}]']
    stream = alpaca.AlpacaStream(
        _KEYS,
        clock_lib.ManualClock(0),
        _connector([list(silent), list(silent)], opened),
        sleep=_no_sleep,
        max_failures=2,
    )
    await stream._send_subscription("subscribe", {"bars": ["A"]})
    sub = await stream.subscribe("AAPL", ["trades"])
    kind, err = await _next(sub)
    assert kind == "error" and "after 2 attempts" in err.message
    assert len(opened) == 2
    await stream.close()


# Sparse-bar coverage -------------------------------------------------------

_M1 = timeframes.Timeframe.M1
_US_CLOSE = _SESSION.close_ms
_UNPACED = (1_000.0, 1_000)
"""Request pacing for budget tests (they count requests, not seconds)."""


def _traded(minute_ms: int) -> bool:
    """Checks whether a session minute had trades in the quiet API.

    Args:
        minute_ms: Minute start, epoch milliseconds.

    Returns:
        False for every third minute of the session, True otherwise.
    """
    return (minute_ms - _US_OPEN) // 60_000 % 3 != 2


def _rest_bar(ts_ms: int) -> dict[str, Any]:
    """Builds an Alpaca REST bar with unit prices and 100 shares.

    Args:
        ts_ms: Bar time, epoch milliseconds.

    Returns:
        The bar in Alpaca's REST shape.
    """
    return {
        "t": alpaca.rfc3339(ts_ms),
        "o": 1,
        "h": 1,
        "l": 1,
        "c": 1,
        "v": 100,
        "n": 1,
        "vw": 1,
    }


class _QuietApi:
    """Alpaca data API for AAPL: every third minute and Wednesday are quiet.

    Attributes:
        clock: Clock (stamps each request).
        page: Bars per page.
        bars: ``(request time, params)`` of every bar request.
        trades: Trades served by ``/trades`` (Alpaca shape).
    """

    def __init__(
        self, clock: clock_lib.ManualClock, page: int = 10_000
    ) -> None:
        """Initializes the API.

        Args:
            clock: Clock.
            page: Bars per page.
        """
        self.clock = clock
        self.page = page
        self.bars: list[tuple[int, dict[str, str]]] = []
        self.trades: list[dict[str, Any]] = []

    def _minutes(self, start: int, last: int) -> list[dict[str, Any]]:
        """Lists the session minutes with trades.

        Args:
            start: Inclusive start, epoch milliseconds.
            last: Inclusive end, epoch milliseconds.

        Returns:
            One bar per traded session minute in the window.
        """
        rows = []
        for day in sessions.XNYS.sessions(start, last + 1):
            minute = max(day.open_ms, -(-start // 60_000) * 60_000)
            while minute < min(day.close_ms, last + 1):
                if _traded(minute):
                    rows.append(_rest_bar(minute))
                minute += 60_000
        return rows

    def _days(self, start: str, last: int) -> list[dict[str, Any]]:
        """Lists daily bars (Wednesday 2026-11-18 had no trades).

        Args:
            start: First date (ISO).
            last: Inclusive end, epoch milliseconds.

        Returns:
            Bars stamped at New York midnight.
        """
        rows = []
        day = datetime.date.fromisoformat(start)
        while (
            midnight := int(
                datetime.datetime.combine(
                    day, datetime.time(0), tzinfo=alpaca._NY
                ).timestamp()
                * 1000
            )
        ) <= last:
            if sessions.XNYS.session(day) and day != datetime.date(
                2026, 11, 18
            ):
                rows.append(_rest_bar(midnight))
            day += datetime.timedelta(days=1)
        return rows

    def __call__(self, request: httpx.Request) -> httpx.Response:
        """Serves bars, trades and (always empty) quotes.

        Bar requests are recorded in ``bars`` and paged by ``page``.

        Args:
            request: Request to one of the AAPL data endpoints.

        Returns:
            The JSON page for the request.
        """
        params = dict(request.url.params)
        last = alpaca.rfc3339_ms(params["end"])
        kind = request.url.path.rsplit("/", 1)[-1]
        if kind != "bars":
            first = alpaca.rfc3339_ms(params["start"])
            rows = [
                t
                for t in self.trades
                if first <= alpaca.rfc3339_ms(t["t"]) <= last
            ]
            return httpx.Response(
                200,
                json={
                    kind: rows if kind == "trades" else [],
                    "next_page_token": None,
                },
            )
        self.bars.append((self.clock.now_ms(), params))
        if params["timeframe"] == "1Day":
            rows = self._days(params["start"], last)
        else:
            rows = self._minutes(alpaca.rfc3339_ms(params["start"]), last)
        offset = int(params.get("page_token") or 0)
        more = offset + self.page < len(rows)
        return httpx.Response(
            200,
            json={
                "bars": rows[offset : offset + self.page],
                "next_page_token": str(offset + self.page) if more else None,
                "symbol": "AAPL",
            },
        )


@pytest.fixture
def quiet_api(
    exchange_routes: Callable[[respx.Router], None],
) -> Iterator[Callable[[clock_lib.ManualClock], _QuietApi]]:
    """Serves a quiet AAPL over the Alpaca routes.

    Args:
        exchange_routes: Installer of the exchange fixture routes.

    Yields:
        Factory binding the API to a clock.
    """
    with respx.mock(assert_all_called=False) as router:
        apis: list[_QuietApi] = []

        def handler(request: httpx.Request) -> httpx.Response:
            """Delegates to the newest API.

            Args:
                request: Request to an AAPL data endpoint.

            Returns:
                The newest API's response.
            """
            return apis[-1](request)

        router.route(
            host="data.alpaca.markets", path__startswith="/v2/stocks/AAPL/"
        ).mock(side_effect=handler)
        exchange_routes(router)

        def make(clock: clock_lib.ManualClock) -> _QuietApi:
            """Creates the API for a clock; it serves all later requests.

            Args:
                clock: Clock that stamps the API's requests.

            Returns:
                The new API.
            """
            apis.append(_QuietApi(clock))
            return apis[-1]

        yield make


def _market_data(
    adapter: alpaca.AlpacaAdapter, clock: clock_lib.Clock
) -> service_lib.MarketDataService:
    """Builds the market-data service over one Alpaca adapter.

    Args:
        adapter: Nasdaq adapter.
        clock: Clock shared by the router and the service.

    Returns:
        The service with a fresh candle store.
    """
    adapters = {instruments.Venue.NASDAQ: adapter}
    store = candle_store.CandleStore()
    return service_lib.MarketDataService(
        adapters,
        registry.InstrumentRegistry(adapters),
        store,
        feed_router.FeedRouter(adapters, store, clock),
        clock,
    )


async def test_quiet_minutes_cost_no_repeat_requests(
    quiet_api: Callable[[clock_lib.ManualClock], _QuietApi],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A burst of queries makes one request; without coverage it storms."""
    monkeypatch.setattr(factory, "_ALPACA_RATE", _UNPACED)
    clock = clock_lib.ManualClock(_US_CLOSE + 5 * 60_000)
    api = quiet_api(clock)
    svc = _market_data(_nasdaq(clock), clock)
    for _ in range(50):
        batch = await svc.get_batch(
            _AAPL, _M1, from_ms=_US_OPEN, to_ms=_US_CLOSE
        )
        assert batch.complete and len(batch.candles) == 260
        await svc.get_candles(_AAPL, _M1, _US_OPEN, _US_CLOSE)
    assert len(api.bars) == 1
    # With dense-bar handling (SPARSE_BARS off) the same burst re-requests
    # from the first quiet minute on every call.
    monkeypatch.setattr(alpaca.AlpacaAdapter, "SPARSE_BARS", False)
    dense_api = quiet_api(clock)
    dense = _market_data(_nasdaq(clock), clock)
    for _ in range(50):
        await dense.get_batch(_AAPL, _M1, from_ms=_US_OPEN, to_ms=_US_CLOSE)
    assert len(dense_api.bars) >= 100
    quiet = alpaca.rfc3339(_US_OPEN + 2 * 60_000)
    assert sum(p["start"] == quiet for _, p in dense_api.bars) >= 49


async def test_live_repairs_ask_once_and_only_for_unsettled_minutes(
    quiet_api: Callable[[clock_lib.ManualClock], _QuietApi],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """During the session each call asks at most once, for the tail only."""
    monkeypatch.setattr(factory, "_ALPACA_RATE", _UNPACED)
    clock = clock_lib.ManualClock(_US_OPEN + 60 * 60_000)
    api = quiet_api(clock)
    svc = _market_data(_nasdaq(clock), clock)
    calls = 40
    for _ in range(calls):
        clock.current_ms += 15_000
        closed = clock.current_ms - clock.current_ms % 60_000
        rows = await svc.get_candles(_AAPL, _M1, _US_OPEN, closed)
        assert rows and rows[-1].end_ms <= closed
    assert len(api.bars) <= calls
    first, *later = api.bars
    assert alpaca.rfc3339_ms(first[1]["start"]) == _US_OPEN
    for asked_at, params in later:
        assert alpaca.rfc3339_ms(params["start"]) >= asked_at - 3 * 60_000


async def test_fetch_coverage_spans_what_alpaca_settled(
    quiet_api: Callable[[clock_lib.ManualClock], _QuietApi],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Minute, aggregated and daily fetches cover up to the settled time."""
    clock = clock_lib.ManualClock(_US_OPEN + 10 * 60_000 + 30_000)
    api = quiet_api(clock)
    adapter = _nasdaq(clock)
    assert adapter.SPARSE_BARS
    end = _US_OPEN + 10 * 60_000
    ones = await adapter.fetch_candle_coverage(_AAPL, _M1, _US_OPEN, end)
    settled = clock.now_ms() - 60_000
    assert ones.covered == ((_US_OPEN, settled),) and ones.complete
    assert all(c.is_final for c in ones.candles if c.end_ms <= settled)
    fives = await adapter.fetch_candle_coverage(
        _AAPL, timeframes.Timeframe.M5, _US_OPEN, end
    )
    assert fives.covered == ((_US_OPEN, settled),)
    assert (
        await adapter.fetch_candle_coverage(_AAPL, _M1, end, end)
    ).candles == ()
    # A page cap ends the coverage at the last bar read.
    monkeypatch.setattr(alpaca, "MAX_PAGES", 1)
    api.page = 4
    capped = await adapter.fetch_candle_coverage(_AAPL, _M1, _US_OPEN, end)
    assert not capped.complete and len(capped.candles) == 4
    assert capped.covered == ((_US_OPEN, capped.candles[-1].start_ms),)
    monkeypatch.setattr(alpaca, "MAX_PAGES", 20)
    # Days are covered up to New York midnight; Wednesday had no trades.
    clock.current_ms = _US_OPEN + 3_600_000
    monday = sessions.XNYS.session(datetime.date(2026, 11, 16))
    assert monday is not None
    days = await adapter.fetch_candle_coverage(
        _AAPL, timeframes.Timeframe.D1, monday.open_ms, _US_CLOSE
    )
    midnight = int(
        datetime.datetime(2026, 11, 20, tzinfo=alpaca._NY).timestamp() * 1000
    )
    assert days.covered == ((monday.open_ms, midnight),)
    assert len(days.candles) == 4 and not days.candles[-1].is_final
    store = candle_store.CandleStore()
    store.upsert_many(list(days.candles))
    store.add_coverage(_AAPL, timeframes.Timeframe.D1, days.covered)
    assert (
        store.final_gaps(
            _AAPL, timeframes.Timeframe.D1, monday.open_ms, midnight
        )
        == []
    )


async def test_seconds_history_reads_the_closing_print(
    quiet_api: Callable[[clock_lib.ManualClock], _QuietApi],
) -> None:
    """A window ending at the close reads on until the print has settled."""
    clock = clock_lib.ManualClock(_US_CLOSE + 120_000)
    api = quiet_api(clock)

    def trade(i: int, ts_ms: int, price: float, conditions: list[str]) -> None:
        """Adds a Nasdaq ("Q") trade of ``100 * i`` shares to the API.

        Args:
            i: Trade id.
            ts_ms: Trade time, epoch milliseconds.
            price: Trade price.
            conditions: Alpaca sale-condition codes.
        """
        api.trades.append(
            {
                "i": i,
                "x": "Q",
                "p": price,
                "s": 100 * i,
                "c": conditions,
                "t": alpaca.rfc3339(ts_ms),
                "z": "C",
            }
        )

    trade(1, _US_CLOSE - 1_500, 230.0, ["@"])
    trade(2, _US_CLOSE - 400, 230.1, ["@"])
    trade(3, _US_CLOSE + 350, 230.4, ["@", "6"])
    trade(4, _US_CLOSE + 4_000, 231.0, ["@", "T"])
    adapter = _nasdaq(clock)
    s1 = timeframes.Timeframe.S1
    window = (_US_CLOSE - 2_000, _US_CLOSE)
    done = await adapter.fetch_candle_coverage(_AAPL, s1, *window)
    last = done.candles[-1]
    assert last.start_ms == _US_CLOSE - 1_000 and last.is_final
    assert last.close == _D("230.4") and last.base_volume == _D(500)
    assert done.covered == (window,)
    early = clock_lib.ManualClock(_US_CLOSE + 20_000)
    pending = await _nasdaq(early).fetch_candle_coverage(_AAPL, s1, *window)
    assert pending.candles[-1].close == _D("230.4")
    assert not pending.candles[-1].is_final
    assert pending.covered == ((window[0], _US_CLOSE - 1_000),)


def _bar_frame(minute: int) -> str:
    """Builds a stream frame holding one AAPL minute bar (``b`` message).

    Args:
        minute: Minutes after the 2026-11-20 open.

    Returns:
        The JSON frame.
    """
    ts = alpaca.rfc3339(_US_OPEN + minute * 60_000)
    return json.dumps(
        [
            {
                "T": "b",
                "S": "AAPL",
                "t": ts,
                "o": 1,
                "h": 1,
                "l": 1,
                "c": 1,
                "v": 10,
                "n": 1,
                "vw": 1,
            }
        ]
    )


async def test_minute_stream_proves_quiet_minutes_between_bars(
    alpaca_api: respx.Router,
) -> None:
    """Consecutive ``b`` messages cover the minutes between; a drop resets."""
    del alpaca_api
    drop = websockets.ConnectionClosedError(None, None)
    adapter = _nasdaq(
        clock_lib.ManualClock(_NOW),
        scripts=[
            [*_OK, _bar_frame(0), _bar_frame(1), _bar_frame(3), drop],
            [*_OK, _bar_frame(9), _bar_frame(10)],
        ],
    )
    stream = adapter.stream_candle_coverage(_AAPL, _M1)
    spans: list[tuple[int, int]] = []
    starts: list[int] = []
    while not starts or starts[-1] != _US_OPEN + 600_000:
        update = await asyncio.wait_for(anext(stream), 5)
        spans.extend(update.covered)
        starts.extend(c.start_ms for c in update.candles)
    await stream.aclose()  # type: ignore[attr-defined]  # stream_candle_coverage is typed AsyncIterator; its generator has aclose()
    minute = 60_000
    assert spans == [
        (_US_OPEN + minute, _US_OPEN + 2 * minute),
        (_US_OPEN + 2 * minute, _US_OPEN + 4 * minute),
        (_US_OPEN + 10 * minute, _US_OPEN + 11 * minute),
    ]


async def test_seconds_stream_covers_only_what_frames_prove(
    alpaca_api: respx.Router,
) -> None:
    """Seconds coverage starts after the partial bar and follows frames."""
    del alpaca_api
    clock = clock_lib.ManualClock(_US_OPEN + 10_000)
    trades = json.dumps(
        [
            {
                "T": "t",
                "S": "AAPL",
                "c": ["@"],
                "i": 1,
                "p": 230.1,
                "s": 100,
                "t": alpaca.rfc3339(_US_OPEN + 500),
                "x": "V",
                "z": "C",
            },
            {
                "T": "t",
                "S": "AAPL",
                "c": ["@"],
                "i": 2,
                "p": 230.2,
                "s": 100,
                "t": alpaca.rfc3339(_US_OPEN + 4_200),
                "x": "V",
                "z": "C",
            },
            {
                "T": "t",
                "S": "AAPL",
                "c": ["@"],
                "i": 3,
                "p": 230.3,
                "s": 100,
                "t": alpaca.rfc3339(_US_OPEN + 9_800),
                "x": "V",
                "z": "C",
            },
        ]
    )
    adapter = _nasdaq(clock, scripts=[[*_OK, trades]])
    stream = adapter.stream_candle_coverage(_AAPL, timeframes.Timeframe.S1)
    update = await asyncio.wait_for(anext(stream), 5)
    while not update.covered:
        update = await asyncio.wait_for(anext(stream), 5)
    assert update.covered == ((_US_OPEN + 1_000, _US_OPEN + 8_500),)
    # Idle ticks finalize bars but prove nothing new.
    clock.current_ms += 30_000
    ticked = await asyncio.wait_for(anext(stream), 5)
    await stream.aclose()  # type: ignore[attr-defined]  # stream_candle_coverage is typed AsyncIterator; its generator has aclose()
    assert ticked.candles and ticked.covered == ()
