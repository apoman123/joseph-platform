# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Contract tests: TWSE, TPEx, Nasdaq/NYSE (Alpaca) and Treasury adapters.

TWSE (OpenAPI, STOCK_DAY, holiday schedules, MIS), TPEx (OpenAPI,
tradingStock, tradingDate, MIS ``otc_`` channels after the close), the
Nasdaq Trader symbol directory and the Treasury CSVs were captured live on
2026-11-23;
Alpaca responses are hand-written from its documentation (no key on the
build host, see ``fixtures/venues/alpaca/README.md``). Every produced
object must validate against the JSON contracts and follow the instrument's
session calendar.
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

import httpx
import pytest
import respx

from joseph.domain import clock as clock_lib
from joseph.domain import errors
from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import sessions
from joseph.domain import timeframes
from joseph.market_data.adapters import alpaca
from joseph.market_data.adapters import factory
from joseph.market_data.adapters import tpex
from joseph.market_data.adapters import treasury
from joseph.market_data.adapters import twse
from joseph.market_data.app import ports
from joseph.market_data.app import registry
from joseph.market_data.domain import candles as candle_rules

Validate = Callable[[str, str, object], None]
_D = decimal.Decimal
_TF = timeframes.Timeframe
_FIXTURES = pathlib.Path(__file__).resolve().parents[3] / "fixtures" / "venues"
_TPE = datetime.timezone(datetime.timedelta(hours=8))
_TWSE_MS = 1_795_408_205_000
"""2026-11-23 12:30:05 Taipei, just after the newest MIS capture."""
_US_SESSION = sessions.XNYS.session(datetime.date(2026, 11, 20))
assert _US_SESSION is not None
_US_OPEN = _US_SESSION.open_ms
_ALPACA_MS = _US_OPEN + 7 * 60_000 + 30_000
"""2026-11-20 09:37:30 New York."""
_KEYS = alpaca.AlpacaConfig(key_id="test-key", secret_key="test-secret")
_TPEX_MS = 1_795_421_880_000
"""2026-11-23 16:18:00 Taipei, just after the TPEx capture (after the close)."""
_TSMC = instruments.InstrumentId.parse("twse:stock:2330")
_ETF50 = instruments.InstrumentId.parse("twse:etf:0050")
_GW = instruments.InstrumentId.parse("tpex:stock:6488")
_BOND = instruments.InstrumentId.parse("tpex:etf:00679B")
_AAPL = instruments.InstrumentId.parse("nasdaq:stock:AAPL")
_UST10 = instruments.InstrumentId.parse("treasury:rate:UST10Y")


def _load(venue: str, name: str) -> Any:
    """Reads a JSON fixture.

    Args:
        venue: Venue directory.
        name: File name without extension.

    Returns:
        Parsed JSON.
    """
    return json.loads((_FIXTURES / venue / f"{name}.json").read_text("utf-8"))


class _Frames:
    """Replays WebSocket frames, then idles."""

    def __init__(self, frames: list[str]) -> None:
        """Initializes the connection.

        Args:
            frames: Frames to deliver.
        """
        self.frames = list(frames)
        self.sent: list[str] = []

    async def send(self, message: str) -> None:
        """Records a sent frame.

        Args:
            message: Frame.
        """
        self.sent.append(message)

    async def recv(self) -> str:
        """Returns the next frame or idles.

        Returns:
            Frame.
        """
        if self.frames:
            return self.frames.pop(0)
        await asyncio.sleep(3600)
        return ""


def _connector(
    frames: list[str], opened: list[_Frames] | None = None
) -> Callable[[str], object]:
    """Builds a connector replaying frames.

    Args:
        frames: Frames.
        opened: Optional list receiving each opened connection.

    Returns:
        Connector.
    """

    @contextlib.asynccontextmanager
    async def connect(url: str) -> AsyncIterator[_Frames]:
        """Yields a replaying connection.

        Args:
            url: Ignored URL.

        Yields:
            Connection.
        """
        del url
        connection = _Frames(frames)
        if opened is not None:
            opened.append(connection)
        yield connection

    return connect


def _adapter(
    venue: instruments.Venue,
    clock: clock_lib.Clock,
    *,
    keys: alpaca.AlpacaConfig | None = _KEYS,
    frames: list[str] | None = None,
    opened: list[_Frames] | None = None,
) -> ports.MarketDataPort:
    """Builds one adapter over the fixture routes.

    Args:
        venue: Venue.
        clock: Clock.
        keys: Alpaca configuration.
        frames: WebSocket frames.
        opened: Receives opened connections.

    Returns:
        Adapter.
    """
    return factory.build_adapters(
        [venue],
        clock,
        client=httpx.AsyncClient(),
        connect=_connector(frames or [], opened),  # type: ignore[arg-type]
        alpaca_config=keys,
    )[venue]


@pytest.fixture(autouse=True)
def _restore_xtai() -> Iterator[None]:
    """Restores runtime XTAI closures after each test.

    Yields:
        Nothing.
    """
    saved = set(sessions.XTAI._extra_closures)
    yield
    sessions.XTAI._extra_closures.clear()
    sessions.XTAI.add_closures(saved)


# TWSE ----------------------------------------------------------------


async def test_twse_instruments_follow_listing_rules(
    venue_api: respx.Router, contract_validate: Validate
) -> None:
    """Stocks and ETFs carry TWSE ticks, limits, lots and asset classes."""
    del venue_api
    rows = await _adapter(
        instruments.Venue.TWSE, clock_lib.ManualClock(_TWSE_MS)
    ).list_instruments()
    by_id = {str(r.instrument_id): r for r in rows}
    for row in rows:
        contract_validate("market.schema.json", "Instrument", row.to_contract())
        assert row.calendar == "XTAI"
        assert row.board_lot == _D(1000) and row.lot_size == _D(1)
        assert row.key.quote == "TWD" and row.listing_exchange == "TWSE"
    tsmc = by_id["twse:stock:2330"]
    assert (tsmc.display_symbol, tsmc.local_name, tsmc.name) == (
        "2330",
        "台積電",
        "TSMC",
    )
    assert by_id["twse:etf:00646"].name == "Yuanta S&P 500 ETF"
    assert tsmc.tick_ladder == twse.STOCK_TICKS
    assert tsmc.tick_for(_D(2030)) == _D(5)
    assert tsmc.tick_for(_D(209)) == _D("0.5")
    assert tsmc.price_limit_pct == _D(10)
    assert tsmc.status is instruments.InstrumentStatus.TRADING
    etf = by_id["twse:etf:0050"]
    assert etf.tick_ladder == twse.ETF_TICKS
    assert etf.tick_for(_D("86.9")) == _D("0.05")
    assert etf.tick_for(_D("28.21")) == _D("0.01")
    assert etf.asset_class is instruments.AssetClass.EQUITY
    bond = by_id["twse:etf:00865B"]
    assert bond.asset_class is instruments.AssetClass.BOND
    assert bond.price_limit_pct is None
    assert by_id["twse:etf:00631L"].price_limit_pct == _D(20)
    assert by_id["twse:etf:00632R"].price_limit_pct == _D(10)
    assert by_id["twse:etf:00646"].price_limit_pct is None
    for excluded in ("00625K", "01001T", "2881A"):
        assert not any(
            r.instrument_id.venue_market_id == excluded for r in rows
        )


async def test_registry_search_matches_local_and_english_names(
    venue_api: respx.Router,
) -> None:
    """Chinese and English names find TWSE listings."""
    del venue_api
    adapter = _adapter(instruments.Venue.TWSE, clock_lib.ManualClock(_TWSE_MS))
    lookup = registry.InstrumentRegistry({instruments.Venue.TWSE: adapter})
    by_chinese = await lookup.search("台積")
    assert [str(r.instrument_id) for r in by_chinese] == ["twse:stock:2330"]
    by_english = await lookup.search("hon hai")
    assert [str(r.instrument_id) for r in by_english] == ["twse:stock:2317"]


async def test_twse_daily_history_is_session_aligned(
    venue_api: respx.Router, contract_validate: Validate
) -> None:
    """STOCK_DAY rows become final XTAI daily bars in shares."""
    del venue_api
    adapter = _adapter(instruments.Venue.TWSE, clock_lib.ManualClock(_TWSE_MS))
    start = int(datetime.datetime(2026, 11, 1, tzinfo=_TPE).timestamp() * 1000)
    rows = await adapter.fetch_candles(_TSMC, _TF.D1, start, _TWSE_MS)
    raw = _load("twse", "stock_day_2330")["data"]
    final = [r for r in rows if r.is_final]
    assert len(final) == len(raw)
    first = final[0]
    assert first.base_volume == twse.number(raw[0][1])
    assert first.close == twse.number(raw[0][6])
    assert first.trade_count == int(twse.number(raw[0][8]) or 0)
    for candle in rows:
        contract_validate("market.schema.json", "Candle", candle.to_contract())
        found = sessions.XTAI.session(
            datetime.datetime.fromtimestamp(candle.start_ms / 1000, _TPE).date()
        )
        assert found is not None and found.open_ms == candle.start_ms
    live = rows[-1]
    assert not live.is_final and live.source == twse.MIS_SOURCE
    assert live.base_volume == _D(53164) * 1000
    weeks = await adapter.fetch_candles(_TSMC, _TF.W1, start, _TWSE_MS)
    assert all(not w.is_final for w in weeks[-1:])
    assert all(w.is_final for w in weeks[:-1])
    assert sum(w.base_volume for w in weeks) == sum(r.base_volume for r in rows)
    months = await adapter.fetch_candles(_TSMC, _TF.MN1, start, _TWSE_MS, 1)
    assert len(months) == 1 and months[0].high == max(r.high for r in rows)


async def test_twse_snapshot_and_book_from_mis(
    venue_api: respx.Router, contract_validate: Validate
) -> None:
    """MIS gives the last trade, best quotes and a 5-level book in shares."""
    del venue_api
    clock = clock_lib.ManualClock(_TWSE_MS)
    adapter = _adapter(instruments.Venue.TWSE, clock)
    snapshot = await adapter.fetch_snapshot(_ETF50)
    contract_validate(
        "market.schema.json",
        "MarketSnapshot",
        snapshot.to_contract(_TWSE_MS, 30_000),
    )
    assert snapshot.last == _D(87)
    assert (snapshot.best_bid, snapshot.best_ask) == (_D(87), _D("87.05"))
    assert snapshot.volume_24h_base == _D(431093) * 1000
    assert snapshot.change_24h_pct is not None and snapshot.change_24h_pct > 0
    book = await adapter.fetch_order_book(_TSMC, 10)
    contract_validate("market.schema.json", "OrderBook", book.to_contract())
    assert len(book.bids) == len(book.asks) == 5
    assert book.asks[0].price == _D(2045) and book.asks[0].quantity == _D(
        112000
    )
    assert book.bids[0].price == _D(2040)
    assert [b.price for b in book.bids] == sorted(
        (b.price for b in book.bids), reverse=True
    )
    tsmc = await adapter.fetch_snapshot(_TSMC)
    assert tsmc.last == _D(2040)  # MIS trade fallback when z is "-".


async def test_twse_bulk_snapshots_are_last_session_values(
    venue_api: respx.Router, contract_validate: Validate
) -> None:
    """STOCK_DAY_ALL rows are stamped at that session's close."""
    del venue_api
    adapter = _adapter(instruments.Venue.TWSE, clock_lib.ManualClock(_TWSE_MS))
    rows = {str(s.instrument_id): s for s in await adapter.fetch_snapshots()}
    friday = sessions.XTAI.session(datetime.date(2026, 11, 20))
    assert friday is not None
    etf = rows["twse:etf:0050"]
    assert etf.as_of_ms == friday.close_ms
    assert etf.last == _D("84.45")
    assert etf.turnover_24h_quote == _D(74858941327)
    for row in rows.values():
        contract_validate(
            "market.schema.json",
            "MarketSnapshot",
            row.to_contract(_TWSE_MS, 30_000),
        )


async def test_twse_intraday_bars_come_from_recorded_snapshots(
    venue_api: respx.Router, contract_validate: Validate
) -> None:
    """Recorded MIS snapshots become 1m and 5m bars with classified delta."""
    del venue_api
    responses = _load("twse", "mis_recorded")["responses"]
    clock = clock_lib.ManualClock(responses[0]["received_ms"])
    adapter = _adapter(instruments.Venue.TWSE, clock)
    assert isinstance(adapter, twse.TwseAdapter)
    session = sessions.XTAI.session(datetime.date(2026, 11, 23))
    assert session is not None
    assert (
        await adapter.fetch_candles(
            _ETF50, _TF.M1, session.open_ms, session.close_ms
        )
        == []
    )
    for item in responses:
        clock.current_ms = item["received_ms"]
        quotes = [
            q
            for r in item["response"]["msgArray"]
            if (q := twse.parse_mis_record(r, item["received_ms"])) is not None
        ]
        adapter.ingest(quotes)
    ones = await adapter.fetch_candles(
        _ETF50, _TF.M1, session.open_ms, session.close_ms
    )
    assert ones
    for candle in ones:
        contract_validate("market.schema.json", "Candle", candle.to_contract())
        assert sessions.XTAI.is_aligned(_TF.M1, candle.start_ms)
    finals = [c for c in ones if c.is_final]
    assert finals and not ones[-1].is_final
    classified = [c for c in ones if c.taker_buy_base_volume is not None]
    assert all(
        c.buy_volume_method is market.BuyVolumeMethod.CLASSIFIED
        for c in classified
    )
    fives = await adapter.fetch_candles(
        _ETF50, _TF.M5, session.open_ms, session.close_ms
    )
    assert fives and all(
        sessions.XTAI.is_aligned(_TF.M5, c.start_ms) for c in fives
    )
    assert await adapter.close() is None


async def test_twse_intraday_coverage_is_the_mis_recording(
    venue_api: respx.Router,
) -> None:
    """Recorded minutes are coverage; before the recording they are gaps."""
    del venue_api
    responses = _load("twse", "mis_recorded")["responses"]
    clock = clock_lib.ManualClock(responses[0]["received_ms"])
    adapter = _adapter(instruments.Venue.TWSE, clock)
    assert isinstance(adapter, twse.TwseAdapter)
    assert ports.sparse_bars(adapter)
    session = sessions.XTAI.session(datetime.date(2026, 11, 23))
    assert session is not None
    window = (session.open_ms, session.close_ms)
    empty = await adapter.fetch_candle_coverage(_ETF50, _TF.M1, *window)
    assert empty.candles == () and empty.covered == () and empty.complete
    for item in responses:
        clock.current_ms = item["received_ms"]
        adapter.ingest(
            [
                q
                for r in item["response"]["msgArray"]
                if (q := twse.parse_mis_record(r, item["received_ms"]))
                is not None
            ]
        )
    fetched = await adapter.fetch_candle_coverage(_ETF50, _TF.M1, *window)
    assert fetched.covered and fetched.complete
    assert list(fetched.covered) == candle_rules.clip_spans(
        adapter._builders["0050"].coverage(), *window
    )
    finals = [c for c in fetched.candles if c.is_final]
    first_known = fetched.covered[0][0]
    end = finals[-1].end_ms
    gaps = candle_rules.find_gaps(
        [c.start_ms for c in finals],
        _TF.M1,
        session.open_ms,
        end,
        calendar=sessions.XTAI,
        covered=fetched.covered,
    )
    # Before the recording everything is a gap; inside it only the minutes
    # that traded at an unknown price are (quiet minutes are not).
    unpriced = {
        start
        for start, bar in adapter._builders["0050"]._bars.items()
        if bar.open is None
    }
    assert gaps and gaps[0] == (session.open_ms, gaps[0][1])
    assert gaps[0][1] > first_known
    for low, high in gaps[1:]:
        assert set(range(low, high, 60_000)) <= unpriced
    # A limit that drops older bars drops their coverage too.
    newest = await adapter.fetch_candle_coverage(_ETF50, _TF.M1, *window, 3)
    assert len(newest.candles) == 3 < len(fetched.candles)
    assert newest.covered[0][0] >= newest.candles[0].start_ms
    assert await adapter.close() is None


async def test_twse_daily_coverage_is_the_months_read(
    venue_api: respx.Router,
) -> None:
    """STOCK_DAY months cover their days up to the day they were read."""
    del venue_api
    adapter = _adapter(instruments.Venue.TWSE, clock_lib.ManualClock(_TWSE_MS))
    assert isinstance(adapter, twse.TwseAdapter)
    start = twse.taipei_ms(datetime.date(2026, 10, 1))
    fetched = await adapter.fetch_candle_coverage(
        _TSMC, _TF.D1, start, _TWSE_MS
    )
    today = twse.taipei_ms(datetime.date(2026, 11, 23))
    assert fetched.covered == ((start, today),) and fetched.complete
    finals = [c.start_ms for c in fetched.candles if c.is_final]
    assert (
        candle_rules.find_gaps(
            finals,
            _TF.D1,
            start,
            today,
            calendar=sessions.XTAI,
            covered=fetched.covered,
        )
        == []
    )
    capped = await adapter.fetch_candle_coverage(
        _TSMC, _TF.D1, start, _TWSE_MS, 3
    )
    assert len(capped.candles) == 3 and not capped.complete
    assert capped.covered[0][0] == capped.candles[0].start_ms
    weeks = await adapter.fetch_candle_coverage(_TSMC, _TF.W1, start, today)
    assert weeks.covered == ((start, today),)


async def test_twse_refuses_what_it_does_not_have(
    venue_api: respx.Router,
) -> None:
    """Seconds bars, streams, trades, funding and OI are unsupported."""
    del venue_api
    adapter = _adapter(instruments.Venue.TWSE, clock_lib.ManualClock(_TWSE_MS))
    with pytest.raises(errors.UnsupportedError):
        await adapter.fetch_candles(_TSMC, _TF.S5, 0, 1)
    with pytest.raises(errors.UnsupportedError):
        adapter.stream_candles(_TSMC, _TF.M1)
    with pytest.raises(errors.UnsupportedError):
        adapter.stream_trades(_TSMC)
    with pytest.raises(errors.UnsupportedError):
        await adapter.fetch_trades(_TSMC, 0, 1)
    with pytest.raises(errors.UnsupportedError):
        await adapter.fetch_open_interest(_TSMC)
    with pytest.raises(errors.UnsupportedError):
        await adapter.fetch_funding(_TSMC)
    caps = adapter.capabilities()
    assert caps.calendar == "XTAI"
    assert caps.market_data.classified_delta
    assert not caps.market_data.taker_volume
    assert not set(caps.timeframes) & set(timeframes.SECONDS)


# TPEx ----------------------------------------------------------------


def _limit_prices(
    instrument: instruments.Instrument, reference: decimal.Decimal
) -> tuple[decimal.Decimal, decimal.Decimal]:
    """Computes limit prices from a reference price, on the tick ladder.

    Args:
        instrument: Instrument with a price limit.
        reference: Reference (previous close) price.

    Returns:
        Limit-up rounded down and limit-down rounded up to their ticks.
    """
    assert instrument.price_limit_pct is not None
    move = instrument.price_limit_pct / 100
    up = reference * (1 + move)
    down = reference * (1 - move)
    up_tick = instrument.tick_for(up)
    down_tick = instrument.tick_for(down)
    return (
        (up / up_tick).to_integral_value(decimal.ROUND_FLOOR) * up_tick,
        (down / down_tick).to_integral_value(decimal.ROUND_CEILING) * down_tick,
    )


async def test_tpex_instruments_follow_listing_rules(
    venue_api: respx.Router, contract_validate: Validate
) -> None:
    """Mainboard stocks and ETFs carry TPEx ticks, limits, lots and classes."""
    del venue_api
    rows = await _adapter(
        instruments.Venue.TPEX, clock_lib.ManualClock(_TPEX_MS)
    ).list_instruments()
    by_id = {str(r.instrument_id): r for r in rows}
    for row in rows:
        contract_validate("market.schema.json", "Instrument", row.to_contract())
        assert row.calendar == "XTAI"
        assert row.board_lot == _D(1000) and row.lot_size == _D(1)
        assert row.key.quote == "TWD" and row.listing_exchange == "TPEx"
    gw = by_id["tpex:stock:6488"]
    assert (gw.display_symbol, gw.local_name, gw.name) == (
        "6488",
        "環球晶",
        "GWC",
    )
    assert gw.tick_ladder == twse.STOCK_TICKS
    assert gw.price_limit_pct == _D(10)
    assert gw.status is instruments.InstrumentStatus.TRADING
    # Ticks seen in MIS quote ladders on 2026-11-23.
    assert gw.tick_for(_D("460.5")) == _D("0.5")
    assert by_id["tpex:stock:5274"].tick_for(_D(8310)) == _D(5)
    assert by_id["tpex:stock:4714"].tick_for(_D("8.70")) == _D("0.01")
    bond = by_id["tpex:etf:00679B"]
    assert bond.tick_ladder == twse.ETF_TICKS
    assert bond.asset_class is instruments.AssetClass.BOND
    assert bond.price_limit_pct is None
    assert bond.tick_for(_D("26.33")) == _D("0.01")
    assert by_id["tpex:etf:00758B"].tick_for(_D("50.50")) == _D("0.05")
    for code in ("00937B", "00687B", "00980D"):
        assert by_id[f"tpex:etf:{code}"].asset_class is (
            instruments.AssetClass.BOND
        )
    domestic = by_id["tpex:etf:006201"]
    assert domestic.asset_class is instruments.AssetClass.EQUITY
    assert domestic.price_limit_pct == _D(10)
    for halted in ("4804", "8084"):
        assert by_id[f"tpex:stock:{halted}"].status is (
            instruments.InstrumentStatus.HALTED
        )
    for excluded in ("00687C", "8349A", "020025"):
        assert not any(
            r.instrument_id.venue_market_id == excluded for r in rows
        )


async def test_tpex_published_limit_prices_follow_the_rules(
    venue_api: respx.Router,
) -> None:
    """TPEx's next-session limits are 10 % of the close on the tick ladder."""
    del venue_api
    rows = await _adapter(
        instruments.Venue.TPEX, clock_lib.ManualClock(_TPEX_MS)
    ).list_instruments()
    by_code = {r.instrument_id.venue_market_id: r for r in rows}
    checked = 0
    for quote in _load("tpex", "mainboard_quotes"):
        listed = by_code.get(quote["SecuritiesCompanyCode"])
        close = twse.number(quote["Close"])
        if listed is None or close is None:
            continue
        published = (
            twse.number(quote["NextLimitUp"]),
            twse.number(quote["NextLimitDown"]),
        )
        if listed.price_limit_pct is None:
            assert published[0] == tpex.UNLIMITED_UP
            continue
        assert _limit_prices(listed, close) == published
        checked += 1
    assert checked >= 8


async def test_tpex_daily_history_is_session_aligned(
    venue_api: respx.Router, contract_validate: Validate
) -> None:
    """The tradingStock rows become final XTAI daily bars in shares, TWD."""
    del venue_api
    adapter = _adapter(instruments.Venue.TPEX, clock_lib.ManualClock(_TPEX_MS))
    start = twse.taipei_ms(datetime.date(2026, 11, 1))
    rows = await adapter.fetch_candles(_GW, _TF.D1, start, _TPEX_MS)
    raw = _load("tpex", "trading_stock_6488")["tables"][0]["data"]
    assert len(rows) == len(raw) and all(r.is_final for r in rows)
    first, last = rows[0], rows[-1]
    assert first.base_volume == _D(10402) * 1000
    assert first.quote_turnover == _D(7831069) * 1000
    assert first.close == _D(760) and first.trade_count == 22796
    assert last.close == _D("460.50") and last.source == tpex.REST_SOURCE
    for candle in rows:
        contract_validate("market.schema.json", "Candle", candle.to_contract())
        found = sessions.XTAI.session(twse.taipei_date(candle.start_ms))
        assert found is not None and found.open_ms == candle.start_ms
    weeks = await adapter.fetch_candles(_GW, _TF.W1, start, _TPEX_MS)
    assert sum(w.base_volume for w in weeks) == sum(r.base_volume for r in rows)
    months = await adapter.fetch_candles(_GW, _TF.MN1, start, _TPEX_MS, 1)
    assert len(months) == 1 and months[0].high == max(r.high for r in rows)
    covered = await adapter.fetch_candle_coverage(_GW, _TF.D1, start, _TPEX_MS)
    today = twse.taipei_ms(datetime.date(2026, 11, 23))
    assert covered.covered == ((start, today),) and covered.complete


async def test_tpex_daily_history_stops_at_the_listing_day(
    venue_api: respx.Router,
) -> None:
    """The listing-day mark (``106/01/17*``) ends the walk back in time."""
    adapter = _adapter(instruments.Venue.TPEX, clock_lib.ManualClock(_TPEX_MS))
    await adapter.list_instruments()
    assert isinstance(adapter, tpex.TpexAdapter)
    assert adapter.listing["00679B"].listed is None
    start = twse.taipei_ms(datetime.date(2015, 1, 1))
    end = twse.taipei_ms(datetime.date(2017, 2, 1))
    before = venue_api.calls.call_count
    fetched = await adapter.fetch_candle_coverage(_BOND, _TF.D1, start, end)
    months = [
        c.request.url.params["date"]
        for c in venue_api.calls[before:]
        if c.request.url.path.endswith("/tradingStock")
    ]
    assert months == ["2017/01/01"]
    assert adapter.listing["00679B"].listed == datetime.date(2017, 1, 17)
    assert fetched.complete and len(fetched.candles) == 6
    assert fetched.candles[0].base_volume == _D(928_000)
    assert fetched.covered == (
        (twse.taipei_ms(datetime.date(2017, 1, 1)), end),
    )


async def test_tpex_snapshot_and_book_come_from_otc_mis_channels(
    venue_api: respx.Router, contract_validate: Validate
) -> None:
    """MIS ``otc_`` records give the last trade, quotes and a 5-level book."""
    adapter = _adapter(instruments.Venue.TPEX, clock_lib.ManualClock(_TPEX_MS))
    snapshot = await adapter.fetch_snapshot(_GW)
    contract_validate(
        "market.schema.json",
        "MarketSnapshot",
        snapshot.to_contract(_TPEX_MS, 30_000),
    )
    assert snapshot.source == tpex.MIS_SOURCE
    assert snapshot.last == _D("460.5")
    assert (snapshot.best_bid, snapshot.best_ask) == (_D(460), _D("460.5"))
    assert snapshot.volume_24h_base == _D(11812) * 1000
    channels = [
        c.request.url.params["ex_ch"]
        for c in venue_api.calls
        if c.request.url.host == "mis.twse.com.tw"
    ]
    assert channels == ["otc_6488.tw"]
    book = await adapter.fetch_order_book(_BOND, 5)
    contract_validate("market.schema.json", "OrderBook", book.to_contract())
    assert len(book.bids) == len(book.asks) == 5
    assert book.bids[0].price == _D("26.32")
    assert book.asks[0].price == _D("26.33")
    assert book.asks[0].quantity == _D(182) * 1000
    assert all(level.quantity % 1000 == 0 for level in book.bids)


async def test_tpex_bulk_snapshots_are_last_session_values(
    venue_api: respx.Router, contract_validate: Validate
) -> None:
    """tpex_mainboard_quotes rows are stamped at that session's close."""
    del venue_api
    adapter = _adapter(instruments.Venue.TPEX, clock_lib.ManualClock(_TPEX_MS))
    rows = {str(s.instrument_id): s for s in await adapter.fetch_snapshots()}
    monday = sessions.XTAI.session(datetime.date(2026, 11, 23))
    assert monday is not None
    bond = rows["tpex:etf:00679B"]
    assert bond.as_of_ms == monday.close_ms
    assert bond.last == _D("26.33")
    assert bond.turnover_24h_quote == _D(1047529510)
    assert bond.volume_24h_base == _D(39822000)
    assert "tpex:etf:00687C" not in rows
    for row in rows.values():
        contract_validate(
            "market.schema.json",
            "MarketSnapshot",
            row.to_contract(_TPEX_MS, 30_000),
        )


async def test_tpex_recorder_polls_otc_channels_with_sparse_coverage(
    venue_api: respx.Router,
) -> None:
    """Intraday requests start MIS recording of ``otc_`` channels."""
    session = sessions.XTAI.session(datetime.date(2026, 11, 23))
    assert session is not None
    clock = clock_lib.ManualClock(session.open_ms + 3_600_000)
    adapter = _adapter(instruments.Venue.TPEX, clock)
    assert isinstance(adapter, tpex.TpexAdapter)
    assert ports.sparse_bars(adapter)
    window = (session.open_ms, session.close_ms)
    empty = await adapter.fetch_candle_coverage(_GW, _TF.M1, *window)
    assert empty.candles == () and empty.covered == () and empty.complete
    # The capture is one closing snapshot: a baseline (applied by this poll
    # or the recorder's own), no bar yet.
    await adapter.poll_once()
    builder = adapter._builders["6488"]
    assert builder.applied == 1 and builder.bars(*window, clock.now_ms()) == []
    channels = {
        c.request.url.params["ex_ch"]
        for c in venue_api.calls
        if c.request.url.host == "mis.twse.com.tw"
    }
    assert channels == {"otc_6488.tw"}
    assert await adapter.close() is None


async def test_tpex_schedule_lists_the_xtai_closures(
    venue_api: respx.Router,
) -> None:
    """TPEx's 2026 and 2027 schedules close exactly the XTAI days."""
    del venue_api
    for year, unscheduled in (
        (2026, {datetime.date(2026, 7, 10), datetime.date(2026, 10, 23)}),
        (2027, set()),
    ):
        closures = tpex.schedule_closures(
            _load("tpex", f"trading_date_{year}"),
            year,
            datetime.date(year, 1, 1),
        )
        day = datetime.date(year, 1, 1)
        xtai = set()
        while day.year == year:
            if day.weekday() < 5 and sessions.XTAI.session(day) is None:
                xtai.add(day)
            day += datetime.timedelta(days=1)
        # Typhoon closures are announced on the day, not in the schedule.
        assert closures == xtai - unscheduled
    adapter = _adapter(instruments.Venue.TPEX, clock_lib.ManualClock(_TPEX_MS))
    assert isinstance(adapter, tpex.TpexAdapter)
    assert await adapter.refresh_calendar() == set()


async def test_tpex_refuses_what_it_does_not_have(
    venue_api: respx.Router,
) -> None:
    """Seconds bars, streams, trades, funding and OI are unsupported."""
    del venue_api
    adapter = _adapter(instruments.Venue.TPEX, clock_lib.ManualClock(_TPEX_MS))
    with pytest.raises(errors.UnsupportedError):
        await adapter.fetch_candles(_GW, _TF.S5, 0, 1)
    with pytest.raises(errors.UnsupportedError):
        adapter.stream_trades(_GW)
    with pytest.raises(errors.UnsupportedError):
        await adapter.fetch_trades(_GW, 0, 1)
    with pytest.raises(errors.UnsupportedError, match="tpex"):
        await adapter.fetch_open_interest(_GW)
    with pytest.raises(errors.UnsupportedError, match="tpex"):
        await adapter.fetch_funding(_GW)
    caps = adapter.capabilities()
    assert caps.calendar == "XTAI" and caps.venue is instruments.Venue.TPEX
    assert caps.market_data.classified_delta
    assert not caps.market_data.taker_volume
    assert not set(caps.timeframes) & set(timeframes.SECONDS)


# Nasdaq / NYSE via Alpaca ------------------------------------------------


async def test_directory_maps_listings_to_venues(
    venue_api: respx.Router, contract_validate: Validate
) -> None:
    """Listings split by exchange; tests, preferreds and units are skipped."""
    del venue_api
    clock = clock_lib.ManualClock(_ALPACA_MS)
    nasdaq = {
        r.instrument_id.venue_market_id: r
        for r in await _adapter(
            instruments.Venue.NASDAQ, clock
        ).list_instruments()
    }
    nyse = {
        r.instrument_id.venue_market_id: r
        for r in await _adapter(
            instruments.Venue.NYSE, clock
        ).list_instruments()
    }
    cboe = {
        r.instrument_id.venue_market_id: r
        for r in await _adapter(
            instruments.Venue.CBOE, clock
        ).list_instruments()
    }
    for row in [*nasdaq.values(), *nyse.values(), *cboe.values()]:
        contract_validate("market.schema.json", "Instrument", row.to_contract())
        assert row.calendar == "XNYS"
        assert row.tick_for(_D("0.5")) == _D("0.0001")
        assert row.tick_for(_D(5)) == _D("0.01")
    assert str(nasdaq["AAPL"].instrument_id) == "nasdaq:stock:AAPL"
    assert nasdaq["AAPL"].name == "Apple Inc. - Common Stock"
    assert nasdaq["AAPL"].local_name is None
    assert str(nasdaq["QQQ"].instrument_id) == "nasdaq:etf:QQQ"
    assert nasdaq["TLT"].asset_class is instruments.AssetClass.BOND
    assert nasdaq["QQQ"].asset_class is instruments.AssetClass.EQUITY
    assert "ZXYZ.A" not in nasdaq
    assert nyse["BRK.B"].listing_exchange == "NYSE"
    assert str(nyse["BRK.B"].instrument_id) == "nyse:stock:BRK.B"
    assert nyse["AGG"].listing_exchange == "NYSE Arca"
    assert nyse["AGG"].asset_class is instruments.AssetClass.BOND
    assert nyse["SPY"].asset_class is instruments.AssetClass.EQUITY
    assert nyse["SGOV"].asset_class is instruments.AssetClass.BOND
    assert set(cboe) == {"AAAU", "BBAG", "CBOE", "GOVT", "VXX"}
    govt = cboe["GOVT"]
    assert str(govt.instrument_id) == "cboe:etf:GOVT"
    assert govt.listing_exchange == "Cboe BZX"
    assert govt.asset_class is instruments.AssetClass.BOND
    assert govt.name == "iShares U.S. Treasury Bond ETF"
    assert cboe["BBAG"].asset_class is instruments.AssetClass.BOND
    assert cboe["AAAU"].asset_class is instruments.AssetClass.EQUITY
    assert str(cboe["CBOE"].instrument_id) == "cboe:stock:CBOE"
    assert str(cboe["VXX"].instrument_id) == "cboe:stock:VXX"
    for skipped in (
        "GOVT",
        "ABR$D",
        "AAC.U",
        "AAC.W",
        "AIIA.R",
        "ZEXIT",
        "MTEST",
        "ZZZTX",
        "CECO",
        "ZBZX",
    ):
        assert skipped not in nyse and skipped not in nasdaq


async def test_alpaca_without_keys_asks_for_them(
    venue_api: respx.Router,
) -> None:
    """The venues stay listed but data requests ask for Alpaca keys."""
    del venue_api
    adapter = _adapter(
        instruments.Venue.NASDAQ, clock_lib.ManualClock(_ALPACA_MS), keys=None
    )
    caps = adapter.capabilities()
    assert caps.requires_credentials and not caps.credentials_configured
    assert await adapter.list_instruments()
    with pytest.raises(
        errors.VenueUnavailableError, match="configure Alpaca keys"
    ):
        await adapter.fetch_candles(_AAPL, _TF.M1, 0, _ALPACA_MS)
    with pytest.raises(
        errors.VenueUnavailableError, match="configure Alpaca keys"
    ):
        await adapter.fetch_snapshots()


async def test_alpaca_minute_bars_are_session_bars(
    venue_api: respx.Router, contract_validate: Validate
) -> None:
    """Pages join, pre-market drops, settled bars are final, 5m aggregates."""
    calls = venue_api
    adapter = _adapter(
        instruments.Venue.NASDAQ, clock_lib.ManualClock(_ALPACA_MS)
    )
    rows = await adapter.fetch_candles(
        _AAPL, _TF.M1, _US_OPEN - 60_000, _ALPACA_MS
    )
    assert [r.start_ms - _US_OPEN for r in rows] == [
        i * 60_000 for i in range(7)
    ]
    assert [r.is_final for r in rows] == [True] * 6 + [False]
    for candle in rows:
        contract_validate("market.schema.json", "Candle", candle.to_contract())
    requests = [
        c.request
        for c in calls.calls
        if c.request.url.host == "data.alpaca.markets"
    ]
    assert requests[-1].url.params["page_token"]
    assert requests[-1].url.params["adjustment"] == "split"
    assert requests[-1].url.params["feed"] == "iex"
    assert "test-secret" not in repr(_KEYS)
    fives = await adapter.fetch_candles(_AAPL, _TF.M5, _US_OPEN, _ALPACA_MS)
    assert [f.start_ms for f in fives] == [_US_OPEN, _US_OPEN + 300_000]
    assert fives[0].base_volume == sum(r.base_volume for r in rows[:5])
    assert fives[0].is_final and not fives[1].is_final
    hours = await adapter.fetch_candles(_AAPL, _TF.H1, _US_OPEN, _ALPACA_MS)
    assert len(hours) == 1 and hours[0].start_ms == _US_OPEN


async def test_alpaca_daily_bars_are_restamped(
    venue_api: respx.Router, contract_validate: Validate
) -> None:
    """New York midnight bars move to the XNYS session open."""
    del venue_api
    adapter = _adapter(
        instruments.Venue.NASDAQ, clock_lib.ManualClock(_ALPACA_MS)
    )
    start = _US_OPEN - 4 * 86_400_000
    days = await adapter.fetch_candles(
        _AAPL, _TF.D1, start - 3_600_000, _ALPACA_MS
    )
    assert len(days) == 5
    assert days[-1].start_ms == _US_OPEN and not days[-1].is_final
    assert all(d.is_final for d in days[:-1])
    for candle in days:
        contract_validate("market.schema.json", "Candle", candle.to_contract())
    weeks = await adapter.fetch_candles(
        _AAPL, _TF.W1, start - 3_600_000, _ALPACA_MS
    )
    assert len(weeks) == 1 and weeks[0].base_volume == sum(
        d.base_volume for d in days
    )


async def test_alpaca_seconds_history_uses_conditions_and_lee_ready(
    venue_api: respx.Router, contract_validate: Validate
) -> None:
    """REST trades and quotes build classified 1s bars inside the session."""
    del venue_api
    adapter = _adapter(
        instruments.Venue.NASDAQ, clock_lib.ManualClock(_ALPACA_MS)
    )
    rows = await adapter.fetch_candles(
        _AAPL, _TF.S1, _US_OPEN, _US_OPEN + 5_000
    )
    by_second = {(r.start_ms - _US_OPEN) // 1000: r for r in rows}
    assert sorted(by_second) == [0, 1, 3, 4]
    assert by_second[0].base_volume == _D(105)
    assert by_second[0].taker_buy_base_volume == _D(105)
    assert by_second[1].taker_buy_base_volume == _D(200)
    assert by_second[1].low == _D("230.11")
    assert by_second[3].base_volume == _D(300)
    assert by_second[4].taker_buy_base_volume == _D(0)
    for candle in rows:
        contract_validate("market.schema.json", "Candle", candle.to_contract())
        assert candle.buy_volume_method is market.BuyVolumeMethod.CLASSIFIED
    trades = await adapter.fetch_trades(
        _AAPL, _US_OPEN - 5_000, _US_OPEN + 5_000
    )
    assert len(trades) == 8
    assert {t.aggressor_side for t in trades} == {market.AggressorSide.UNKNOWN}


async def test_alpaca_snapshots_and_unsupported_parts(
    venue_api: respx.Router, contract_validate: Validate
) -> None:
    """Snapshots validate; book, OI and funding are unsupported."""
    del venue_api
    clock = clock_lib.ManualClock(_ALPACA_MS)
    adapter = _adapter(instruments.Venue.NASDAQ, clock)
    snapshot = await adapter.fetch_snapshot(_AAPL)
    contract_validate(
        "market.schema.json",
        "MarketSnapshot",
        snapshot.to_contract(_ALPACA_MS, 30_000),
    )
    assert snapshot.last == _D("230.35")
    assert snapshot.best_bid == _D("230.34")
    bulk = {
        s.instrument_id.venue_market_id for s in await adapter.fetch_snapshots()
    }
    assert bulk == {"AAPL", "QQQ", "TLT"}
    nyse = _adapter(instruments.Venue.NYSE, clock)
    assert {
        s.instrument_id.venue_market_id for s in await nyse.fetch_snapshots()
    } == {"BRK.B", "SPY"}
    cboe = _adapter(instruments.Venue.CBOE, clock)
    (govt,) = await cboe.fetch_snapshots()
    assert str(govt.instrument_id) == "cboe:etf:GOVT"
    assert govt.last == _D("23.05") and govt.best_bid == _D("23.05")
    caps = cboe.capabilities()
    assert caps.display_name == "Cboe BZX (Alpaca iex)"
    assert caps.calendar == "XNYS"
    for call in (
        adapter.fetch_order_book(_AAPL, 5),
        adapter.fetch_open_interest(_AAPL),
        adapter.fetch_funding(_AAPL),
    ):
        with pytest.raises(errors.UnsupportedError):
            await call


async def test_alpaca_stream_delivers_bars_trades_and_halts(
    venue_api: respx.Router, contract_validate: Validate
) -> None:
    """One authenticated connection streams bars, revisions and statuses."""
    del venue_api
    frames = _load("alpaca", "ws_frames")["frames"]
    opened: list[_Frames] = []
    clock = clock_lib.ManualClock(_ALPACA_MS)
    adapter = _adapter(
        instruments.Venue.NASDAQ, clock, frames=frames, opened=opened
    )
    stream = adapter.stream_candles(_AAPL, _TF.M1)
    seen = [await asyncio.wait_for(anext(stream), 5) for _ in range(3)]
    await stream.aclose()  # type: ignore[attr-defined]
    for candle in seen:
        contract_validate("market.schema.json", "Candle", candle.to_contract())
    assert [c.start_ms - _US_OPEN for c in seen] == [300_000, 300_000, 360_000]
    assert seen[1].close == _D("230.27")  # updatedBars revision.
    assert all(c.is_final and c.source == "alpaca.ws" for c in seen)
    auth = json.loads(opened[0].sent[0])
    assert auth == {
        "action": "auth",
        "key": "test-key",
        "secret": "test-secret",
    }
    subscribe = json.loads(opened[0].sent[1])
    assert subscribe["bars"] == ["AAPL"] and subscribe["statuses"] == ["AAPL"]


async def test_alpaca_trade_and_seconds_streams(
    venue_api: respx.Router,
) -> None:
    """Streamed trades have no aggressor; seconds bars are classified."""
    del venue_api
    frames = _load("alpaca", "ws_frames")["frames"]
    clock = clock_lib.ManualClock(_ALPACA_MS)
    adapter = _adapter(instruments.Venue.NASDAQ, clock, frames=frames)
    trades = adapter.stream_trades(_AAPL)
    first = await asyncio.wait_for(anext(trades), 5)
    await trades.aclose()  # type: ignore[attr-defined]
    assert first.aggressor_side is market.AggressorSide.UNKNOWN
    adapter = _adapter(instruments.Venue.NASDAQ, clock, frames=frames)
    bars = adapter.stream_candles(_AAPL, _TF.S1)
    candle = await asyncio.wait_for(anext(bars), 5)
    await bars.aclose()  # type: ignore[attr-defined]
    # The first streamed trade's second is partial and never emitted.
    assert candle.start_ms == _US_OPEN + 1_000
    assert candle.taker_buy_base_volume == _D(200)
    assert candle.buy_volume_method is market.BuyVolumeMethod.CLASSIFIED


async def test_alpaca_status_marks_halts(venue_api: respx.Router) -> None:
    """A trading-halt status marks the streamed instrument HALTED."""
    del venue_api
    frames = _load("alpaca", "ws_frames")["frames"]
    clock = clock_lib.ManualClock(_ALPACA_MS + 86_400_000)
    adapter = _adapter(instruments.Venue.NASDAQ, clock, frames=frames)
    stream = adapter.stream_candles(_AAPL, _TF.D1)
    first = await asyncio.wait_for(anext(stream), 5)
    assert first.timeframe is _TF.D1
    for _ in range(50):
        listed = {
            r.instrument_id.venue_market_id: r
            for r in await adapter.list_instruments()
        }
        if listed["AAPL"].status is instruments.InstrumentStatus.HALTED:
            break
        await asyncio.sleep(0.01)
    await stream.aclose()  # type: ignore[attr-defined]
    assert listed["AAPL"].status is instruments.InstrumentStatus.HALTED


async def test_alpaca_stream_auth_failure_is_reported(
    venue_api: respx.Router,
) -> None:
    """A rejected key ends the stream with a clear error."""
    del venue_api
    errors_ = _load("alpaca", "ws_errors")
    frames = [_load("alpaca", "ws_frames")["frames"][0], errors_["auth_failed"]]
    adapter = _adapter(
        instruments.Venue.NASDAQ,
        clock_lib.ManualClock(_ALPACA_MS),
        frames=frames,
    )
    stream = adapter.stream_trades(_AAPL)
    with pytest.raises(errors.VenueUnavailableError, match="402"):
        await asyncio.wait_for(anext(stream), 5)


# Treasury ----------------------------------------------------------------


async def test_treasury_lists_nominal_and_real_series(
    venue_api: respx.Router, contract_validate: Validate
) -> None:
    """Every published tenor is a non-tradable BOND rate in percent."""
    del venue_api
    rows = await _adapter(
        instruments.Venue.TREASURY, clock_lib.ManualClock(_TWSE_MS)
    ).list_instruments()
    assert len(rows) == len(treasury.TENORS) == 19
    for row in rows:
        contract_validate("market.schema.json", "Instrument", row.to_contract())
        assert row.asset_class is instruments.AssetClass.BOND
        assert row.status is instruments.InstrumentStatus.UNKNOWN
        assert row.calendar == "USBOND"
    ids = {str(r.instrument_id) for r in rows}
    assert {"treasury:rate:UST1.5M", "treasury:rate:TIPS30Y"} <= ids
    names = {r.instrument_id.venue_market_id: r.name for r in rows}
    assert names["UST10Y"] == "US Treasury 10-year par yield"
    assert names["TIPS5Y"] == "US Treasury 5-year par real yield"


async def test_treasury_daily_weekly_monthly_bars(
    venue_api: respx.Router, contract_validate: Validate
) -> None:
    """Yields are flat USBOND bars; weeks and months aggregate them."""
    del venue_api
    adapter = _adapter(
        instruments.Venue.TREASURY, clock_lib.ManualClock(_TWSE_MS)
    )
    start = int(
        datetime.datetime(2026, 11, 1, tzinfo=datetime.UTC).timestamp() * 1000
    )
    days = await adapter.fetch_candles(_UST10, _TF.D1, start, _TWSE_MS)
    assert days[-1].close == _D("4.92")
    friday = sessions.USBOND.session(datetime.date(2026, 11, 20))
    assert friday is not None and days[-1].start_ms == friday.open_ms
    for candle in days:
        contract_validate("market.schema.json", "Candle", candle.to_contract())
        assert candle.open == candle.high == candle.low == candle.close
        assert candle.base_volume == 0 and candle.is_final
    weeks = await adapter.fetch_candles(_UST10, _TF.W1, start, _TWSE_MS)
    assert weeks[-1].close == _D("4.92") and weeks[-1].is_final
    months = await adapter.fetch_candles(_UST10, _TF.MN1, start, _TWSE_MS)
    assert len(months) == 1 and not months[0].is_final
    assert months[0].high == max(d.high for d in days)
    limited = await adapter.fetch_candles(_UST10, _TF.D1, 0, _TWSE_MS, 3)
    assert [d.start_ms for d in limited] == [d.start_ms for d in days[-3:]]


async def test_treasury_handles_tenor_gaps(venue_api: respx.Router) -> None:
    """Tenors absent from a year (or blank) produce no bars."""
    del venue_api
    adapter = _adapter(
        instruments.Venue.TREASURY, clock_lib.ManualClock(_TWSE_MS)
    )
    year = (
        int(
            datetime.datetime(2002, 1, 1, tzinfo=datetime.UTC).timestamp()
            * 1000
        ),
        int(
            datetime.datetime(2003, 1, 1, tzinfo=datetime.UTC).timestamp()
            * 1000
        ),
    )
    thirty = await adapter.fetch_candles(
        instruments.InstrumentId.parse("treasury:rate:UST30Y"), _TF.D1, *year
    )
    gap = sessions.USBOND.session(datetime.date(2002, 2, 19))
    assert gap is not None
    assert thirty and all(c.start_ms < gap.open_ms for c in thirty)
    one = await adapter.fetch_candles(
        instruments.InstrumentId.parse("treasury:rate:UST1M"),
        _TF.D1,
        int(
            datetime.datetime(1990, 1, 1, tzinfo=datetime.UTC).timestamp()
            * 1000
        ),
        int(
            datetime.datetime(1991, 1, 1, tzinfo=datetime.UTC).timestamp()
            * 1000
        ),
    )
    assert one == []


async def test_treasury_snapshots_and_unsupported(
    venue_api: respx.Router, contract_validate: Validate
) -> None:
    """Snapshots carry the newest yield; books and streams are refused."""
    del venue_api
    adapter = _adapter(
        instruments.Venue.TREASURY, clock_lib.ManualClock(_TWSE_MS)
    )
    snapshot = await adapter.fetch_snapshot(_UST10)
    assert snapshot.last == _D("4.92")
    rows = await adapter.fetch_snapshots()
    assert len(rows) == 19
    for row in rows:
        contract_validate(
            "market.schema.json",
            "MarketSnapshot",
            row.to_contract(_TWSE_MS, 30_000),
        )
    with pytest.raises(errors.UnsupportedError):
        await adapter.fetch_order_book(_UST10, 5)
    with pytest.raises(errors.UnsupportedError):
        adapter.stream_candles(_UST10, _TF.D1)
    with pytest.raises(errors.UnsupportedError):
        await adapter.fetch_candles(_UST10, _TF.H1, 0, 1)
    with pytest.raises(errors.NotFoundError):
        await adapter.fetch_snapshot(
            instruments.InstrumentId.parse("treasury:rate:UST99Y")
        )
