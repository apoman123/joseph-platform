"""Unit tests for Alpaca configuration, parsing, classification and stream."""

from __future__ import annotations

import asyncio
import contextlib
import decimal
import json
from collections.abc import AsyncIterator
from collections.abc import Callable
from typing import Any

import httpx
import pytest
import respx
import websockets

from joseph.domain import clock as clock_lib
from joseph.domain import errors
from joseph.domain import instruments
from joseph.domain import market
from joseph.market_data.adapters import alpaca
from joseph.market_data.adapters import http
from joseph.market_data.domain import classification
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


def test_classify_keeps_odd_lots_out_of_the_tick_reference() -> None:
    """Odd lots are classified but do not move the tick-test price."""
    book = alpaca.QuoteBook()
    classifier = classification.LeeReadyClassifier()

    def trade(i: int, price: float, conditions: list[str]) -> dict[str, Any]:
        """Builds an Alpaca trade.

        Args:
            i: Trade id.
            price: Price.
            conditions: Conditions.

        Returns:
            Trade.
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

    rows = alpaca.classify(
        [
            trade(1, 10.0, ["@"]),
            trade(2, 10.5, ["@", "I"]),
            trade(3, 10.2, ["@"]),
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
        sent: Frames sent by the client.
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
            message: Frame.
        """
        self.sent.append(json.loads(message))

    async def recv(self) -> str:
        """Delivers the next frame, raises a scripted error or idles.

        Returns:
            Frame.

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
        scripts: Frames per connection.
        opened: Receives each connection.

    Returns:
        Connector.
    """

    @contextlib.asynccontextmanager
    async def connect(url: str) -> AsyncIterator[_Script]:
        """Opens the next scripted connection.

        Args:
            url: Ignored.

        Yields:
            Connection.

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


_OK = [
    '[{"T":"success","msg":"connected"}]',
    '[{"T":"success","msg":"authenticated"}]',
]


async def _next(sub: Any) -> tuple[str, Any]:
    """Waits for the next non-frame event.

    Args:
        sub: Subscription.

    Returns:
        Event.
    """
    while True:
        event = await asyncio.wait_for(sub.queue.get(), 5)
        if event[0] != "frame":
            return event  # type: ignore[no-any-return]


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

    async def no_sleep(seconds: float) -> None:
        """Skips backoff.

        Args:
            seconds: Ignored.
        """
        del seconds

    stream = alpaca.AlpacaStream(
        _KEYS,
        clock_lib.ManualClock(0),
        _connector(scripts, opened),
        sleep=no_sleep,
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

    async def no_sleep(seconds: float) -> None:
        """Skips backoff.

        Args:
            seconds: Ignored.
        """
        del seconds

    stream = alpaca.AlpacaStream(
        _KEYS,
        clock_lib.ManualClock(0),
        _connector([], opened),
        sleep=no_sleep,
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
