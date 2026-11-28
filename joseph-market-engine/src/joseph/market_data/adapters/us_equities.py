# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Keyless US equity lists: the Nasdaq.com screener and index members.

* ``api.nasdaq.com/api/screener/stocks`` (unofficial, delayed): every
  Nasdaq, NYSE and NYSE American listing with sector, industry, market
  capitalization and a delayed last sale, change and volume. The endpoint
  answers browsers only, so requests carry a browser-like User-Agent.
* ``api.nasdaq.com/api/quote/list-type/nasdaq100``: Nasdaq-100 members.
* Wikipedia ``List of S&P 500 companies`` (wikitext of the constituents
  table): S&P 500 members.

Sector and industry always come from the screener so that every US data set
uses one taxonomy; a listing without a sector is ``Miscellaneous``.
"""

from __future__ import annotations

import dataclasses
import decimal
import re
from collections.abc import Mapping
from typing import Any

from joseph.domain import decimals
from joseph.domain import errors
from joseph.market_data.adapters import http

NASDAQ_API = "https://api.nasdaq.com"
WIKIPEDIA_API = "https://en.wikipedia.org"
SCREENER_PATH = "/api/screener/stocks"
NASDAQ100_PATH = "/api/quote/list-type/nasdaq100"
SP500_PAGE = "List_of_S&P_500_companies"
EXCHANGES = {"nasdaq": "Nasdaq", "nyse": "NYSE", "amex": "NYSE American"}
"""Screener ``exchange`` filters and the listing exchange they mean."""

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
}
"""Headers the Nasdaq.com API requires (it rejects non-browser agents)."""

WIKIPEDIA_HEADERS = {"User-Agent": f"{http.USER_AGENT} (heatmap index members)"}
"""Wikimedia asks API clients to name themselves (generic agents get 403)."""

UNCLASSIFIED = "Miscellaneous"
"""Sector of a listing the screener does not classify."""

_SYMBOL = re.compile(r"^[A-Z]{1,5}(?:\.[A-Z])?$")
_SKIPPED_NAME = re.compile(
    r"\b(?:warrants?|units?|rights?|preferred"
    r"|depositary shares? representing)\b",
    re.I,
)
_NAME_TAIL = re.compile(
    r"[,\s]*(?:\b(?:class|series) [a-z]\b\s*)?"
    r"\b(?:common stock|common shares|ordinary shares|subordinate voting shares"
    r"|american depositary shares|american depositary receipts"
    r"|shares of beneficial"
    r" interest|capital stock)\b.*$",
    re.I,
)
_WIKI_SYMBOL = re.compile(r"\{\{\s*(?:NyseSymbol|NasdaqSymbol)\s*\|\s*([^}|]+)")


@dataclasses.dataclass(frozen=True)
class ScreenerRow:
    """One common stock of the screener.

    Attributes:
        symbol: Ticker in exchange form (``BRK.B``).
        name: Company name without the share-class suffix.
        exchange: Listing exchange (``Nasdaq``, ``NYSE``, ``NYSE American``).
        sector: Screener sector, ``Miscellaneous`` when blank.
        industry: Screener industry, or None.
        last: Delayed last sale, or None.
        change_pct: Delayed change from the previous close in percent.
        volume: Delayed session volume in shares.
        market_cap: Market capitalization in USD, or None when not positive.
    """

    symbol: str
    name: str
    exchange: str
    sector: str
    industry: str | None
    last: decimal.Decimal | None
    change_pct: decimal.Decimal | None
    volume: decimal.Decimal | None
    market_cap: decimal.Decimal | None


def _number(text: object) -> decimal.Decimal | None:
    """Parses screener numbers such as ``$355.58``, ``-2.774%`` or ``1,234``.

    Args:
        text: Raw field.

    Returns:
        The value, or None for blanks, ``NA`` and malformed text.
    """
    if text is None:
        return None
    cleaned = str(text).strip().replace("$", "").replace(",", "")
    cleaned = cleaned.removesuffix("%").strip()
    if cleaned in {"", "NA", "N/A", "--"}:
        return None
    try:
        return decimals.to_decimal(cleaned)
    except (ValueError, decimal.InvalidOperation):
        return None


def clean_name(name: str) -> str:
    """Removes share-class descriptions from a security name.

    Args:
        name: Screener name, e.g. ``Apple Inc. Common Stock``.

    Returns:
        The company name, e.g. ``Apple Inc.``.
    """
    cleaned = _NAME_TAIL.sub("", name.strip()).strip(" ,")
    return cleaned or name.strip()


def parse_screener(payload: Any, exchange: str) -> list[ScreenerRow]:
    """Parses a screener download into common stocks.

    Preferred shares, warrants, units and rights are skipped.

    Args:
        payload: JSON body of ``/api/screener/stocks``.
        exchange: Listing exchange label of the filtered download.

    Returns:
        Rows in the source order.

    Raises:
        errors.VenueUnavailableError: If the body has no row table.
    """
    try:
        rows = payload["data"]["rows"]
    except (KeyError, TypeError):
        raise errors.VenueUnavailableError(
            "nasdaq.com screener returned no table"
        ) from None
    out = []
    for row in rows or ():
        if not isinstance(row, Mapping):
            continue
        symbol = str(row.get("symbol") or "").strip().replace("/", ".")
        name = str(row.get("name") or "").strip()
        if not _SYMBOL.match(symbol) or _SKIPPED_NAME.search(name):
            continue
        cap = _number(row.get("marketCap"))
        out.append(
            ScreenerRow(
                symbol=symbol,
                name=clean_name(name) if name else symbol,
                exchange=exchange,
                sector=str(row.get("sector") or "").strip() or UNCLASSIFIED,
                industry=str(row.get("industry") or "").strip() or None,
                last=_number(row.get("lastsale")),
                change_pct=_number(row.get("pctchange")),
                volume=_number(row.get("volume")),
                market_cap=cap if cap is not None and cap > 0 else None,
            )
        )
    return out


def parse_nasdaq100(payload: Any) -> list[str]:
    """Parses the Nasdaq-100 member list.

    Args:
        payload: JSON body of ``/api/quote/list-type/nasdaq100``.

    Returns:
        Member tickers in exchange form.

    Raises:
        errors.VenueUnavailableError: If the body has no member table.
    """
    try:
        rows = payload["data"]["data"]["rows"]
    except (KeyError, TypeError):
        raise errors.VenueUnavailableError(
            "nasdaq.com returned no Nasdaq-100 table"
        ) from None
    return [
        str(row["symbol"]).strip().replace("/", ".")
        for row in rows or ()
        if isinstance(row, Mapping) and row.get("symbol")
    ]


def parse_sp500(payload: Any) -> list[str]:
    """Parses S&P 500 members from the Wikipedia constituents wikitext.

    Args:
        payload: JSON body of the MediaWiki ``action=parse`` request.

    Returns:
        Member tickers in exchange form (``BRK.B``), in table order.

    Raises:
        errors.VenueUnavailableError: If the text has implausibly few rows.
    """
    try:
        text = str(payload["parse"]["wikitext"])
    except (KeyError, TypeError):
        raise errors.VenueUnavailableError(
            "wikipedia returned no S&P 500 table"
        ) from None
    symbols = list(
        dict.fromkeys(
            match.strip().upper() for match in _WIKI_SYMBOL.findall(text)
        )
    )
    if not symbols:
        raise errors.VenueUnavailableError(
            "wikipedia S&P 500 table has no symbols"
        )
    return symbols


class UsEquitySource:
    """Reads the screener and index member lists (no caching here).

    Attributes:
        nasdaq: Client for ``NASDAQ_API``.
        wikipedia: Client for ``WIKIPEDIA_API``.
    """

    def __init__(
        self, nasdaq: http.VenueHttp, wikipedia: http.VenueHttp
    ) -> None:
        """Binds the clients.

        Args:
            nasdaq: Rate-limited client for ``NASDAQ_API``.
            wikipedia: Rate-limited client for ``WIKIPEDIA_API``.
        """
        self.nasdaq = nasdaq
        self.wikipedia = wikipedia

    async def screener(self, exchange: str) -> list[ScreenerRow]:
        """Downloads the common stocks of one exchange.

        Args:
            exchange: ``nasdaq``, ``nyse`` or ``amex``.

        Returns:
            Parsed rows.

        Raises:
            ValueError: For another exchange.
        """
        if exchange not in EXCHANGES:
            raise ValueError(f"unknown screener exchange {exchange}")
        payload = await self.nasdaq.get(
            SCREENER_PATH,
            {"tableonly": "true", "download": "true", "exchange": exchange},
            headers=BROWSER_HEADERS,
        )
        return parse_screener(payload, EXCHANGES[exchange])

    async def nasdaq100(self) -> list[str]:
        """Downloads the Nasdaq-100 members.

        Returns:
            Member tickers.
        """
        payload = await self.nasdaq.get(NASDAQ100_PATH, headers=BROWSER_HEADERS)
        return parse_nasdaq100(payload)

    async def sp500(self) -> list[str]:
        """Downloads the S&P 500 members from Wikipedia.

        Returns:
            Member tickers.
        """
        payload = await self.wikipedia.get(
            "/w/api.php",
            {
                "action": "parse",
                "page": SP500_PAGE,
                "prop": "wikitext",
                "section": "1",
                "format": "json",
                "formatversion": "2",
            },
            headers=WIKIPEDIA_HEADERS,
        )
        return parse_sp500(payload)

    async def close(self) -> None:
        """Closes owned clients."""
        await self.nasdaq.close()
        await self.wikipedia.close()
