# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Unit tests: the official closing print in Alpaca minute and hour bars.

Alpaca stamps the closing cross after the close, in its minute after the
session together with post-market trades. The adapter reads each session's
closing window once from the historical trades endpoint and folds the
condition-6 prints into the session's last minute bar. The responses are
the hand-written fixtures of ``fixtures/venues/alpaca`` (``close_*`` and
``early_close_*``), shaped like Alpaca's documented responses, served with
Alpaca's inclusive ``start``/``end`` filtering.
"""

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
from typing import TypeAlias

import httpx
import pytest
import respx

from joseph.domain import clock as clock_lib
from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import quality
from joseph.domain import sessions
from joseph.domain import timeframes
from joseph.market_data.adapters import alpaca
from joseph.market_data.adapters import factory
from joseph.market_data.app import candle_store
from joseph.market_data.app import feed_router
from joseph.market_data.app import registry
from joseph.market_data.app import service as service_lib
from joseph.market_data.domain import candles as candle_rules

Validate: TypeAlias = Callable[[str, str, object], None]
_D = decimal.Decimal
_TF = timeframes.Timeframe
_FIXTURES = (
    pathlib.Path(__file__).resolve().parents[3] / "fixtures/venues/alpaca"
)
_SIP = alpaca.AlpacaConfig(key_id="kid", secret_key="sek", feed=alpaca.Feed.SIP)
_AAPL = instruments.InstrumentId.parse("nasdaq:stock:AAPL")
_GOVT = instruments.InstrumentId.parse("cboe:etf:GOVT")


def _trading_session(
    calendar: sessions.ExchangeCalendar, day: datetime.date
) -> sessions.Session:
    """Returns the session on a day the calendar trades.

    Args:
        calendar: Exchange calendar.
        day: Local trading day.

    Returns:
        The session; the day must not be a closure.
    """
    session = calendar.session(day)
    assert session is not None
    return session


_SESSION = _trading_session(sessions.XNYS, datetime.date(2026, 11, 20))
_EARLY = _trading_session(sessions.XNYS, datetime.date(2025, 11, 28))
_OPEN = _SESSION.open_ms
_CLOSE = _SESSION.close_ms
_MINUTE = 60_000
_CROSS = _D("231.1")
"""Price of the Nasdaq closing cross in ``close_trades.json``."""
_PRINTED = _D(2_401_235)
"""Volume of the folded prints (cross, odd lot, another market center)."""
_UNPACED = (1_000.0, 1_000)
"""Request pacing for budget tests (they count requests, not seconds)."""


def _load(name: str) -> dict[str, Any]:
    """Reads an Alpaca fixture.

    Args:
        name: File name without extension.

    Returns:
        The parsed fixture object.
    """
    payload: dict[str, Any] = json.loads(
        (_FIXTURES / f"{name}.json").read_text("utf-8")
    )
    return payload


def _instant(text: str) -> int:
    """Parses an Alpaca ``start``/``end`` parameter.

    Args:
        text: RFC-3339 time or ``YYYY-MM-DD`` (New York midnight).

    Returns:
        UTC epoch milliseconds.
    """
    if len(text) == 10:
        midnight = datetime.datetime.combine(
            datetime.date.fromisoformat(text),
            datetime.time(0),
            tzinfo=alpaca._NY,
        )
        return int(midnight.timestamp() * 1000)
    return alpaca.rfc3339_ms(text)


class _Api:
    """Alpaca bars and trades endpoints over fixture rows.

    Attributes:
        minutes: 1Min bars per symbol.
        days: 1Day bars per symbol.
        trades: Trades per symbol.
        requests: ``(kind, symbol, params)`` of every request.
    """

    def __init__(self) -> None:
        """Initializes empty endpoints."""
        self.minutes: dict[str, list[dict[str, Any]]] = {}
        self.days: dict[str, list[dict[str, Any]]] = {}
        self.trades: dict[str, list[dict[str, Any]]] = {}
        self.requests: list[tuple[str, str, dict[str, str]]] = []

    def closing_requests(self) -> list[dict[str, str]]:
        """Returns the parameters of every trades request.

        Returns:
            Query parameters, in request order.
        """
        return [p for kind, _, p in self.requests if kind == "trades"]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        """Serves rows inside the inclusive ``[start, end]`` window.

        Each request is recorded in ``requests``.

        Args:
            request: Request to a bars, trades or quotes endpoint.

        Returns:
            Response shaped like Alpaca's (one page).
        """
        symbol, kind = request.url.path.split("/")[3:5]
        params = dict(request.url.params)
        self.requests.append((kind, symbol, params))
        if kind == "bars":
            source = (
                self.days if params["timeframe"] == "1Day" else self.minutes
            )
        else:
            source = self.trades if kind == "trades" else {}
        first, last = _instant(params["start"]), _instant(params["end"])
        rows = [
            row
            for row in source.get(symbol, [])
            if first <= alpaca.rfc3339_ms(row["t"]) <= last
        ]
        return httpx.Response(
            200,
            json={kind: rows, "next_page_token": None, "symbol": symbol},
        )


@pytest.fixture
def api(
    exchange_routes: Callable[[respx.Router], None],
) -> Iterator[_Api]:
    """Serves the closing-print fixtures and the symbol directory.

    Args:
        exchange_routes: Installer of the exchange fixture routes.

    Yields:
        The Alpaca endpoints (AAPL and GOVT loaded).
    """
    endpoints = _Api()
    endpoints.minutes["AAPL"] = _load("close_bars_1min")["bars"]
    endpoints.days["AAPL"] = _load("close_bars_1day")["bars"]
    endpoints.trades["AAPL"] = _load("close_trades")["trades"]
    endpoints.minutes["GOVT"] = _load("early_close_bars_1min")["bars"]
    endpoints.trades["GOVT"] = _load("early_close_trades")["trades"]
    with respx.mock(assert_all_called=False) as router:
        router.route(
            host="data.alpaca.markets", path__startswith="/v2/stocks/"
        ).mock(side_effect=endpoints)
        exchange_routes(router)
        yield endpoints


class _Gate:
    """Stream connection delivering the frames a test releases.

    Attributes:
        frames: Frames to deliver.
    """

    def __init__(self) -> None:
        """Initializes an idle connection."""
        self.frames: asyncio.Queue[str] = asyncio.Queue()

    async def send(self, message: str) -> None:
        """Ignores client frames.

        Args:
            message: Client frame.
        """
        del message

    async def recv(self) -> str:
        """Waits for the next released frame.

        Returns:
            The next frame put on ``frames``.
        """
        return await self.frames.get()


def _adapter(
    venue: instruments.Venue,
    clock: clock_lib.Clock,
    *,
    cfg: alpaca.AlpacaConfig = _SIP,
    gate: _Gate | None = None,
) -> alpaca.AlpacaAdapter:
    """Builds an Alpaca adapter over the fixture routes.

    Args:
        venue: ``nasdaq`` or ``cboe``.
        clock: Clock for the adapter.
        cfg: Alpaca configuration.
        gate: Stream connection to open; None opens a new idle one each time.

    Returns:
        The venue's Alpaca adapter.
    """

    @contextlib.asynccontextmanager
    async def connect(url: str) -> AsyncIterator[_Gate]:
        """Opens the gated connection.

        Args:
            url: Ignored.

        Yields:
            The gated connection.
        """
        del url
        yield gate or _Gate()

    adapter = factory.build_adapters(
        [venue],
        clock,
        crypto_source="native",
        client=httpx.AsyncClient(),
        connect=connect,
        alpaca_config=cfg,
    )[venue]
    assert isinstance(adapter, alpaca.AlpacaAdapter)
    return adapter


def _bar_frame(row: dict[str, Any]) -> str:
    """Wraps an Alpaca bar as a stream ``b`` message.

    Args:
        row: REST bar object.

    Returns:
        The JSON frame.
    """
    return json.dumps([{"T": "b", "S": "AAPL", **row}])


def _row(rows: list[dict[str, Any]], ts_ms: int) -> dict[str, Any]:
    """Finds the fixture bar stamped at an instant.

    Args:
        rows: Alpaca bars.
        ts_ms: Bar time, epoch milliseconds.

    Returns:
        The first bar stamped at ``ts_ms``.
    """
    return next(r for r in rows if alpaca.rfc3339_ms(r["t"]) == ts_ms)


async def test_last_minute_gains_the_closing_print(
    api: _Api, contract_validate: Validate
) -> None:
    """The 15:59 bar closes at the cross; post-market trades stay out."""
    clock = clock_lib.ManualClock(_CLOSE + 30 * _MINUTE)
    adapter = _adapter(instruments.Venue.NASDAQ, clock)
    window = (_CLOSE - 3 * _MINUTE, _CLOSE)
    fetched = await adapter.fetch_candle_coverage(_AAPL, _TF.M1, *window)
    assert [c.start_ms for c in fetched.candles] == [
        _CLOSE - 3 * _MINUTE,
        _CLOSE - 2 * _MINUTE,
        _CLOSE - _MINUTE,
    ]
    last = fetched.candles[-1]
    raw = _row(api.minutes["AAPL"], _CLOSE - _MINUTE)
    # Alpaca's own 15:59 bar closed at 231.02; the cross prints 231.10.
    assert _D(str(raw["c"])) == _D("231.02")
    assert (last.open, last.high, last.low, last.close) == (
        _D("231.08"),
        _D("231.15"),
        _D("230.98"),
        _CROSS,
    )
    # Folded: the cross, another closing print and an odd-lot closing
    # print (volume only). Not folded: Form T trades (up to 231.30) and
    # the canceled print; Alpaca's 16:00 bar (post-market) is dropped.
    assert last.base_volume == _D(301_200) + _PRINTED
    assert last.trade_count == 3_420 + 3
    assert last.is_final and last.end_ms == _CLOSE
    assert fetched.covered == (window,)
    for candle in fetched.candles:
        contract_validate("market.schema.json", "Candle", candle.to_contract())
    (params,) = api.closing_requests()
    assert params["start"] == "2026-11-20T21:00:00.000Z"
    assert params["end"] == "2026-11-20T21:00:59.999Z"
    assert params["feed"] == "sip" and params["sort"] == "asc"
    assert params["limit"] == "10000"
    # The hour over the close aggregates the folded minute (cached print).
    hours = await adapter.fetch_candle_coverage(
        _AAPL, _TF.H1, _CLOSE - 30 * _MINUTE, _CLOSE
    )
    (hour,) = hours.candles
    assert hour.start_ms == _CLOSE - 30 * _MINUTE and hour.is_final
    assert hour.close == _CROSS and hour.high == _D("231.15")
    assert hour.base_volume == _D(120_500 + 152_300 + 168_400 + 301_200) + (
        _PRINTED
    )
    assert len(api.closing_requests()) == 1


async def test_hour_four_hour_and_day_closes_agree(api: _Api) -> None:
    """1m, 5m, 1h, 4h and 1d bars of the session all close at the cross."""
    clock = clock_lib.ManualClock(_CLOSE + 9 * 3_600_000)
    adapter = _adapter(instruments.Venue.NASDAQ, clock)
    closes = {}
    for timeframe in (_TF.M1, _TF.M5, _TF.H1, _TF.H4, _TF.D1):
        rows = await adapter.fetch_candles(_AAPL, timeframe, _OPEN, _CLOSE)
        assert rows[-1].is_final, timeframe
        assert rows[-1].end_ms == _CLOSE, timeframe
        closes[timeframe] = rows[-1].close
    assert set(closes.values()) == {_CROSS}
    day = (await adapter.fetch_candles(_AAPL, _TF.D1, _OPEN, _CLOSE))[-1]
    four = await adapter.fetch_candles(_AAPL, _TF.H4, _OPEN, _CLOSE)
    assert [b.start_ms for b in four] == [_OPEN + 4 * 3_600_000]
    assert day.high >= four[-1].high and day.low <= four[-1].low
    assert len(api.closing_requests()) == 1


async def test_early_close_folds_into_the_1259_bar(api: _Api) -> None:
    """On a 13:00 close the window is 13:00-13:01 (Cboe BZX listing)."""
    close = _EARLY.close_ms
    assert datetime.datetime.fromtimestamp(
        close / 1000, alpaca._NY
    ).time() == datetime.time(13, 0)
    clock = clock_lib.ManualClock(close + 30 * _MINUTE)
    adapter = _adapter(instruments.Venue.CBOE, clock)
    rows = await adapter.fetch_candles(
        _GOVT, _TF.M1, close - 2 * _MINUTE, close
    )
    assert [r.start_ms for r in rows] == [close - 2 * _MINUTE, close - _MINUTE]
    last = rows[-1]
    assert last.close == _D("23.05") and last.high == _D("23.06")
    assert last.base_volume == _D(88_900 + 608_000)
    assert last.is_final
    (params,) = api.closing_requests()
    assert params["start"] == "2025-11-28T18:00:00.000Z"
    assert params["end"] == "2025-11-28T18:00:59.999Z"
    assert [s for kind, s, _ in api.requests if kind == "trades"] == ["GOVT"]
    hours = await adapter.fetch_candles(
        _GOVT, _TF.H1, close - 30 * _MINUTE, close
    )
    assert [(h.start_ms, h.end_ms) for h in hours] == [
        (close - 30 * _MINUTE, close)
    ]
    assert hours[0].close == _D("23.05")


async def test_pending_prints_keep_the_last_minute_open(api: _Api) -> None:
    """Until its window has passed the last minute is open and uncovered."""
    clock = clock_lib.ManualClock(_CLOSE + 30_000)
    adapter = _adapter(instruments.Venue.NASDAQ, clock)
    start = _CLOSE - 3 * _MINUTE
    early = await adapter.fetch_candle_coverage(_AAPL, _TF.M1, start, _CLOSE)
    assert not early.candles[-1].is_final
    assert early.candles[-1].close == _D("231.02")
    assert early.covered == ((start, _CLOSE - _MINUTE),)
    # Settled by the usual minute rule, but the window has not passed.
    clock.current_ms = _CLOSE + 60_500
    waiting = await adapter.fetch_candle_coverage(_AAPL, _TF.M1, start, _CLOSE)
    assert not waiting.candles[-1].is_final
    assert waiting.covered == ((start, _CLOSE - _MINUTE),)
    hour = await adapter.fetch_candle_coverage(
        _AAPL, _TF.H1, _CLOSE - 30 * _MINUTE, _CLOSE
    )
    assert not hour.candles[-1].is_final
    assert api.closing_requests() == []
    clock.current_ms = _CLOSE + 61_000
    done = await adapter.fetch_candle_coverage(_AAPL, _TF.M1, start, _CLOSE)
    assert done.candles[-1].is_final and done.candles[-1].close == _CROSS
    assert done.covered == ((start, _CLOSE),)
    assert len(api.closing_requests()) == 1


async def test_iex_feed_reads_no_closing_window(api: _Api) -> None:
    """IEX runs no closing cross for these listings: nothing is read."""
    clock = clock_lib.ManualClock(_CLOSE + 30 * _MINUTE)
    cfg = alpaca.AlpacaConfig(key_id="kid", secret_key="sek")
    adapter = _adapter(instruments.Venue.NASDAQ, clock, cfg=cfg)
    window = (_CLOSE - 3 * _MINUTE, _CLOSE)
    fetched = await adapter.fetch_candle_coverage(_AAPL, _TF.M1, *window)
    assert fetched.candles[-1].close == _D("231.02")
    assert fetched.candles[-1].is_final and fetched.covered == (window,)
    assert api.closing_requests() == []
    notes = " ".join(adapter.capabilities().notes)
    assert "IEX feed has no closing cross" in notes


def _sessions_before(
    last: sessions.Session, count: int
) -> list[sessions.Session]:
    """Lists ``count`` XNYS sessions ending with ``last``.

    Args:
        last: Newest session.
        count: Number of sessions to list.

    Returns:
        The sessions, oldest first.
    """
    out = [last]
    while len(out) < count:
        out.insert(0, sessions.XNYS.previous_session(out[0]))
    return out


def _serve_sessions(api: _Api, days: list[sessions.Session]) -> None:
    """Gives AAPL a last minute, a post-close minute and a cross per day.

    The last minute of day ``i`` closes at ``100 + i`` and its cross prints
    at ``100.5 + i``; the post-close minute and a Form T trade are at
    ``200 + i``.

    Args:
        api: Endpoints whose AAPL minutes and trades are replaced.
        days: Sessions to serve, oldest first.
    """
    api.minutes["AAPL"] = []
    api.trades["AAPL"] = []
    for i, day in enumerate(days):
        for start, price in (
            (day.close_ms - _MINUTE, 100 + i),
            (day.close_ms, 200 + i),
        ):
            api.minutes["AAPL"].append(
                {
                    "t": alpaca.rfc3339(start),
                    "o": price,
                    "h": price,
                    "l": price,
                    "c": price,
                    "v": 100,
                    "n": 1,
                    "vw": price,
                }
            )
        api.trades["AAPL"] += [
            {
                "c": ["@", "6"],
                "i": i,
                "p": 100.5 + i,
                "s": 1_000,
                "t": alpaca.rfc3339(day.close_ms + 3),
                "x": "Q",
                "z": "C",
            },
            {
                "c": ["@", "T"],
                "i": 10_000 + i,
                "p": 200 + i,
                "s": 10,
                "t": alpaca.rfc3339(day.close_ms + 2_000),
                "x": "D",
                "z": "C",
            },
        ]


def _hole(day: sessions.Session) -> candle_rules.Span:
    """Returns the last minute of a session.

    Args:
        day: Session.

    Returns:
        The ``(start, end)`` span of the session's last minute.
    """
    return (day.close_ms - _MINUTE, day.close_ms)


async def test_closing_requests_stay_within_the_budget(
    api: _Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    """20 sessions per fetch, newest first, 60 per minute, each read once."""
    monkeypatch.setattr(factory, "_ALPACA_RATE", _UNPACED)
    assert alpaca.CLOSING_REQUESTS_PER_FETCH == 20
    assert alpaca.CLOSING_REQUESTS_PER_MINUTE == 60
    days = _sessions_before(_SESSION, 30)
    _serve_sessions(api, days)
    clock = clock_lib.ManualClock(_CLOSE + 9 * 3_600_000)
    adapter = _adapter(instruments.Venue.NASDAQ, clock)
    span = (days[0].open_ms, _CLOSE)
    first = await adapter.fetch_candle_coverage(_AAPL, _TF.H1, *span)
    asked = [p["start"] for p in api.closing_requests()]
    assert asked == [alpaca.rfc3339(d.close_ms) for d in reversed(days[10:])]
    assert [h.is_final for h in first.candles] == [False] * 10 + [True] * 20
    assert [h.close for h in first.candles[10:]] == [
        _D("100.5") + i for i in range(10, 30)
    ]
    assert first.covered == tuple(
        candle_rules.subtract_spans([span], [_hole(d) for d in days[:10]])
    )
    second = await adapter.fetch_candle_coverage(_AAPL, _TF.H1, *span)
    assert len(api.closing_requests()) == 30
    assert all(h.is_final for h in second.candles)
    assert second.candles[0].close == _D("100.5")
    assert second.covered == (span,)
    await adapter.fetch_candle_coverage(_AAPL, _TF.M1, *span)
    assert len(api.closing_requests()) == 30
    # Across fetches and symbols at most 60 windows a minute are read.
    many = _sessions_before(_SESSION, 70)
    _serve_sessions(api, many)
    fresh = _adapter(instruments.Venue.NASDAQ, clock)
    wide = (many[0].open_ms, _CLOSE)
    for _ in range(4):
        capped = await fresh.fetch_candle_coverage(_AAPL, _TF.H1, *wide)
    assert len(api.closing_requests()) == 30 + 60
    assert [h.is_final for h in capped.candles] == [False] * 10 + [True] * 60
    clock.current_ms += 60_000
    healed = await fresh.fetch_candle_coverage(_AAPL, _TF.H1, *wide)
    assert len(api.closing_requests()) == 30 + 70
    assert all(h.is_final for h in healed.candles)


async def test_service_repairs_pending_sessions_once(
    api: _Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Batches report pending last minutes as gaps until a fetch reads them."""
    monkeypatch.setattr(factory, "_ALPACA_RATE", _UNPACED)
    days = _sessions_before(_SESSION, 30)
    _serve_sessions(api, days)
    clock = clock_lib.ManualClock(_CLOSE + 9 * 3_600_000)
    adapters = {
        instruments.Venue.NASDAQ: _adapter(instruments.Venue.NASDAQ, clock)
    }
    store = candle_store.CandleStore()
    svc = service_lib.MarketDataService(
        adapters,
        registry.InstrumentRegistry(adapters),
        store,
        feed_router.FeedRouter(adapters, store, clock),
        clock,
    )
    batch = await svc.get_batch(
        _AAPL, _TF.H1, from_ms=days[0].open_ms, to_ms=_CLOSE
    )
    assert not batch.complete and len(batch.gaps) == 10
    batch = await svc.get_batch(
        _AAPL, _TF.H1, from_ms=days[0].open_ms, to_ms=_CLOSE
    )
    assert batch.complete and len(batch.candles) == 30
    bars = sum(kind == "bars" for kind, _, _ in api.requests)
    for _ in range(5):
        batch = await svc.get_batch(
            _AAPL, _TF.H1, from_ms=days[0].open_ms, to_ms=_CLOSE
        )
    assert batch.complete
    assert sum(kind == "bars" for kind, _, _ in api.requests) == bars
    assert len(api.closing_requests()) == 30
    finals = await svc.get_candles(_AAPL, _TF.H1, days[0].open_ms, _CLOSE)
    assert finals[-1].close == _D("100.5") + 29


async def test_live_minute_stream_folds_the_print_after_the_window(
    api: _Api, contract_validate: Validate
) -> None:
    """The streamed 15:59 bar stays open until the print is read over REST."""
    gate = _Gate()
    for frame in (
        '[{"T":"success","msg":"connected"}]',
        '[{"T":"success","msg":"authenticated"}]',
    ):
        gate.frames.put_nowait(frame)
    clock = clock_lib.ManualClock(_CLOSE + 5_000)
    adapter = _adapter(instruments.Venue.NASDAQ, clock, gate=gate)
    stream = adapter.stream_candle_coverage(_AAPL, _TF.M1)
    rows = api.minutes["AAPL"]

    async def release(ts_ms: int) -> candle_rules.CandleCoverage:
        """Streams the fixture bar at an instant and waits for an update.

        Args:
            ts_ms: Time of the fixture bar to stream, epoch milliseconds.

        Returns:
            The next coverage update.
        """
        gate.frames.put_nowait(_bar_frame(_row(rows, ts_ms)))
        return await asyncio.wait_for(anext(stream), 5)

    seeded = await release(_CLOSE - 2 * _MINUTE)
    by_start = {c.start_ms: c for c in seeded.candles}
    assert by_start[_CLOSE - 2 * _MINUTE].is_final
    assert not by_start[_CLOSE - _MINUTE].is_final  # REST seed, pending.
    streamed = await release(_CLOSE - _MINUTE)
    (last,) = streamed.candles
    assert last.source == "alpaca.ws" and not last.is_final
    assert last.close == _D("231.02")
    assert streamed.covered == ()  # The last minute is withheld.
    assert api.closing_requests() == []
    clock.current_ms = _CLOSE + 90_000
    folded = await release(_CLOSE)  # Alpaca's post-market 16:00 bar.
    await stream.aclose()  # type: ignore[attr-defined]  # stream_candle_coverage is typed AsyncIterator; its generator has aclose()
    (bar,) = folded.candles
    assert bar.start_ms == _CLOSE - _MINUTE and bar.is_final
    assert bar.close == _CROSS and bar.source == "alpaca.ws"
    assert bar.base_volume == _D(301_200) + _PRINTED
    contract_validate("market.schema.json", "Candle", bar.to_contract())
    # The withheld last minute is proven once its prints are folded in.
    assert (_CLOSE - _MINUTE, _CLOSE) in folded.covered
    assert len(api.closing_requests()) == 1


def test_request_budget_is_a_rolling_minute() -> None:
    """Requests beyond the budget are refused until a minute has passed."""
    clock = clock_lib.ManualClock(0)
    budget = alpaca.RequestBudget(clock, 2)
    assert budget.take() and budget.take() and not budget.take()
    clock.current_ms = 59_999
    assert not budget.take()
    clock.current_ms = 60_000
    assert budget.take() and budget.take() and not budget.take()


def test_unsettled_reopens_bars_over_pending_slots() -> None:
    """Only final bars overlapping a pending slot become non-final."""
    hour = 3_600_000
    bars = [
        market.Candle(
            instrument_id=_AAPL,
            timeframe=_TF.H1,
            start_ms=start,
            end_ms=start + hour,
            open=_D(1),
            high=_D(1),
            low=_D(1),
            close=_D(1),
            base_volume=_D(1),
            is_final=True,
            source="test",
            feed_state=quality.FeedState.LIVE,
            received_at_ms=0,
        )
        for start in (_OPEN, _OPEN + hour)
    ]
    out = alpaca.unsettled(
        bars, [(_OPEN + 2 * hour - 60_000, _OPEN + 2 * hour)]
    )
    assert [b.is_final for b in out] == [True, False]
    assert alpaca.unsettled(bars, []) == bars
