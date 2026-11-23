"""US stocks and ETFs listed on Nasdaq, NYSE and Cboe BZX, via Alpaca.

Instruments come from the official Nasdaq Trader symbol directory (free, no
key; ``nasdaqlisted.txt`` and ``otherlisted.txt``, verified 2026-11-23):
pipe-separated with a ``File Creation Time`` footer, a ``Test Issue`` flag
and an ``ETF`` flag. ``otherlisted`` exchange codes: ``N`` NYSE, ``P`` NYSE
Arca, ``A`` NYSE American (all venue ``nyse``) and ``Z`` Cboe BZX (venue
``cboe``, e.g. GOVT and many other ETFs). ``V`` IEX, ``M`` NYSE Texas and
``F`` TXSE listings are skipped (on 2026-11-23 IEX and NYSE Texas listed
only test issues).
Class shares are ``BRK.B`` in both files and in Alpaca; preferreds
(``ABR$D``), units (``.U``), warrants (``.W``) and rights (``.R``) of
``otherlisted`` are skipped because their Alpaca symbol form is unverified.

Market data needs the owner's Alpaca key (documented, not verifiable on this
machine without one; fixtures follow the documented shapes):

* REST ``https://data.alpaca.markets/v2/stocks/{symbol}/bars|trades|quotes``
  and ``/v2/stocks/snapshots``, headers ``APCA-API-KEY-ID`` and
  ``APCA-API-SECRET-KEY``, ``limit`` up to 10000 with ``next_page_token``.
  The free plan allows 200 requests per minute and SIP history except the
  latest 15 minutes; historical ``feed`` is ``iex`` or ``sip`` (the latest
  and snapshot endpoints also accept ``delayed_sip``).
* WebSocket ``wss://stream.data.alpaca.markets/v2/{iex|sip|delayed_sip}``:
  ``[{"T":"success","msg":"connected"}]``, then ``{"action":"auth",...}``
  within 10 s, then ``subscribe`` messages. The free plan allows one
  connection per endpoint (error 406 otherwise) and 30 symbols, so one
  multiplexed connection (``AlpacaStream``) serves every subscription.
* Minute bars (``b``) arrive right after each minute with the previous
  minute's trades (extended hours included); ``updatedBars`` (``u``) revise
  them after late trades; daily bars (``d``) carry the whole New York day.
  Alpaca's ``1Hour`` bars are clock aligned, so 1h and other intraday bars
  are aggregated from session minute bars on the XNYS grid; daily bars are
  re-stamped from New York midnight to the XNYS session.
* Quote sizes are in shares (round lots before 2025-11-03, per the REST
  reference).

Closing prints. The closing cross is stamped just after the close, so
Alpaca puts it in the minute after the session (16:00, or 13:00 on an
early close), together with post-market trades. On the consolidated feeds
(``sip``, ``delayed_sip``) each session's closing window ``[close,
close + 60 s)`` is read once per symbol from the historical trades endpoint
(one request of up to 10,000 trades, ``sort=asc``; the cross prints at the
start of the window) and its condition-``6`` prints fold into the session's
last minute bar (``us_trade_bars.with_closing_prints``), for history, polls
and the live stream alike; 1h/4h bars aggregate the folded minute. The
prints are cached per symbol and session (``AlpacaSource.closing_prints``).
A history fetch sends at most ``CLOSING_REQUESTS_PER_FETCH`` such requests
(newest sessions first) and all fetches and streams together at most
``CLOSING_REQUESTS_PER_MINUTE`` per rolling minute, inside Alpaca's 200
requests per minute. A session whose prints are not known yet (its window
has not passed, or the budget is spent) keeps its last minute bar open and
that minute out of coverage, so a later fetch completes it. The ``iex``
feed carries no closing cross for these listings and reads nothing.
"""

from __future__ import annotations

import asyncio
import collections
import contextlib
import dataclasses
import datetime
import decimal
import enum
import json
import logging
import os
import random
import re
import zoneinfo
from collections.abc import AsyncGenerator
from collections.abc import AsyncIterator
from collections.abc import Awaitable
from collections.abc import Callable
from collections.abc import Iterable
from collections.abc import Mapping
from collections.abc import Sequence
from typing import Any

import websockets

from joseph.domain import clock as clock_lib
from joseph.domain import decimals
from joseph.domain import errors
from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import quality
from joseph.domain import sessions
from joseph.domain import timeframes
from joseph.market_data.adapters import base
from joseph.market_data.adapters import http
from joseph.market_data.adapters import ws
from joseph.market_data.domain import candles as candle_rules
from joseph.market_data.domain import capabilities
from joseph.market_data.domain import classification
from joseph.market_data.domain import health
from joseph.market_data.domain import session_bars
from joseph.market_data.domain import us_trade_bars

_LOG = logging.getLogger(__name__)

DATA_URL = "https://data.alpaca.markets"
STREAM_URL = "wss://stream.data.alpaca.markets/v2/{feed}"
SYMDIR_URL = "https://www.nasdaqtrader.com"
NASDAQ_LISTED = "/dynamic/SymDir/nasdaqlisted.txt"
OTHER_LISTED = "/dynamic/SymDir/otherlisted.txt"
DOCS_URL = "https://docs.alpaca.markets/docs/about-market-data-api"
SOURCE = "alpaca"
_NY = zoneinfo.ZoneInfo("America/New_York")
_M1 = timeframes.Timeframe.M1
_D1 = timeframes.Timeframe.D1
_PERIODIC = (timeframes.Timeframe.W1, timeframes.Timeframe.MN1)
_INTRADAY = tuple(
    tf for tf in timeframes.MINUTE_AND_ABOVE if tf.duration_ms < _D1.duration_ms
)
_TF = (*timeframes.SECONDS, *timeframes.MINUTE_AND_ABOVE)
_PAGE_LIMIT = 10_000
MAX_PAGES = 20
"""REST pages per call (newest data is kept when the cap truncates)."""
_BAR_SETTLE_MS = 60_000
"""Late trades revise a minute bar for about 30 s; older bars are final."""
_DELAY_MS = 900_000
CLOSING_REQUESTS_PER_FETCH = 20
"""Closing-window requests one history fetch may send (newest first)."""
CLOSING_REQUESTS_PER_MINUTE = 60
"""Closing-window requests per rolling minute, all symbols (of 200/min)."""
_CLOSING_CACHE_SESSIONS = 100_000
"""Symbol sessions whose closing prints are kept (oldest dropped)."""
_CLOSING_RETRY_MS = 15_000
"""A stream retries an unanswered closing window after this long."""
VENUES = (
    instruments.Venue.NASDAQ,
    instruments.Venue.NYSE,
    instruments.Venue.CBOE,
)
"""US exchange venues served from the directory and Alpaca."""
_EXCHANGES: dict[str, tuple[instruments.Venue, str]] = {
    "N": (instruments.Venue.NYSE, "NYSE"),
    "P": (instruments.Venue.NYSE, "NYSE Arca"),
    "A": (instruments.Venue.NYSE, "NYSE American"),
    "Z": (instruments.Venue.CBOE, "Cboe BZX"),
}
"""``otherlisted.txt`` exchange codes served: venue and listing exchange."""
_DISPLAY_NAMES = {
    instruments.Venue.NASDAQ: "Nasdaq",
    instruments.Venue.NYSE: "NYSE",
    instruments.Venue.CBOE: "Cboe BZX",
}
_SYMBOL = re.compile(r"^[A-Z]{1,5}(\.[A-Z])?$")
_SKIPPED_CLASSES = frozenset("UWR")
BOND_ETFS = frozenset(
    {
        "TLT", "IEF", "SHY", "AGG", "BND", "LQD", "HYG", "TIP", "GOVT",
        "MBB", "EMB", "VCIT", "VCSH", "BIL", "SGOV", "JNK", "MUB",
    }
)  # fmt: skip
TICK_LADDER = (
    instruments.TickStep(decimal.Decimal("0"), decimal.Decimal("0.0001")),
    instruments.TickStep(decimal.Decimal("1"), decimal.Decimal("0.01")),
)
"""Reg NMS Rule 612 increments: 0.0001 below $1, else 0.01."""
_HALT_CODES = frozenset({"H", "P", "Q", "2"})
_RESUME_CODES = frozenset({"T", "3"})
Sleep = Callable[[float], Awaitable[None]]


class Feed(enum.StrEnum):
    """Alpaca stock data feed (``JOSEPH_ALPACA_FEED``)."""

    IEX = "iex"
    """Real time, IEX trades and quotes only (free)."""
    SIP = "sip"
    """Real-time consolidated tape (paid)."""
    DELAYED_SIP = "delayed_sip"
    """Consolidated tape delayed at least 15 minutes (free)."""


@dataclasses.dataclass(frozen=True)
class AlpacaConfig:
    """Alpaca credentials and feed choice (secrets are redacted).

    Attributes:
        key_id: API key id, ``None`` when not configured.
        secret_key: API secret, ``None`` when not configured.
        feed: Data feed.
        seconds_history_ms: Longest window of seconds bars built on demand
            from historical trades and quotes.
    """

    key_id: str | None = None
    secret_key: str | None = None
    feed: Feed = Feed.IEX
    seconds_history_ms: int = 30 * 60_000

    def __repr__(self) -> str:
        """Returns a representation without the credentials."""
        return (
            f"AlpacaConfig(key_id={'***' if self.key_id else None}, "
            f"secret_key={'***' if self.secret_key else None}, "
            f"feed={self.feed.value!r}, "
            f"seconds_history_ms={self.seconds_history_ms})"
        )

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> AlpacaConfig:
        """Reads ``JOSEPH_ALPACA_*`` variables.

        Args:
            env: Mapping to read (defaults to ``os.environ``).

        Returns:
            Configuration.

        Raises:
            ValueError: For an unknown feed.
        """
        source = os.environ if env is None else env
        return cls(
            key_id=source.get("JOSEPH_ALPACA_KEY_ID") or None,
            secret_key=source.get("JOSEPH_ALPACA_SECRET_KEY") or None,
            feed=Feed(source.get("JOSEPH_ALPACA_FEED", "iex").strip().lower()),
            seconds_history_ms=int(
                source.get("JOSEPH_ALPACA_SECONDS_HISTORY_MS", str(30 * 60_000))
            ),
        )

    @property
    def configured(self) -> bool:
        """Returns True when both credentials are set."""
        return bool(self.key_id and self.secret_key)

    @property
    def rest_feed(self) -> str:
        """Returns the historical ``feed`` parameter (``iex`` or ``sip``)."""
        return "iex" if self.feed is Feed.IEX else "sip"

    @property
    def delay_ms(self) -> int:
        """Returns how far behind real time the entitled data is."""
        return _DELAY_MS if self.feed is Feed.DELAYED_SIP else 0

    @property
    def closing_prints(self) -> bool:
        """Returns whether minute bars get the closing prints folded in.

        Only the consolidated feeds carry the listing exchange's closing
        cross; IEX runs none for Nasdaq, NYSE or Cboe listings.
        """
        return self.feed is not Feed.IEX

    def headers(self) -> dict[str, str]:
        """Returns the authentication headers.

        Returns:
            Headers (empty without credentials).
        """
        if not self.configured:
            return {}
        assert self.key_id is not None and self.secret_key is not None
        return {
            "APCA-API-KEY-ID": self.key_id,
            "APCA-API-SECRET-KEY": self.secret_key,
        }


def rfc3339_ms(text: str) -> int:
    """Parses an RFC-3339 timestamp with up to nanosecond precision.

    Args:
        text: E.g. ``2022-01-03T09:00:00.086175744Z``.

    Returns:
        UTC epoch milliseconds (truncated).

    Raises:
        ValueError: For malformed text.
    """
    match = re.match(
        r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(\.(\d+))?(Z|[+-]\d{2}:\d{2})$",
        text.strip(),
    )
    if match is None:
        raise ValueError(f"not an RFC-3339 timestamp: {text!r}")
    zone = "+00:00" if match.group(4) == "Z" else match.group(4)
    moment = datetime.datetime.fromisoformat(match.group(1) + zone)
    millis = int((match.group(3) or "0")[:3].ljust(3, "0"))
    return int(moment.timestamp()) * 1000 + millis


def rfc3339(ts_ms: int) -> str:
    """Formats epoch milliseconds as RFC-3339 UTC.

    Args:
        ts_ms: UTC epoch milliseconds.

    Returns:
        Text such as ``2026-11-20T14:30:00.000Z``.
    """
    moment = datetime.datetime.fromtimestamp(ts_ms / 1000, datetime.UTC)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{ts_ms % 1000:03d}Z"


def ny_date(ts_ms: int) -> datetime.date:
    """Returns the New York date of an instant.

    Args:
        ts_ms: UTC epoch milliseconds.

    Returns:
        Local date.
    """
    return datetime.datetime.fromtimestamp(ts_ms / 1000, _NY).date()


def alpaca_symbol(raw: str) -> str | None:
    """Maps a directory symbol to Joseph's id form (also Alpaca's).

    Args:
        raw: Directory symbol (``AAPL``, ``BRK.B``, ``BRK-B``).

    Returns:
        ``AAPL`` or ``BRK.B``; ``None`` for preferreds, units, warrants,
        rights and other forms without a verified Alpaca symbol.
    """
    symbol = raw.strip().upper().replace("-", ".")
    if not _SYMBOL.match(symbol):
        return None
    if "." in symbol and symbol[-1] in _SKIPPED_CLASSES:
        return None
    return symbol


def bond_class(symbol: str, name: str, etf: bool) -> instruments.AssetClass:
    """Returns BOND for known bond ETFs and ETFs named Bond/Treasury.

    Args:
        symbol: Symbol.
        name: Security name.
        etf: ETF flag.

    Returns:
        Asset class.
    """
    if symbol in BOND_ETFS:
        return instruments.AssetClass.BOND
    if etf and re.search(r"\b(bond|treasury)\b", name, re.IGNORECASE):
        return instruments.AssetClass.BOND
    return instruments.AssetClass.EQUITY


def _instrument(
    venue: instruments.Venue,
    *,
    symbol: str,
    name: str,
    etf: bool,
    exchange: str,
    now_ms: int,
) -> instruments.Instrument:
    """Builds one US listing.

    Args:
        venue: ``nasdaq``, ``nyse`` or ``cboe``.
        symbol: Joseph/Alpaca symbol.
        name: Security name.
        etf: ETF flag.
        exchange: Listing exchange name.
        now_ms: Fetch time.

    Returns:
        Instrument.
    """
    market_type = (
        instruments.MarketType.ETF if etf else instruments.MarketType.STOCK
    )
    return instruments.Instrument(
        key=instruments.InstrumentKey(
            instrument_id=instruments.InstrumentId(venue, market_type, symbol),
            canonical_base=symbol,
            quote="USD",
            settlement="USD",
        ),
        display_symbol=symbol,
        tick_size=decimal.Decimal("0.01"),
        lot_size=decimals.ONE,
        min_qty=decimals.ONE,
        contract_multiplier=decimals.ONE,
        linear=True,
        status=instruments.InstrumentStatus.TRADING,
        asset_class=bond_class(symbol, name, etf),
        source="nasdaqtrader.symdir",
        as_of_ms=now_ms,
        listing_exchange=exchange,
        tick_ladder=TICK_LADDER,
        name=name.strip() or None,
    )


def _rows(text: str) -> list[dict[str, str]]:
    """Parses a pipe-separated directory file (footer dropped).

    Args:
        text: File body.

    Returns:
        Rows keyed by header.
    """
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return []
    header = lines[0].split("|")
    return [
        dict(zip(header, line.split("|"), strict=False))
        for line in lines[1:]
        if not line.startswith("File Creation Time")
    ]


def parse_directory(
    nasdaq_text: str, other_text: str, now_ms: int
) -> dict[instruments.Venue, list[instruments.Instrument]]:
    """Builds Nasdaq, NYSE-group and Cboe BZX listings from the directory.

    Args:
        nasdaq_text: ``nasdaqlisted.txt``.
        other_text: ``otherlisted.txt``.
        now_ms: Fetch time.

    Returns:
        Instruments per venue (test issues and other exchanges skipped).
    """
    out: dict[instruments.Venue, list[instruments.Instrument]] = {
        venue: [] for venue in VENUES
    }
    for row in _rows(nasdaq_text):
        symbol = alpaca_symbol(row.get("Symbol", ""))
        if symbol is None or row.get("Test Issue") != "N":
            continue
        out[instruments.Venue.NASDAQ].append(
            _instrument(
                instruments.Venue.NASDAQ,
                symbol=symbol,
                name=row.get("Security Name", ""),
                etf=row.get("ETF") == "Y",
                exchange="Nasdaq",
                now_ms=now_ms,
            )
        )
    for row in _rows(other_text):
        listing = _EXCHANGES.get(row.get("Exchange", ""))
        symbol = alpaca_symbol(row.get("ACT Symbol", ""))
        if listing is None or symbol is None or row.get("Test Issue") != "N":
            continue
        venue, exchange = listing
        out[venue].append(
            _instrument(
                venue,
                symbol=symbol,
                name=row.get("Security Name", ""),
                etf=row.get("ETF") == "Y",
                exchange=exchange,
                now_ms=now_ms,
            )
        )
    return out


def _bar(
    row: Mapping[str, Any],
    instrument_id: instruments.InstrumentId,
    timeframe: timeframes.Timeframe,
    start_ms: int,
    now_ms: int,
    *,
    is_final: bool,
    source: str,
) -> market.Candle:
    """Converts an Alpaca bar object ``{t,o,h,l,c,v,n,vw}``.

    Args:
        row: Bar.
        instrument_id: Instrument.
        timeframe: Resolution.
        start_ms: Session-aligned start.
        now_ms: Receipt time.
        is_final: Finality.
        source: Provenance label.

    Returns:
        Candle.
    """
    return session_bars.candle(
        instrument_id=instrument_id,
        timeframe=timeframe,
        start_ms=start_ms,
        open_=base.dec(row["o"]),
        high=base.dec(row["h"]),
        low=base.dec(row["l"]),
        close=base.dec(row["c"]),
        base_volume=base.dec(row["v"]),
        now_ms=now_ms,
        source=source,
        is_final=is_final,
        trade_count=int(row["n"]) if row.get("n") is not None else None,
    )


def minute_bar(
    row: Mapping[str, Any],
    instrument_id: instruments.InstrumentId,
    now_ms: int,
    *,
    settled_before_ms: int,
    source: str,
) -> market.Candle | None:
    """Converts a minute bar inside the XNYS session.

    Args:
        row: Alpaca bar.
        instrument_id: Instrument.
        now_ms: Receipt time.
        settled_before_ms: Bars ending by then are final.
        source: Provenance label.

    Returns:
        Candle, or ``None`` for extended-hours minutes.
    """
    start = rfc3339_ms(str(row["t"]))
    calendar = sessions.XNYS
    if not calendar.is_open(start) or not calendar.is_aligned(_M1, start):
        return None
    end = calendar.end_of(_M1, start)
    return _bar(
        row,
        instrument_id,
        _M1,
        start,
        now_ms,
        is_final=end <= settled_before_ms,
        source=source,
    )


def daily_bar(
    row: Mapping[str, Any],
    instrument_id: instruments.InstrumentId,
    now_ms: int,
    *,
    delay_ms: int,
    source: str,
) -> market.Candle | None:
    """Re-stamps an Alpaca daily bar (New York midnight) to its session.

    The bar aggregates the whole New York day, so it is final only after
    that day has ended (extended-hours trades update its volume).

    Args:
        row: Alpaca bar.
        instrument_id: Instrument.
        now_ms: Receipt time.
        delay_ms: Entitlement delay.
        source: Provenance label.

    Returns:
        Candle, or ``None`` when the day is not an XNYS session.
    """
    day = ny_date(rfc3339_ms(str(row["t"])) + 3_600_000)
    session = sessions.XNYS.session(day)
    if session is None:
        return None
    day_end = datetime.datetime.combine(
        day + datetime.timedelta(days=1), datetime.time(0), tzinfo=_NY
    )
    final = int(day_end.timestamp() * 1000) + delay_ms <= now_ms
    return _bar(
        row,
        instrument_id,
        _D1,
        session.open_ms,
        now_ms,
        is_final=final,
        source=source,
    )


def _proven(
    rows: Sequence[Mapping[str, Any]], settled_ms: int, complete: bool
) -> int:
    """Returns how far a REST bar range is complete.

    Args:
        rows: Raw Alpaca bars, ascending.
        settled_ms: Time before which delivered bars are final.
        complete: Whether every page was read.

    Returns:
        ``settled_ms``, or the last bar read when the page cap cut the
        range (bars after it were not delivered).
    """
    if complete or not rows:
        return settled_ms
    return min(settled_ms, rfc3339_ms(str(rows[-1]["t"])))


def parse_trade(
    row: Mapping[str, Any], side: market.AggressorSide
) -> us_trade_bars.TapeTrade:
    """Converts an Alpaca trade ``{i,x,p,s,c,t,z}``.

    Args:
        row: REST trade or ``t`` stream message.
        side: Classified side.

    Returns:
        Tape trade (id = exchange code + trade id).
    """
    return us_trade_bars.TapeTrade(
        trade_id=f"{row.get('x', '')}:{row['i']}",
        ts_ms=rfc3339_ms(str(row["t"])),
        price=base.dec(row["p"]),
        size=base.dec(row["s"]),
        conditions=tuple(str(c) for c in row.get("c") or ()),
        tape=str(row["z"]) if row.get("z") is not None else None,
        side=side,
    )


def to_market_trade(
    trade: us_trade_bars.TapeTrade,
    instrument_id: instruments.InstrumentId,
    now_ms: int,
) -> market.Trade:
    """Converts a tape trade to a public trade (no venue aggressor side).

    Args:
        trade: Tape trade.
        instrument_id: Instrument.
        now_ms: Receipt time.

    Returns:
        Trade with ``UNKNOWN`` aggressor (the tape publishes none).
    """
    return market.Trade(
        instrument_id=instrument_id,
        trade_id=trade.trade_id,
        venue_event_ts_ms=trade.ts_ms,
        received_at_ms=now_ms,
        price=trade.price,
        base_quantity=trade.size,
        aggressor_side=market.AggressorSide.UNKNOWN,
        is_aggregated=False,
    )


def changed_candle(
    previous: market.Candle | None, candle: market.Candle
) -> bool:
    """Checks whether a candle differs from the last one emitted.

    Aggregates are rebuilt on every event with a new receipt time, so the
    receipt time is ignored.

    Args:
        previous: Last emitted candle of the bar, if any.
        candle: New candle.

    Returns:
        True when any value or the finality changed.
    """
    if previous is None:
        return True
    return dataclasses.replace(previous, received_at_ms=0) != (
        dataclasses.replace(candle, received_at_ms=0)
    )


class QuoteBook:
    """Recent NBBO quotes of one symbol for Lee-Ready classification."""

    def __init__(self, capacity: int = 4_096) -> None:
        """Initializes an empty book.

        Args:
            capacity: Quotes kept.
        """
        self._times: list[int] = []
        self._quotes: list[
            tuple[decimal.Decimal | None, decimal.Decimal | None]
        ] = []
        self._capacity = capacity

    def add(self, row: Mapping[str, Any]) -> None:
        """Records a quote ``{bp, ap, t}`` (0 means no bid/ask).

        Args:
            row: Quote.
        """
        ts = rfc3339_ms(str(row["t"]))
        bid = base.opt_dec(row.get("bp")) or None
        ask = base.opt_dec(row.get("ap")) or None
        index = len(self._times)
        while index and self._times[index - 1] > ts:
            index -= 1
        self._times.insert(index, ts)
        self._quotes.insert(index, (bid, ask))
        if len(self._times) > self._capacity:
            del self._times[0]
            del self._quotes[0]

    def prevailing(
        self, ts_ms: int
    ) -> tuple[decimal.Decimal | None, decimal.Decimal | None]:
        """Returns the newest quote stamped strictly before ``ts_ms``.

        Args:
            ts_ms: Trade time.

        Returns:
            ``(bid, ask)``, ``(None, None)`` without one.
        """
        lo, hi = 0, len(self._times)
        while lo < hi:
            mid = (lo + hi) // 2
            if self._times[mid] < ts_ms:
                lo = mid + 1
            else:
                hi = mid
        return self._quotes[lo - 1] if lo else (None, None)


def classify(
    rows: Iterable[Mapping[str, Any]],
    quotes: QuoteBook,
    classifier: classification.LeeReadyClassifier,
) -> list[us_trade_bars.TapeTrade]:
    """Classifies trades with Lee-Ready against the prevailing quote.

    Only price-updating trades feed the tick test (odd lots and average
    price trades are classified but do not move the reference price).

    Args:
        rows: Alpaca trades in time order.
        quotes: Recent quotes.
        classifier: Per-symbol classifier.

    Returns:
        Classified tape trades.
    """
    out = []
    for row in rows:
        price = base.dec(row["p"])
        ts = rfc3339_ms(str(row["t"]))
        bid, ask = quotes.prevailing(ts)
        rule = us_trade_bars.update_rule(
            [str(c) for c in row.get("c") or ()], row.get("z")
        )
        if rule.high_low:
            side = classifier.classify(price, bid, ask)
        else:
            side = classification.lee_ready(
                price, bid, ask, classifier.ticks.reference(price)
            )
        out.append(parse_trade(row, side))
    return out


@dataclasses.dataclass(slots=True)
class _Sub:
    """One stream subscriber.

    Attributes:
        symbol: Alpaca symbol.
        channels: Channels (``trades``, ``quotes``, ``bars``, ...).
        queue: Delivered events.
    """

    symbol: str
    channels: frozenset[str]
    queue: asyncio.Queue[tuple[str, Any]]


_TYPE_CHANNEL = {
    "t": "trades",
    "q": "quotes",
    "b": "bars",
    "u": "updatedBars",
    "d": "dailyBars",
    "s": "statuses",
}


class AlpacaStream:
    """One authenticated, multiplexed Alpaca stream connection.

    Subscribers receive ``(kind, payload)`` events: ``("msg", message)``
    for data messages of their symbol and channels, ``("frame", now_ms)``
    after every received frame (delivery proof), ``("reset", now_ms)``
    after a reconnect (data may have been missed) and ``("error", err)``
    when the stream gave up.
    """

    def __init__(
        self,
        config: AlpacaConfig,
        clock: clock_lib.Clock,
        connect: ws.ConnectFn = ws.default_connect,
        *,
        sleep: Sleep = asyncio.sleep,
        max_failures: int = 5,
    ) -> None:
        """Initializes the stream (connects on the first subscription).

        Args:
            config: Credentials and feed.
            clock: Time source.
            connect: WebSocket connector.
            sleep: Sleep function (backoff).
            max_failures: Consecutive failures before giving up.
        """
        self._config = config
        self._clock = clock
        self._connect = connect
        self._sleep = sleep
        self._max_failures = max_failures
        self._subs: list[_Sub] = []
        self._connection: ws.WsConnection | None = None
        self._task: asyncio.Task[None] | None = None
        self.halted: dict[str, bool] = {}

    @property
    def url(self) -> str:
        """Returns the feed URL."""
        return STREAM_URL.format(feed=self._config.feed.value)

    def _wanted(self) -> dict[str, set[str]]:
        """Returns the symbols wanted per channel.

        Returns:
            Channel to symbols.
        """
        wanted: dict[str, set[str]] = {}
        for sub in self._subs:
            for channel in sub.channels:
                wanted.setdefault(channel, set()).add(sub.symbol)
        return wanted

    async def subscribe(self, symbol: str, channels: Iterable[str]) -> _Sub:
        """Adds a subscriber (connecting or extending the subscription).

        Args:
            symbol: Alpaca symbol.
            channels: Channels.

        Returns:
            Subscription handle.
        """
        sub = _Sub(symbol, frozenset(channels), asyncio.Queue())
        self._subs.append(sub)
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())
        elif self._connection is not None:
            await self._send_subscription(
                "subscribe", {c: [symbol] for c in sub.channels}
            )
        return sub

    async def unsubscribe(self, sub: _Sub) -> None:
        """Removes a subscriber (and its symbols nobody else needs).

        Args:
            sub: Subscription handle.
        """
        if sub in self._subs:
            self._subs.remove(sub)
        wanted = self._wanted()
        unused = {
            c: [sub.symbol]
            for c in sub.channels
            if sub.symbol not in wanted.get(c, set())
        }
        if not self._subs:
            await self.close()
            return
        if unused and self._connection is not None:
            await self._send_subscription("unsubscribe", unused)

    async def _send_subscription(
        self, action: str, channels: Mapping[str, Sequence[str]]
    ) -> None:
        """Sends a subscribe or unsubscribe message.

        Args:
            action: ``subscribe`` or ``unsubscribe``.
            channels: Channel to symbols.
        """
        if self._connection is None or not channels:
            return
        message = {
            "action": action,
            **{c: sorted(s) for c, s in channels.items()},
        }
        with contextlib.suppress(OSError, websockets.WebSocketException):
            await self._connection.send(json.dumps(message))

    def _broadcast(self, kind: str, payload: Any) -> None:
        """Delivers an event to every subscriber.

        Args:
            kind: Event kind.
            payload: Payload.
        """
        for sub in self._subs:
            sub.queue.put_nowait((kind, payload))

    def _dispatch(self, message: Mapping[str, Any]) -> None:
        """Routes one data message.

        Args:
            message: Message with ``T`` and ``S``.
        """
        kind = str(message.get("T", ""))
        symbol = str(message.get("S", ""))
        if kind == "s":
            code = str(message.get("sc", ""))
            if code in _HALT_CODES:
                self.halted[symbol] = True
            elif code in _RESUME_CODES:
                self.halted[symbol] = False
        channel = _TYPE_CHANNEL.get(kind)
        if kind in ("c", "x"):
            channel = "trades"
        if channel is None:
            return
        for sub in self._subs:
            if sub.symbol == symbol and channel in sub.channels:
                sub.queue.put_nowait(("msg", message))

    async def _handshake(
        self, connection: ws.WsConnection
    ) -> dict[str, set[str]]:
        """Authenticates and subscribes everything wanted.

        Args:
            connection: Open connection.

        Returns:
            The channels and symbols subscribed.

        Raises:
            errors.VenueUnavailableError: On an authentication or
                subscription error message.
        """
        config = self._config
        await connection.send(
            json.dumps(
                {
                    "action": "auth",
                    "key": config.key_id,
                    "secret": config.secret_key,
                }
            )
        )
        for _ in range(3):
            frame = await asyncio.wait_for(connection.recv(), 10)
            for message in json.loads(frame):
                if message.get("T") == "error":
                    raise errors.VenueUnavailableError(
                        f"alpaca stream error {message.get('code')}: "
                        f"{message.get('msg')}",
                        retryable=message.get("code") not in (402, 406, 409),
                    )
                if message.get("msg") == "authenticated":
                    wanted = self._wanted()
                    await connection.send(
                        json.dumps(
                            {
                                "action": "subscribe",
                                **{c: sorted(s) for c, s in wanted.items()},
                            }
                        )
                    )
                    return wanted
        raise errors.VenueUnavailableError("alpaca stream did not authenticate")

    async def _run(self) -> None:
        """Connects, authenticates and dispatches until no one listens."""
        failures = 0
        connected_before = False
        while self._subs:
            try:
                async with self._connect(self.url) as connection:
                    sent = await self._handshake(connection)
                    self._connection = connection
                    # Subscribers added during the handshake.
                    await self._send_subscription(
                        "subscribe",
                        {
                            c: sorted(s - sent.get(c, set()))
                            for c, s in self._wanted().items()
                            if s - sent.get(c, set())
                        },
                    )
                    if connected_before:
                        self._broadcast("reset", self._clock.now_ms())
                    connected_before = True
                    while True:
                        frame = await asyncio.wait_for(connection.recv(), 60)
                        failures = 0
                        try:
                            messages = json.loads(frame)
                        except ValueError:
                            continue
                        for message in (
                            messages if isinstance(messages, list) else []
                        ):
                            if message.get("T") == "error":
                                _LOG.warning(
                                    "alpaca stream error %s: %s",
                                    message.get("code"),
                                    message.get("msg"),
                                )
                                continue
                            self._dispatch(message)
                        self._broadcast("frame", self._clock.now_ms())
            except errors.VenueUnavailableError as err:
                self._connection = None
                if not err.retryable:
                    self._broadcast("error", err)
                    return
                failures += 1
            except (
                OSError,
                TimeoutError,
                websockets.WebSocketException,
            ) as err:
                self._connection = None
                failures += 1
                _LOG.warning(
                    "alpaca stream failed (%s), attempt %d",
                    type(err).__name__,
                    failures,
                )
            if failures >= self._max_failures:
                self._broadcast(
                    "error",
                    errors.VenueUnavailableError(
                        f"alpaca stream unavailable after {failures} attempts"
                    ),
                )
                return
            await self._sleep(min(30.0, 0.5 * 2**failures) * random.random())  # noqa: S311

    async def close(self) -> None:
        """Stops the connection task."""
        task, self._task = self._task, None
        self._connection = None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task


class RequestBudget:
    """Requests allowed per rolling minute (a hard cap, never a wait).

    Attributes:
        per_minute: Requests allowed in any 60 s.
    """

    def __init__(self, clock: clock_lib.Clock, per_minute: int) -> None:
        """Initializes an unused budget.

        Args:
            clock: Time source.
            per_minute: Requests allowed in any 60 s.
        """
        self.per_minute = per_minute
        self._clock = clock
        self._sent: collections.deque[int] = collections.deque()

    def take(self) -> bool:
        """Spends one request if the last minute leaves room.

        Returns:
            True when the request may be sent.
        """
        now = self._clock.now_ms()
        while self._sent and self._sent[0] <= now - 60_000:
            self._sent.popleft()
        if len(self._sent) >= self.per_minute:
            return False
        self._sent.append(now)
        return True


def unsettled(
    rows: Iterable[market.Candle], holes: Sequence[candle_rules.Span]
) -> list[market.Candle]:
    """Reopens bars overlapping a slot whose data is still incomplete.

    Args:
        rows: Bars.
        holes: Slots still awaiting data (e.g. a pending closing print).

    Returns:
        The bars, those overlapping a hole made non-final.
    """
    return [
        dataclasses.replace(bar, is_final=False)
        if bar.is_final
        and any(low < bar.end_ms and bar.start_ms < high for low, high in holes)
        else bar
        for bar in rows
    ]


class AlpacaSource:
    """State shared by the US exchange adapters (one key, one budget).

    Attributes:
        config: Credentials and feed.
        data: Client for ``DATA_URL``.
        directory_http: Client for ``SYMDIR_URL``.
        clock: Time source.
        stream: The shared stream connection.
        closing_budget: Budget of closing-window requests.
    """

    def __init__(
        self,
        config: AlpacaConfig,
        data_http: http.VenueHttp,
        directory_http: http.VenueHttp,
        clock: clock_lib.Clock,
        connect: ws.ConnectFn = ws.default_connect,
    ) -> None:
        """Initializes the source.

        Args:
            config: Credentials and feed.
            data_http: Client for ``DATA_URL`` (200 requests/min budget).
            directory_http: Client for ``SYMDIR_URL``.
            clock: Time source.
            connect: WebSocket connector.
        """
        self.config = config
        self.data = data_http
        self.directory_http = directory_http
        self.clock = clock
        self.stream = AlpacaStream(config, clock, connect)
        self.closing_budget = RequestBudget(clock, CLOSING_REQUESTS_PER_MINUTE)
        self._closing: dict[
            tuple[str, int], tuple[us_trade_bars.TapeTrade, ...]
        ] = {}
        self._directory: dict[
            instruments.Venue, list[instruments.Instrument]
        ] = {}
        self._directory_ms: int | None = None
        self._lock = asyncio.Lock()
        self._users = 0

    def acquire(self) -> None:
        """Registers one more adapter using the source."""
        self._users += 1

    async def listings(
        self, venue: instruments.Venue
    ) -> list[instruments.Instrument]:
        """Returns the directory listings of a venue (cached 6 hours).

        Args:
            venue: ``nasdaq``, ``nyse`` or ``cboe``.

        Returns:
            Instruments.
        """
        async with self._lock:
            now = self.clock.now_ms()
            if (
                self._directory_ms is None
                or now - self._directory_ms > 6 * 3_600_000
            ):
                nasdaq_text, other_text = await asyncio.gather(
                    self.directory_http.get_text(NASDAQ_LISTED),
                    self.directory_http.get_text(OTHER_LISTED),
                )
                self._directory = parse_directory(nasdaq_text, other_text, now)
                self._directory_ms = now
            return list(self._directory.get(venue, []))

    def require_keys(self, venue: instruments.Venue) -> None:
        """Fails clearly when no Alpaca key is configured.

        Args:
            venue: Requesting venue.

        Raises:
            errors.VenueUnavailableError: Without credentials.
        """
        if not self.config.configured:
            raise errors.VenueUnavailableError(
                f"{venue.value} market data comes from Alpaca: configure "
                "Alpaca keys (JOSEPH_ALPACA_KEY_ID and "
                "JOSEPH_ALPACA_SECRET_KEY)",
                retryable=False,
            )

    async def get(self, path: str, params: dict[str, Any]) -> Any:
        """Sends an authenticated data request.

        Args:
            path: Path under ``DATA_URL``.
            params: Query parameters.

        Returns:
            Parsed JSON.

        Raises:
            errors.VenueUnavailableError: With a key hint on HTTP 401/403.
        """
        try:
            return await self.data.get(
                path, params, headers=self.config.headers()
            )
        except http.HttpStatusError as err:
            if err.status_code in (401, 403):
                raise errors.VenueUnavailableError(
                    f"alpaca rejected {path} with HTTP {err.status_code}: "
                    "check the Alpaca keys and that the subscription covers "
                    f"feed {self.config.feed.value}",
                    retryable=False,
                ) from err
            raise

    async def pages(
        self, path: str, params: dict[str, Any], key: str
    ) -> tuple[list[Mapping[str, Any]], bool]:
        """Follows ``next_page_token`` up to ``MAX_PAGES``.

        Args:
            path: Path.
            params: Query parameters.
            key: Result array (``bars``, ``trades``, ``quotes``).

        Returns:
            Rows of every page read (oldest first with ``sort=asc``) and
            whether the last page was reached (False when ``MAX_PAGES``
            cut the range short).
        """
        rows: list[Mapping[str, Any]] = []
        token: str | None = None
        for _ in range(MAX_PAGES):
            query = dict(params)
            if token:
                query["page_token"] = token
            payload = await self.get(path, query)
            rows.extend(payload.get(key) or [])
            token = payload.get("next_page_token")
            if not token:
                return rows, True
        return rows, False

    async def paged(
        self, path: str, params: dict[str, Any], key: str
    ) -> list[Mapping[str, Any]]:
        """Follows ``next_page_token`` up to ``MAX_PAGES``.

        Args:
            path: Path.
            params: Query parameters.
            key: Result array (``bars``, ``trades``, ``quotes``).

        Returns:
            Rows of every page read.
        """
        rows, _ = await self.pages(path, params, key)
        return rows

    async def closing_prints(
        self, symbol: str, closes: Iterable[int], max_requests: int
    ) -> dict[int, tuple[us_trade_bars.TapeTrade, ...]]:
        """Returns the closing prints of sessions, read once and cached.

        Each uncached session costs one request for the trades of its
        closing window (``[close, close + 60 s)``, up to 10,000 trades,
        oldest first). Sessions are served newest first while
        ``max_requests`` and the shared per-minute budget allow; a failed
        request stops the reading (the session stays unknown).

        Args:
            symbol: Alpaca symbol.
            closes: Closes of sessions whose closing window has passed.
            max_requests: Requests this call may send.

        Returns:
            Prints per known close (empty when the session printed none);
            closes left unread are absent.
        """
        known: dict[int, tuple[us_trade_bars.TapeTrade, ...]] = {}
        sent = 0
        for close in sorted(set(closes), reverse=True):
            cached = self._closing.get((symbol, close))
            if cached is not None:
                known[close] = cached
                continue
            if sent >= max_requests or not self.closing_budget.take():
                continue
            sent += 1
            try:
                payload = await self.get(
                    f"/v2/stocks/{symbol}/trades",
                    {
                        "feed": self.config.rest_feed,
                        "limit": _PAGE_LIMIT,
                        "sort": "asc",
                        "start": rfc3339(close),
                        "end": rfc3339(
                            close + us_trade_bars.CLOSING_WINDOW_MS - 1
                        ),
                    },
                )
            except errors.JosephError as err:
                _LOG.warning(
                    "alpaca closing prints of %s unavailable: %s",
                    symbol,
                    err.message,
                )
                max_requests = sent
                continue
            trades = [
                parse_trade(row, market.AggressorSide.UNKNOWN)
                for row in payload.get("trades") or ()
                if not row.get("u")
            ]
            prints = us_trade_bars.closing_prints(trades, close)
            if len(self._closing) >= _CLOSING_CACHE_SESSIONS:
                del self._closing[next(iter(self._closing))]
            self._closing[(symbol, close)] = prints
            known[close] = prints
        return known

    async def close(self) -> None:
        """Closes shared resources once every adapter closed."""
        self._users -= 1
        if self._users <= 0:
            await self.stream.close()
            await self.data.close()
            await self.directory_http.close()


@dataclasses.dataclass(frozen=True, slots=True)
class _Fetched:
    """Bars of one REST range and how far they are complete.

    Attributes:
        rows: Ascending session bars (minute bars without closing prints).
        proven_ms: Bars starting before this time were all delivered and
            are final (a slot without a bar there had no trades).
        complete: False when ``MAX_PAGES`` cut the range short.
        prints: Closing prints per close of the sessions ending in the
            range (minute fetches on a consolidated feed).
        pending: Closes of sessions ending in the range whose closing
            prints are not known yet.
    """

    rows: list[market.Candle]
    proven_ms: int
    complete: bool
    prints: Mapping[int, tuple[us_trade_bars.TapeTrade, ...]] = (
        dataclasses.field(default_factory=dict)
    )
    pending: tuple[int, ...] = ()

    def holes(
        self, instrument: instruments.InstrumentId
    ) -> list[candle_rules.Span]:
        """Returns the last minutes of the pending sessions.

        Args:
            instrument: Instrument.

        Returns:
            Slots kept out of coverage until their prints are known.
        """
        return [us_trade_bars.closing_slot(instrument, c) for c in self.pending]

    def minutes(
        self, instrument: instruments.InstrumentId, now_ms: int, settled: int
    ) -> list[market.Candle]:
        """Returns the minute bars with the known closing prints folded in.

        Args:
            instrument: Instrument.
            now_ms: Receipt time.
            settled: Bars ending by then are final.

        Returns:
            Ascending minute bars (pending sessions' last minutes open).
        """
        return us_trade_bars.with_closing_prints(
            self.rows,
            instrument,
            prints=self.prints,
            pending=self.pending,
            now_ms=now_ms,
            settled_before_ms=settled,
            source=f"{SOURCE}.rest",
        )


class _LiveCloses:
    """Closing prints of the sessions a live minute stream holds.

    On a consolidated feed every session with a streamed or seeded minute
    is tracked: its last minute stays open, and out of the proven spans,
    until its closing window has passed and its prints were read (shared
    cache and budget; an unanswered window is retried). The IEX feed tracks
    nothing.
    """

    def __init__(
        self, instrument: instruments.InstrumentId, source: AlpacaSource
    ) -> None:
        """Initializes an empty tracker.

        Args:
            instrument: Streamed instrument.
            source: Shared Alpaca source.
        """
        self._instrument = instrument
        self._source = source
        self._enabled = source.config.closing_prints
        self._delay = source.config.delay_ms
        self._closes: set[int] = set()
        self._prints: dict[int, tuple[us_trade_bars.TapeTrade, ...]] = {}
        self._retry_at: dict[int, int] = {}
        self._withheld: set[int] = set()

    def note(self, start_ms: int) -> None:
        """Tracks the session of a stored session minute.

        Args:
            start_ms: Minute start.
        """
        if self._enabled:
            self._closes.add(
                sessions.XNYS.session_at_or_before(start_ms).close_ms
            )

    def seed(self, fetched: _Fetched) -> None:
        """Takes over the sessions and prints of a REST seed.

        Args:
            fetched: Seed fetch.
        """
        for bar in fetched.rows:
            self.note(bar.start_ms)
        if self._enabled:
            self._prints.update(fetched.prints)
            self._closes.update(fetched.prints.keys(), fetched.pending)

    def pending(self) -> set[int]:
        """Returns the tracked closes whose prints are not known yet."""
        return self._closes - self._prints.keys()

    def holes(self) -> list[candle_rules.Span]:
        """Returns the last minutes of sessions awaiting their prints.

        Returns:
            Slots kept open.
        """
        return [
            us_trade_bars.closing_slot(self._instrument, close)
            for close in sorted(self.pending())
        ]

    def prove(
        self, span: candle_rules.Span | None
    ) -> tuple[candle_rules.Span, ...]:
        """Removes the open slots from a span the connection proved.

        Args:
            span: Proven span, if any.

        Returns:
            The span minus the slots awaiting prints (those are proven once
            their prints are read).
        """
        if span is None:
            return ()
        holes = self.holes()
        self._withheld.update(
            close for low, close in holes if low < span[1] and span[0] < close
        )
        return tuple(candle_rules.subtract_spans([span], holes))

    async def learn(self, now_ms: int) -> tuple[candle_rules.Span, ...]:
        """Reads the closing prints of sessions whose window has passed.

        Args:
            now_ms: Receipt time.

        Returns:
            Withheld slots now complete.
        """
        horizon = (
            now_ms
            - self._delay
            - us_trade_bars.CLOSING_WINDOW_MS
            - base.TRADE_PUBLISH_LAG_MS
        )
        due = [
            close
            for close in self.pending()
            if close <= horizon and self._retry_at.get(close, 0) <= now_ms
        ]
        if not due:
            return ()
        known = await self._source.closing_prints(
            self._instrument.venue_market_id, due, CLOSING_REQUESTS_PER_FETCH
        )
        self._prints.update(known)
        for close in due:
            if close not in known:
                self._retry_at[close] = now_ms + _CLOSING_RETRY_MS
        proven = sorted(self._withheld & known.keys())
        self._withheld.difference_update(proven)
        return tuple(
            us_trade_bars.closing_slot(self._instrument, close)
            for close in proven
        )

    def fold(
        self, bars: Iterable[market.Candle], now_ms: int
    ) -> list[market.Candle]:
        """Folds the known prints into the streamed minutes.

        Args:
            bars: Streamed minute bars.
            now_ms: Receipt time.

        Returns:
            Ascending minute bars (pending last minutes not final).
        """
        return us_trade_bars.with_closing_prints(
            bars,
            self._instrument,
            prints=self._prints,
            pending=self.pending(),
            now_ms=now_ms,
            settled_before_ms=now_ms - self._delay,
            source=f"{SOURCE}.ws",
        )

    def prune(self, cutoff_ms: int) -> None:
        """Forgets sessions that closed before a time.

        Args:
            cutoff_ms: Oldest close kept.
        """
        for close in [c for c in self._closes if c < cutoff_ms]:
            self._closes.discard(close)
            self._prints.pop(close, None)
            self._retry_at.pop(close, None)
            self._withheld.discard(close)


class AlpacaAdapter(base.BaseVenueAdapter):
    """``SparseBarsPort`` for one venue (``nasdaq``, ``nyse`` or ``cboe``).

    Alpaca publishes no bar for a minute (or day) without trades, so every
    fetch and stream states the span it delivered completely.
    """

    INSTRUMENT_TTL_MS = 6 * 3_600_000
    TRADE_BUY_VOLUME_METHOD = market.BuyVolumeMethod.CLASSIFIED
    SPARSE_BARS = True

    def __init__(
        self,
        venue: instruments.Venue,
        source: AlpacaSource,
        clock: clock_lib.Clock,
        verified_at_ms: int | None = None,
    ) -> None:
        """Initializes the adapter.

        Args:
            venue: ``nasdaq``, ``nyse`` or ``cboe``.
            source: Shared Alpaca source.
            clock: Time source.
            verified_at_ms: When the public parts were verified live.
        """
        super().__init__(source.directory_http, clock)
        self.VENUE = venue
        self._source = source
        source.acquire()
        self._verified_at_ms = verified_at_ms

    @property
    def config(self) -> AlpacaConfig:
        """Returns the Alpaca configuration."""
        return self._source.config

    def capabilities(self) -> capabilities.VenueCapabilities:
        """Returns capabilities (data needs a configured key).

        Returns:
            Capabilities.
        """
        config = self.config
        name = _DISPLAY_NAMES[self.VENUE]
        return capabilities.VenueCapabilities(
            venue=self.VENUE,
            display_name=f"{name} (Alpaca {config.feed.value})",
            market_types=(
                instruments.MarketType.STOCK,
                instruments.MarketType.ETF,
            ),
            market_data=capabilities.MarketDataCapabilities(
                rest_history=True,
                native_ws_candles=True,
                native_ws_trades=True,
                trades_history=True,
                classified_delta=True,
            ),
            timeframes=_TF,
            trade_built_timeframes=timeframes.SECONDS,
            aggregated_timeframes=(
                *(tf for tf in _INTRADAY if tf is not _M1),
                *_PERIODIC,
            ),
            data_delay_ms=config.delay_ms or None,
            requires_credentials=True,
            credentials_configured=config.configured,
            notes=(
                "Instruments come from the Nasdaq Trader symbol directory "
                "(no key; verified live); prices, bars and trades need "
                "Alpaca keys (JOSEPH_ALPACA_KEY_ID, JOSEPH_ALPACA_SECRET_KEY)."
                " The Alpaca endpoints follow Alpaca's documentation and "
                "have not been verified live (no key on the build host).",
                f"Feed {config.feed.value}: "
                + {
                    Feed.IEX: "real time but IEX trades and quotes only "
                    "(a few percent of consolidated volume).",
                    Feed.SIP: "real-time consolidated tape (paid plan).",
                    Feed.DELAYED_SIP: "consolidated tape at least 15 minutes "
                    "late (free plan).",
                }[config.feed],
                "Regular-session bars on the XNYS calendar: 1m from Alpaca "
                "minute bars, 3m-12h aggregated from them, 1d re-stamped "
                "from Alpaca daily bars (whose volume includes extended "
                "hours), 1w and 1M aggregated from 1d. Bars use split "
                "adjustment. Minutes without trades have no bar; inside a "
                "fetched or streamed span they are not reported as gaps.",
                "Live minute bars arrive after each minute closes (Alpaca "
                "bars/updatedBars); there is no in-progress minute.",
                "Seconds bars are built from trades with the CTA/UTP sale "
                "condition rules (odd lots update volume only); seconds "
                "history is built on demand from REST trades and quotes, "
                "bounded to the configured window. The official closing "
                "print (sale condition 6) stamped within 60 s after the "
                "close joins the session's last seconds bar, which settles "
                "only then; other after-close trades stay out.",
                "Closing prints in minute bars: "
                + (
                    "Alpaca's minute after the close (16:00, 13:00 on early "
                    "closes) also holds post-market trades, so each "
                    "session's closing window is read once per symbol from "
                    "REST trades (one request, cached) and its condition-6 "
                    "prints fold into the session's last minute bar, hence "
                    "into 1h/4h bars: their close is the official close, "
                    "like the 1d bar's. At most "
                    f"{CLOSING_REQUESTS_PER_FETCH} such requests per fetch "
                    f"(newest sessions first) and {CLOSING_REQUESTS_PER_MINUTE}"
                    " per minute overall; until its prints are read a "
                    "session's last minute stays open and is fetched again."
                    if config.closing_prints
                    else "the IEX feed has no closing cross for these "
                    "listings, so the last minute of a session holds IEX "
                    "trades only (sip and delayed_sip fold the official "
                    "closing print in)."
                ),
                "The tape publishes no aggressor side: trades are classified "
                "with Lee-Ready against the prevailing NBBO (quote rule, "
                "tick test at the midpoint); taker_buy_method = classified. "
                "Public trades are delivered with an unknown aggressor.",
                "Trading halts and pauses from the statuses channel mark "
                "streamed instruments HALTED (status availability depends "
                "on the Alpaca plan and is unverified without a key).",
                "Free plan: 200 REST requests/min, one stream connection, "
                "30 streamed symbols; history from 2016. No order book, "
                "funding or open interest.",
                "Class shares (BRK.B) are served; preferreds, units, warrants "
                "and rights from otherlisted.txt, and listings on IEX, NYSE "
                "Texas and TXSE, are not. Cboe BZX listings (e.g. GOVT) are "
                "venue cboe.",
            ),
            docs_url=DOCS_URL,
            verified_at_ms=self._verified_at_ms,
        )

    def instrument_id(self, venue_market_id: str) -> instruments.InstrumentId:
        """Builds the canonical id of a listed symbol.

        Args:
            venue_market_id: Symbol.

        Returns:
            Canonical id (``etf`` when listed as an ETF).
        """
        found = self._instruments.get(venue_market_id)
        if found is not None:
            return found.instrument_id
        return instruments.InstrumentId(
            self.VENUE, instruments.MarketType.STOCK, venue_market_id
        )

    async def _load_instruments(
        self,
    ) -> tuple[list[instruments.Instrument], dict[str, Mapping[str, Any]]]:
        """Loads this venue's directory listings.

        Returns:
            Instruments and empty metadata.
        """
        return await self._source.listings(self.VENUE), {}

    async def list_instruments(self) -> list[instruments.Instrument]:
        """Lists instruments, marking streamed halts.

        Returns:
            Instruments.
        """
        rows = await super().list_instruments()
        halted = self._source.stream.halted
        return [
            dataclasses.replace(row, status=instruments.InstrumentStatus.HALTED)
            if halted.get(row.instrument_id.venue_market_id)
            else row
            for row in rows
        ]

    async def _ready(
        self, instrument: instruments.InstrumentId
    ) -> instruments.Instrument:
        """Resolves an instrument and checks the key.

        Args:
            instrument: Instrument.

        Returns:
            Instrument metadata.
        """
        listed = await self._require(instrument)
        self._source.require_keys(self.VENUE)
        return listed

    def _settled_before(self) -> int:
        """Returns the time before which REST minute bars are final."""
        return self._clock.now_ms() - self.config.delay_ms - _BAR_SETTLE_MS

    async def _closes(
        self,
        instrument: instruments.InstrumentId,
        start_ms: int,
        end_ms: int,
        max_requests: int,
    ) -> tuple[dict[int, tuple[us_trade_bars.TapeTrade, ...]], tuple[int, ...]]:
        """Reads the closing prints of the sessions ending in a range.

        Args:
            instrument: Instrument.
            start_ms: Inclusive start (sessions whose last minute starts
                earlier are left out).
            end_ms: Exclusive end (bars before it were delivered).
            max_requests: Closing-window requests allowed.

        Returns:
            Prints per known close and the pending closes (window not
            passed yet, or unread within the budget); nothing on the IEX
            feed.
        """
        if not self.config.closing_prints or end_ms <= start_ms:
            return {}, ()
        closes = [
            session.close_ms
            for session in sessions.XNYS.sessions(start_ms, end_ms)
            if start_ms
            <= us_trade_bars.closing_slot(instrument, session.close_ms)[0]
            < end_ms
        ]
        horizon = (
            self._clock.now_ms()
            - self.config.delay_ms
            - us_trade_bars.CLOSING_WINDOW_MS
            - base.TRADE_PUBLISH_LAG_MS
        )
        due = [close for close in closes if close <= horizon]
        known = (
            await self._source.closing_prints(
                instrument.venue_market_id, due, max_requests
            )
            if due
            else {}
        )
        return known, tuple(close for close in closes if close not in known)

    async def _minutes(
        self, instrument: instruments.InstrumentId, start_ms: int, end_ms: int
    ) -> _Fetched:
        """Fetches session minute bars of a range and their closing prints.

        Up to ``CLOSING_REQUESTS_PER_FETCH`` closing windows are read.
        Sessions ending before the first bar Alpaca returned read none
        (they had no trade in the range).

        Args:
            instrument: Instrument.
            start_ms: Inclusive start.
            end_ms: Exclusive end.

        Returns:
            Ascending candles without closing prints, proven up to the
            settled time (or the last bar read when the page cap cut the
            range), with the closing prints of the sessions ending in the
            proven part.
        """
        now = self._clock.now_ms()
        stop = min(end_ms, now - self.config.delay_ms)
        if stop <= start_ms:
            return _Fetched([], start_ms, complete=True)
        rows, complete = await self._source.pages(
            f"/v2/stocks/{instrument.venue_market_id}/bars",
            {
                "timeframe": "1Min",
                "start": rfc3339(start_ms),
                "end": rfc3339(stop - 1),
                "adjustment": "split",
                "feed": self.config.rest_feed,
                "limit": _PAGE_LIMIT,
                "sort": "asc",
            },
            "bars",
        )
        settled = self._settled_before()
        out = []
        for row in rows:
            bar = minute_bar(
                row,
                instrument,
                now,
                settled_before_ms=settled,
                source=f"{SOURCE}.rest",
            )
            if bar is not None and start_ms <= bar.start_ms < end_ms:
                out.append(bar)
        proven = _proven(rows, min(stop, settled), complete)
        prints, pending = await self._closes(
            instrument,
            max(start_ms, rfc3339_ms(str(rows[0]["t"]))) if rows else proven,
            min(end_ms, proven),
            CLOSING_REQUESTS_PER_FETCH,
        )
        return _Fetched(out, proven, complete, prints, pending)

    async def _days(
        self, instrument: instruments.InstrumentId, start_ms: int, end_ms: int
    ) -> _Fetched:
        """Fetches session daily bars of a range.

        Args:
            instrument: Instrument.
            start_ms: Inclusive start.
            end_ms: Exclusive end.

        Returns:
            Ascending candles, proven up to the start of the New York day
            still open (its bar is not final).
        """
        now = self._clock.now_ms()
        stop = min(end_ms, now - self.config.delay_ms)
        if stop <= start_ms:
            return _Fetched([], start_ms, complete=True)
        rows, complete = await self._source.pages(
            f"/v2/stocks/{instrument.venue_market_id}/bars",
            {
                "timeframe": "1Day",
                "start": ny_date(start_ms).isoformat(),
                "end": rfc3339(stop - 1),
                "adjustment": "split",
                "feed": self.config.rest_feed,
                "limit": _PAGE_LIMIT,
                "sort": "asc",
            },
            "bars",
        )
        out = []
        for row in rows:
            bar = daily_bar(
                row,
                instrument,
                now,
                delay_ms=self.config.delay_ms,
                source=f"{SOURCE}.rest",
            )
            if bar is not None and start_ms <= bar.start_ms < end_ms:
                out.append(bar)
        day_open = datetime.datetime.combine(
            ny_date(now - self.config.delay_ms), datetime.time(0), tzinfo=_NY
        )
        settled = int(day_open.timestamp() * 1000)
        return _Fetched(
            out, _proven(rows, min(stop, settled), complete), complete
        )

    @staticmethod
    def _window(
        timeframe: timeframes.Timeframe,
        start_ms: int,
        end_ms: int,
        limit: int | None,
    ) -> int:
        """Returns the grid-aligned fetch start honoring ``limit``.

        Args:
            timeframe: Resolution.
            start_ms: Requested start.
            end_ms: Requested end.
            limit: Optional maximum bars.

        Returns:
            Fetch start.
        """
        calendar = sessions.XNYS
        try:
            first = calendar.shift(timeframe, start_ms, 0)
            if limit is not None:
                newest = calendar.floor(timeframe, end_ms - 1)
                first = max(
                    first, calendar.shift(timeframe, newest, -(limit - 1))
                )
        except ValueError:
            return start_ms
        return first

    async def fetch_candles(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        start_ms: int,
        end_ms: int,
        limit: int | None = None,
    ) -> list[market.Candle]:
        """Fetches session-aligned history.

        Args:
            instrument: Instrument.
            timeframe: Resolution.
            start_ms: Inclusive start.
            end_ms: Exclusive end.
            limit: Optional maximum bars (newest kept).

        Returns:
            Ascending candles on the XNYS calendar.
        """
        fetched = await self.fetch_candle_coverage(
            instrument, timeframe, start_ms, end_ms, limit
        )
        return list(fetched.candles)

    async def fetch_candle_coverage(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        start_ms: int,
        end_ms: int,
        limit: int | None = None,
    ) -> candle_rules.CandleCoverage:
        """Fetches session-aligned history with the span it covers.

        Args:
            instrument: Instrument.
            timeframe: Resolution.
            start_ms: Inclusive start.
            end_ms: Exclusive end.
            limit: Optional maximum bars (newest kept).

        Returns:
            Ascending candles on the XNYS calendar and the span in which
            every bar was delivered final (a slot there without a bar had
            no trades). Intraday bars carry the sessions' closing prints;
            the last minute of a session whose prints are pending is left
            out of the span and the bars over it are not final.
        """
        self._require_timeframe(timeframe)
        await self._ready(instrument)
        if end_ms <= start_ms:
            return candle_rules.CandleCoverage((), (), complete=True)
        if timeframe.is_seconds:
            return await self._seconds_history(
                instrument, timeframe, start_ms, end_ms, limit
            )
        first = self._window(timeframe, start_ms, end_ms, limit)
        now = self._clock.now_ms()
        holes: list[candle_rules.Span] = []
        if timeframe in _INTRADAY:
            fetched = await self._minutes(instrument, first, end_ms)
            settled = self._settled_before()
            rows = fetched.minutes(instrument, now, settled)
            holes = fetched.holes(instrument)
            if timeframe is not _M1:
                rows = unsettled(
                    session_bars.aggregate(
                        rows, timeframe, now_ms=now, covered_to_ms=settled
                    ),
                    holes,
                )
        else:
            fetched = await self._days(instrument, first, end_ms)
            rows = fetched.rows
            if timeframe is not _D1:
                finals = [bar.end_ms for bar in rows if bar.is_final]
                rows = session_bars.aggregate(
                    rows,
                    timeframe,
                    now_ms=now,
                    covered_to_ms=max(finals, default=0),
                )
        return base.candle_coverage(
            rows,
            start_ms,
            end_ms,
            limit,
            candle_rules.subtract_spans([(first, fetched.proven_ms)], holes),
            complete=fetched.complete,
        )

    async def _tape(
        self, instrument: instruments.InstrumentId, start_ms: int, end_ms: int
    ) -> tuple[list[us_trade_bars.TapeTrade], int]:
        """Fetches and classifies trades of a range.

        Args:
            instrument: Instrument.
            start_ms: Inclusive start.
            end_ms: Exclusive end.

        Returns:
            Classified trades in ``[start, covered)`` and ``covered``, the
            end up to which both trades and quotes are complete.
        """
        symbol = instrument.venue_market_id
        common = {
            "feed": self.config.rest_feed,
            "limit": _PAGE_LIMIT,
            "sort": "asc",
        }
        trades = await self._source.paged(
            f"/v2/stocks/{symbol}/trades",
            {**common, "start": rfc3339(start_ms), "end": rfc3339(end_ms - 1)},
            "trades",
        )
        quotes = await self._source.paged(
            f"/v2/stocks/{symbol}/quotes",
            {
                **common,
                "start": rfc3339(start_ms - 10_000),
                "end": rfc3339(end_ms - 1),
            },
            "quotes",
        )
        covered = end_ms
        if len(trades) >= MAX_PAGES * _PAGE_LIMIT and trades:
            covered = min(covered, rfc3339_ms(str(trades[-1]["t"])))
        if len(quotes) >= MAX_PAGES * _PAGE_LIMIT and quotes:
            covered = min(covered, rfc3339_ms(str(quotes[-1]["t"])))
        book = QuoteBook(capacity=len(quotes) + 1)
        for quote in quotes:
            book.add(quote)
        valid = [t for t in trades if not t.get("u")]
        classified = classify(valid, book, classification.LeeReadyClassifier())
        return [t for t in classified if t.ts_ms < covered], covered

    async def _seconds_history(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        start_ms: int,
        end_ms: int,
        limit: int | None,
    ) -> candle_rules.CandleCoverage:
        """Builds seconds bars from REST trades and quotes.

        A range ending at a session close also reads the closing window,
        so the session's last bar gets its closing print.

        Args:
            instrument: Instrument.
            timeframe: Seconds resolution.
            start_ms: Inclusive start.
            end_ms: Exclusive end.
            limit: Optional maximum bars (newest kept).

        Returns:
            Ascending seconds candles within the configured window and the
            span their trades settle completely.
        """
        now = self._clock.now_ms()
        stop = min(
            us_trade_bars.closing_window_end(instrument, end_ms),
            now - self.config.delay_ms - base.TRADE_PUBLISH_LAG_MS,
        )
        first = max(
            timeframe.floor(start_ms),
            timeframe.floor(stop - self.config.seconds_history_ms),
        )
        if stop <= first:
            return candle_rules.CandleCoverage((), (), complete=True)
        trades, covered = await self._tape(instrument, first, stop)
        bars = us_trade_bars.bars_from_trades(
            trades,
            instrument,
            timeframe,
            from_ms=first,
            to_ms=covered,
            source=f"{SOURCE}.trades.rest",
            now_ms=now,
        )
        settled = us_trade_bars.settled_until(instrument, timeframe, covered)
        return base.candle_coverage(
            bars,
            start_ms,
            end_ms,
            limit,
            [(first, settled)],
            complete=covered >= stop,
        )

    async def fetch_trades(
        self,
        instrument: instruments.InstrumentId,
        start_ms: int,
        end_ms: int,
        limit: int | None = None,
    ) -> list[market.Trade]:
        """Fetches public trades (aggressor unknown).

        Args:
            instrument: Instrument.
            start_ms: Inclusive start.
            end_ms: Exclusive end.
            limit: Optional maximum trades (newest kept).

        Returns:
            Ascending trades within the configured window.
        """
        await self._ready(instrument)
        now = self._clock.now_ms()
        stop = min(end_ms, now - self.config.delay_ms)
        first = max(start_ms, stop - self.config.seconds_history_ms)
        if stop <= first:
            return []
        rows = await self._source.paged(
            f"/v2/stocks/{instrument.venue_market_id}/trades",
            {
                "feed": self.config.rest_feed,
                "limit": _PAGE_LIMIT,
                "sort": "asc",
                "start": rfc3339(first),
                "end": rfc3339(stop - 1),
            },
            "trades",
        )
        trades = [
            to_market_trade(
                parse_trade(row, market.AggressorSide.UNKNOWN), instrument, now
            )
            for row in rows
            if not row.get("u")
        ]
        if limit is not None:
            trades = trades[-limit:]
        return trades

    async def _snapshots(
        self, symbols: Sequence[str]
    ) -> Mapping[str, Mapping[str, Any]]:
        """Fetches ``/v2/stocks/snapshots`` for symbols.

        Args:
            symbols: Alpaca symbols.

        Returns:
            Snapshot per symbol.
        """
        feed = (
            Feed.DELAYED_SIP.value
            if self.config.feed is Feed.DELAYED_SIP
            else self.config.rest_feed
        )
        payload = await self._source.get(
            "/v2/stocks/snapshots",
            {"symbols": ",".join(symbols), "feed": feed},
        )
        if isinstance(payload, Mapping) and "snapshots" in payload:
            payload = payload["snapshots"]
        return payload if isinstance(payload, Mapping) else {}

    def _snapshot(
        self,
        instrument: instruments.InstrumentId,
        row: Mapping[str, Any],
    ) -> market.MarketSnapshot:
        """Converts an Alpaca snapshot.

        Args:
            instrument: Instrument.
            row: ``{latestTrade, latestQuote, dailyBar, prevDailyBar}``.

        Returns:
            Snapshot; volume/turnover are of the current New York day and
            the change is from the previous daily close.
        """
        trade = row.get("latestTrade") or {}
        quote = row.get("latestQuote") or {}
        day = row.get("dailyBar") or {}
        previous = row.get("prevDailyBar") or {}
        last = base.opt_dec(trade.get("p"))
        prev_close = base.opt_dec(previous.get("c"))
        volume = base.opt_dec(day.get("v"))
        vwap = base.opt_dec(day.get("vw"))
        stamp = trade.get("t") or quote.get("t")
        return market.MarketSnapshot(
            instrument_id=instrument,
            feed_state=quality.FeedState.LIVE,
            source=f"{SOURCE}.rest",
            as_of_ms=rfc3339_ms(str(stamp)) if stamp else self._clock.now_ms(),
            last=last,
            best_bid=base.opt_dec(quote.get("bp")) or None,
            best_ask=base.opt_dec(quote.get("ap")) or None,
            volume_24h_base=volume,
            turnover_24h_quote=(
                None if volume is None or vwap is None else volume * vwap
            ),
            change_24h_pct=(
                None
                if last is None or not prev_close
                else (last - prev_close) / prev_close * decimals.HUNDRED
            ),
        )

    async def fetch_snapshot(
        self, instrument: instruments.InstrumentId
    ) -> market.MarketSnapshot:
        """Fetches the latest trade, quote and daily bar.

        Args:
            instrument: Instrument.

        Returns:
            Snapshot.

        Raises:
            errors.MissingDataError: When Alpaca returns no snapshot.
        """
        await self._ready(instrument)
        rows = await self._snapshots([instrument.venue_market_id])
        row = rows.get(instrument.venue_market_id)
        if not row:
            raise errors.MissingDataError(
                f"alpaca has no snapshot for {instrument}"
            )
        return self._snapshot(instrument, row)

    async def fetch_snapshots(self) -> list[market.MarketSnapshot]:
        """Fetches snapshots of every listing (batches of 200 symbols).

        Returns:
            Snapshots (turnover = day volume x VWAP, an estimate).
        """
        listed = await self.list_instruments()
        self._source.require_keys(self.VENUE)
        out = []
        symbols = [row.instrument_id.venue_market_id for row in listed]
        by_symbol = {row.instrument_id.venue_market_id: row for row in listed}
        for i in range(0, len(symbols), 200):
            rows = await self._snapshots(symbols[i : i + 200])
            for symbol, row in rows.items():
                if symbol in by_symbol and row:
                    out.append(
                        self._snapshot(by_symbol[symbol].instrument_id, row)
                    )
        return out

    async def fetch_order_book(
        self, instrument: instruments.InstrumentId, depth: int
    ) -> market.OrderBook:
        """Alpaca stock data has no order book.

        Args:
            instrument: Instrument.
            depth: Ignored.

        Raises:
            errors.UnsupportedError: Always.
        """
        del instrument, depth
        raise errors.UnsupportedError("alpaca publishes no stock order book")

    async def fetch_open_interest(
        self, instrument: instruments.InstrumentId
    ) -> market.OpenInterest:
        """Stocks have no open interest.

        Args:
            instrument: Instrument.

        Raises:
            errors.UnsupportedError: Always.
        """
        del instrument
        raise errors.UnsupportedError("stocks have no open interest")

    async def fetch_funding(
        self, instrument: instruments.InstrumentId
    ) -> market.Funding:
        """Stocks have no funding.

        Args:
            instrument: Instrument.

        Raises:
            errors.UnsupportedError: Always.
        """
        del instrument
        raise errors.UnsupportedError("stocks have no funding")

    # Streams -------------------------------------------------------------

    def stream_trades(
        self,
        instrument: instruments.InstrumentId,
        tracker: health.FeedHealthTracker | None = None,
    ) -> AsyncIterator[market.Trade]:
        """Streams public trades (aggressor unknown).

        Args:
            instrument: Instrument.
            tracker: Optional health tracker.

        Returns:
            Async iterator of trades.
        """
        return self._trade_stream(instrument, tracker)

    async def _events(
        self,
        instrument: instruments.InstrumentId,
        channels: Iterable[str],
        tracker: health.FeedHealthTracker | None,
        timeout_s: float | None = None,
    ) -> AsyncGenerator[tuple[str, Any], None]:
        """Subscribes and yields stream events (``("tick", now)`` on idle).

        Args:
            instrument: Instrument.
            channels: Channels.
            tracker: Optional health tracker.
            timeout_s: Idle interval producing ``tick`` events.

        Yields:
            Events.

        Raises:
            errors.VenueUnavailableError: When the stream gave up.
        """
        await self._ready(instrument)
        sub = await self._source.stream.subscribe(
            instrument.venue_market_id, channels
        )
        try:
            while True:
                try:
                    kind, payload = await asyncio.wait_for(
                        sub.queue.get(), timeout_s
                    )
                except TimeoutError:
                    yield "tick", self._clock.now_ms()
                    continue
                now = self._clock.now_ms()
                if kind == "error":
                    raise payload
                if tracker is not None:
                    if kind == "reset":
                        tracker.record_reconnect(now, "alpaca reconnect")
                        tracker.record_gap(now)
                    elif kind == "msg":
                        tracker.record_message(now)
                yield kind, payload
        finally:
            await self._source.stream.unsubscribe(sub)

    async def _trade_stream(
        self,
        instrument: instruments.InstrumentId,
        tracker: health.FeedHealthTracker | None,
    ) -> AsyncGenerator[market.Trade, None]:
        """Yields trades from the shared stream.

        Args:
            instrument: Instrument.
            tracker: Optional health tracker.

        Yields:
            Trades.
        """
        events = self._events(instrument, ("trades",), tracker)
        async with contextlib.aclosing(events):
            async for kind, payload in events:
                if kind == "msg" and payload.get("T") == "t":
                    yield to_market_trade(
                        parse_trade(payload, market.AggressorSide.UNKNOWN),
                        instrument,
                        self._clock.now_ms(),
                    )

    def stream_candles(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        tracker: health.FeedHealthTracker | None = None,
    ) -> AsyncIterator[market.Candle]:
        """Streams session bars over the shared Alpaca connection.

        Args:
            instrument: Instrument.
            timeframe: Resolution.
            tracker: Optional health tracker.

        Returns:
            Async iterator of candle updates.
        """
        return base.candles_of(
            self.stream_candle_coverage(instrument, timeframe, tracker)
        )

    def stream_candle_coverage(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        tracker: health.FeedHealthTracker | None = None,
    ) -> AsyncIterator[candle_rules.CandleCoverage]:
        """Streams session bars with the spans the connection proved.

        Minute (and aggregated intraday) streams prove the minutes between
        two ``b`` messages of one connection quiet; seconds streams prove
        every second after a segment's first trade up to the delivery time
        of the latest frame. Daily streams prove nothing (their bars are
        not final while streamed).

        Args:
            instrument: Instrument.
            timeframe: Resolution.
            tracker: Optional health tracker.

        Returns:
            Async iterator of candle updates with coverage.
        """
        self._require_timeframe(timeframe)
        if timeframe.is_seconds:
            return self._seconds_stream(instrument, timeframe, tracker)
        if timeframe in _INTRADAY:
            return self._minute_stream(instrument, timeframe, tracker)
        return self._daily_stream(instrument, timeframe, tracker)

    async def _seconds_stream(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        tracker: health.FeedHealthTracker | None,
        grace_ms: int = 1_500,
    ) -> AsyncGenerator[candle_rules.CandleCoverage, None]:
        """Builds seconds bars from streamed trades and quotes.

        Coverage advances only on received frames (proof the connection is
        alive), never on idle ticks; a reconnect starts a new segment.

        Args:
            instrument: Instrument.
            timeframe: Seconds resolution.
            tracker: Optional health tracker.
            grace_ms: Delivery grace before a quiet bar is finalized.

        Yields:
            Live and final seconds candles with newly proven spans.
        """
        builder = us_trade_bars.SecondsBarBuilder(
            instrument, timeframe, source=f"{SOURCE}.trades"
        )
        builder.start_segment(None)
        quotes = QuoteBook()
        classifier = classification.LeeReadyClassifier()
        delay = self.config.delay_ms
        proven_to = 0
        events = self._events(instrument, ("trades", "quotes"), tracker, 1.0)
        async with contextlib.aclosing(events):
            async for kind, payload in events:
                now = self._clock.now_ms()
                candles: list[market.Candle] = []
                covered: tuple[candle_rules.Span, ...] = ()
                if kind == "reset":
                    builder.start_segment(None)
                    classifier.reset()
                elif kind == "msg" and payload.get("T") == "q":
                    quotes.add(payload)
                elif kind == "msg" and payload.get("T") == "t":
                    candles = builder.add(
                        classify([payload], quotes, classifier), now
                    )
                elif kind in ("frame", "tick"):
                    candles = builder.finalize_through(
                        now - delay - grace_ms, now
                    )
                    span = builder.coverage(now - delay - grace_ms)
                    if (
                        kind == "frame"
                        and span is not None
                        and span[1] > proven_to
                    ):
                        covered, proven_to = (span,), span[1]
                if candles or covered:
                    yield candle_rules.CandleCoverage(tuple(candles), covered)

    async def _minute_stream(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        tracker: health.FeedHealthTracker | None,
    ) -> AsyncGenerator[candle_rules.CandleCoverage, None]:
        """Streams 1m bars, or aggregates them on the XNYS grid.

        The current aggregate is seeded from REST minute bars; it becomes
        final once a minute at or after its end arrives or a minute after
        its end has passed. Alpaca sends one ``b`` message per minute with
        trades, in order, so two ``b`` messages of one connection prove the
        minutes between them quiet (for every intraday timeframe); a
        reconnect starts the proof over.

        On a consolidated feed a session's last minute (and the aggregate
        over it) stays open until its closing window has passed and its
        closing prints were read over REST (once per session, within the
        shared budget, retried while unanswered); the prints then fold into
        it and its slot is proven.

        Args:
            instrument: Instrument.
            timeframe: Intraday resolution.
            tracker: Optional health tracker.

        Yields:
            Candle updates with newly proven spans.
        """
        calendar = sessions.XNYS
        minutes: dict[int, market.Candle] = {}
        emitted: dict[int, market.Candle] = {}
        delay = self.config.delay_ms
        closes = _LiveCloses(instrument, self._source)

        def current(now_ms: int) -> list[market.Candle]:
            """Recomputes the aggregates touched by stored minutes.

            Args:
                now_ms: Receipt time.

            Returns:
                Changed aggregates.
            """
            rows = closes.fold(minutes.values(), now_ms)
            if timeframe is not _M1:
                rows = unsettled(
                    session_bars.aggregate(
                        rows,
                        timeframe,
                        now_ms=now_ms,
                        covered_to_ms=now_ms - delay - _BAR_SETTLE_MS,
                    ),
                    closes.holes(),
                )
            changed = []
            for bar in rows:
                if changed_candle(emitted.get(bar.start_ms), bar):
                    emitted[bar.start_ms] = bar
                    changed.append(bar)
            return changed

        events = self._events(
            instrument, ("bars", "updatedBars", "statuses"), tracker, 15.0
        )
        chain = candle_rules.StreamCoverage()
        async with contextlib.aclosing(events):
            seeded = False
            async for kind, payload in events:
                now = self._clock.now_ms()
                covered: tuple[candle_rules.Span, ...] = ()
                if kind == "reset":
                    chain.restart()
                if not seeded or kind == "reset":
                    seeded = True
                    try:
                        start = calendar.floor(timeframe, now - delay)
                        seed = await self._minutes(instrument, start, now)
                        for bar in seed.rows:
                            minutes[bar.start_ms] = bar
                        closes.seed(seed)
                    except (errors.JosephError, ValueError) as err:
                        _LOG.warning("alpaca seed failed: %s", err)
                if kind == "msg" and payload.get("T") in ("b", "u"):
                    update = minute_bar(
                        payload,
                        instrument,
                        now,
                        settled_before_ms=now + _M1.duration_ms,
                        source=f"{SOURCE}.ws",
                    )
                    if update is not None:
                        minutes[update.start_ms] = update
                        closes.note(update.start_ms)
                    if payload.get("T") == "b":
                        minute = rfc3339_ms(str(payload["t"]))
                        span = chain.observe(minute, minute + _M1.duration_ms)
                        covered = closes.prove(span)
                covered += await closes.learn(now)
                changed = current(now)
                if changed or covered:
                    yield candle_rules.CandleCoverage(tuple(changed), covered)
                cutoff = now - 2 * timeframe.duration_ms - _D1.duration_ms
                for start in [s for s in minutes if s < cutoff]:
                    del minutes[start]
                for start in [s for s in emitted if s < cutoff]:
                    del emitted[start]
                closes.prune(cutoff)

    async def _daily_stream(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        tracker: health.FeedHealthTracker | None,
    ) -> AsyncGenerator[candle_rules.CandleCoverage, None]:
        """Streams the day's bar (``dailyBars``), or its week or month.

        Args:
            instrument: Instrument.
            timeframe: ``1d``, ``1w`` or ``1M``.
            tracker: Optional health tracker.

        Yields:
            Candle updates (non-final until the New York day ended), with
            no coverage.
        """
        days: dict[int, market.Candle] = {}
        last: market.Candle | None = None
        events = self._events(
            instrument, ("dailyBars", "statuses"), tracker, 60.0
        )
        async with contextlib.aclosing(events):
            seeded = False
            async for kind, payload in events:
                now = self._clock.now_ms()
                if not seeded or kind == "reset":
                    seeded = True
                    try:
                        start = sessions.XNYS.floor(timeframe, now)
                        seed = await self._days(instrument, start, now + 1)
                        for bar in seed.rows:
                            days[bar.start_ms] = bar
                    except (errors.JosephError, ValueError) as err:
                        _LOG.warning("alpaca seed failed: %s", err)
                if kind == "msg" and payload.get("T") == "d":
                    update = daily_bar(
                        payload,
                        instrument,
                        now,
                        delay_ms=self.config.delay_ms,
                        source=f"{SOURCE}.ws",
                    )
                    if update is not None:
                        days[update.start_ms] = dataclasses.replace(
                            update, is_final=False
                        )
                if not days:
                    continue
                if timeframe is _D1:
                    newest = days[max(days)]
                else:
                    finals = [b.end_ms for b in days.values() if b.is_final]
                    newest = session_bars.aggregate(
                        days.values(),
                        timeframe,
                        now_ms=now,
                        covered_to_ms=max(finals, default=0),
                    )[-1]
                if changed_candle(last, newest):
                    last = newest
                    yield candle_rules.CandleCoverage((newest,))

    async def close(self) -> None:
        """Releases the shared source when every venue closed."""
        await self._source.close()
