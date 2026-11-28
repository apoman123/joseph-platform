# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Keyless TWSE and TPEx open data: company profiles and daily closes.

* TWSE ``/v1/opendata/t187ap03_L`` and TPEx ``/openapi/v1/mopsfin_t187ap03_O``:
  listed company profiles with the industry code, issued shares, paid-in
  capital and par value.
* TWSE ``/v1/exchangeReport/STOCK_DAY_ALL`` and TPEx
  ``/openapi/v1/tpex_mainboard_daily_close_quotes``: the official end-of-day
  quotes of every listed stock and ETF.

Market capitalization is derived as issued shares (or paid-in capital over
par value) times the last price and labelled as derived by the caller.
"""

from __future__ import annotations

import dataclasses
import datetime
import decimal
import re
import zoneinfo
from collections.abc import Mapping
from typing import Any

from joseph.domain import decimals
from joseph.domain import errors
from joseph.market_data.adapters import http

TWSE_OPENAPI = "https://openapi.twse.com.tw"
TPEX_OPENAPI = "https://www.tpex.org.tw"
TWSE_COMPANIES = "/v1/opendata/t187ap03_L"
TPEX_COMPANIES = "/openapi/v1/mopsfin_t187ap03_O"
TWSE_DAILY = "/v1/exchangeReport/STOCK_DAY_ALL"
TPEX_DAILY = "/openapi/v1/tpex_mainboard_daily_close_quotes"
_TAIPEI = zoneinfo.ZoneInfo("Asia/Taipei")
_CLOSE = datetime.time(13, 30)

INDUSTRIES: dict[str, tuple[str, str]] = {
    "01": ("水泥工業", "Cement"),
    "02": ("食品工業", "Food"),
    "03": ("塑膠工業", "Plastics"),
    "04": ("紡織纖維", "Textiles"),
    "05": ("電機機械", "Electric Machinery"),
    "06": ("電器電纜", "Electrical and Cable"),
    "08": ("玻璃陶瓷", "Glass and Ceramics"),
    "09": ("造紙工業", "Paper and Pulp"),
    "10": ("鋼鐵工業", "Iron and Steel"),
    "11": ("橡膠工業", "Rubber"),
    "12": ("汽車工業", "Automobile"),
    "14": ("建材營造業", "Building Material and Construction"),
    "15": ("航運業", "Shipping and Transportation"),
    "16": ("觀光餐旅", "Tourism and Hospitality"),
    "17": ("金融保險業", "Financial and Insurance"),
    "18": ("貿易百貨業", "Trading and Consumer Goods"),
    "19": ("綜合", "Comprehensive"),
    "20": ("其他業", "Other"),
    "21": ("化學工業", "Chemical"),
    "22": ("生技醫療業", "Biotechnology and Medical Care"),
    "23": ("油電燃氣業", "Oil, Gas and Electricity"),
    "24": ("半導體業", "Semiconductor"),
    "25": ("電腦及週邊設備業", "Computer and Peripheral Equipment"),
    "26": ("光電業", "Optoelectronic"),
    "27": ("通信網路業", "Communications and Internet"),
    "28": ("電子零組件業", "Electronic Parts and Components"),
    "29": ("電子通路業", "Electronic Products Distribution"),
    "30": ("資訊服務業", "Information Service"),
    "31": ("其他電子業", "Other Electronic"),
    "32": ("文化創意業", "Cultural and Creative"),
    "33": ("農業科技業", "Agricultural Science and Technology"),
    "34": ("電子商務", "E-commerce"),
    "35": ("綠能環保", "Green Energy and Environmental Services"),
    "36": ("數位雲端", "Digital and Cloud Services"),
    "37": ("運動休閒", "Sports and Leisure"),
    "38": ("居家生活", "Household"),
    "80": ("管理股票", "Managed Stocks"),
    "91": ("存託憑證", "Taiwan Depositary Receipts"),
}
"""TWSE/TPEx industry codes (``產業別``) with Chinese and English names.

Checked against the industry column of the TWSE ISIN listings
(``isin.twse.com.tw/isin/C_public.jsp?strMode=2`` and ``strMode=4``).
"""

_PAR = re.compile(r"新台幣\s*([0-9]+(?:\.[0-9]+)?)\s*元")


@dataclasses.dataclass(frozen=True)
class TwCompany:
    """A listed company profile.

    Attributes:
        code: Stock code, e.g. ``2330``.
        market: ``twse`` or ``tpex``.
        name: Chinese short name.
        english_name: English short name, or None.
        industry_code: Two-digit industry code, or None.
        shares: Issued common shares, or None when not derivable.
    """

    code: str
    market: str
    name: str
    english_name: str | None
    industry_code: str | None
    shares: decimal.Decimal | None

    @property
    def industry(self) -> str | None:
        """English industry name with the Chinese one, or None if unknown."""
        names = INDUSTRIES.get(self.industry_code or "")
        return f"{names[1]} {names[0]}" if names else None


@dataclasses.dataclass(frozen=True)
class TwDailyQuote:
    """An official end-of-day quote.

    Attributes:
        code: Stock or ETF code.
        name: Chinese short name.
        open: Opening price, or None without trades.
        high: High price, or None.
        low: Low price, or None.
        close: Closing price, or None.
        change: Change from the previous close, or None.
        volume: Traded shares, or None.
        value: Traded value in TWD, or None.
        shares: Issued shares when the file publishes them (TPEx), else None.
        as_of_ms: Session close of the trading date, UTC epoch milliseconds.
    """

    code: str
    name: str
    open: decimal.Decimal | None
    high: decimal.Decimal | None
    low: decimal.Decimal | None
    close: decimal.Decimal | None
    change: decimal.Decimal | None
    volume: decimal.Decimal | None
    value: decimal.Decimal | None
    shares: decimal.Decimal | None
    as_of_ms: int | None

    @property
    def previous_close(self) -> decimal.Decimal | None:
        """Close minus change, or None when either is missing."""
        if self.close is None or self.change is None:
            return None
        return self.close - self.change


def _number(text: object) -> decimal.Decimal | None:
    """Parses an open-data number (``--``, ``---`` and blanks are missing).

    Args:
        text: Raw field.

    Returns:
        Value, or None.
    """
    if text is None:
        return None
    cleaned = str(text).strip().replace(",", "").replace("+", "")
    try:
        return decimals.to_decimal(cleaned) if cleaned else None
    except (ValueError, decimal.InvalidOperation):
        return None


def roc_close_ms(text: object) -> int | None:
    """Converts a ROC date such as ``1151127`` to its 13:30 Taipei close.

    Args:
        text: ROC calendar date ``YYYMMDD``.

    Returns:
        UTC epoch milliseconds, or None when malformed.
    """
    value = str(text or "").strip()
    if not re.fullmatch(r"\d{6,7}", value):
        return None
    try:
        day = datetime.date(
            int(value[:-4]) + 1911, int(value[-4:-2]), int(value[-2:])
        )
    except ValueError:
        return None
    stamp = datetime.datetime.combine(day, _CLOSE, tzinfo=_TAIPEI)
    return int(stamp.timestamp() * 1000)


def _shares(
    issued: object, capital: object, par: object
) -> decimal.Decimal | None:
    """Returns issued shares, else paid-in capital over a TWD par value.

    Args:
        issued: Issued common shares field.
        capital: Paid-in capital in TWD.
        par: Par value text such as ``新台幣 10.0000元``.

    Returns:
        Positive share count, or None.
    """
    shares = _number(issued)
    if shares is not None and shares > 0:
        return shares
    match = _PAR.search(str(par or "").replace(" ", ""))
    amount = _number(capital)
    if match and amount is not None and amount > 0:
        value = decimals.to_decimal(match.group(1))
        if value > 0:
            return (amount / value).quantize(decimals.ONE)
    return None


def _rows(payload: Any, label: str) -> list[Mapping[str, Any]]:
    """Checks that an open-data body is a list of objects.

    Args:
        payload: Parsed JSON.
        label: Source label for the error.

    Returns:
        The objects.

    Raises:
        errors.VenueUnavailableError: If the body is not a list.
    """
    if not isinstance(payload, list):
        raise errors.VenueUnavailableError(f"{label} returned no table")
    return [row for row in payload if isinstance(row, Mapping)]


def parse_twse_companies(payload: Any) -> list[TwCompany]:
    """Parses TWSE listed company profiles.

    Args:
        payload: Body of ``t187ap03_L``.

    Returns:
        Companies.
    """
    return [
        TwCompany(
            code=str(row.get("公司代號") or "").strip(),
            market="twse",
            name=str(row.get("公司簡稱") or "").strip(),
            english_name=str(row.get("英文簡稱") or "").strip() or None,
            industry_code=str(row.get("產業別") or "").strip() or None,
            shares=_shares(
                row.get("已發行普通股數或TDR原股發行股數"),
                row.get("實收資本額"),
                row.get("普通股每股面額"),
            ),
        )
        for row in _rows(payload, "twse company profiles")
        if row.get("公司代號")
    ]


def parse_tpex_companies(payload: Any) -> list[TwCompany]:
    """Parses TPEx listed company profiles.

    Args:
        payload: Body of ``mopsfin_t187ap03_O``.

    Returns:
        Companies.
    """
    return [
        TwCompany(
            code=str(row.get("SecuritiesCompanyCode") or "").strip(),
            market="tpex",
            name=str(row.get("CompanyAbbreviation") or "").strip(),
            english_name=str(row.get("Symbol") or "").strip() or None,
            industry_code=str(row.get("SecuritiesIndustryCode") or "").strip()
            or None,
            shares=_shares(
                row.get("IssueShares"),
                row.get("Paidin.Capital.NTDollars"),
                row.get("ParValueOfCommonStock"),
            ),
        )
        for row in _rows(payload, "tpex company profiles")
        if row.get("SecuritiesCompanyCode")
    ]


def parse_twse_daily(payload: Any) -> list[TwDailyQuote]:
    """Parses TWSE ``STOCK_DAY_ALL`` quotes.

    Args:
        payload: Body of ``STOCK_DAY_ALL``.

    Returns:
        Quotes of every listed stock and ETF.
    """
    return [
        TwDailyQuote(
            code=str(row.get("Code") or "").strip(),
            name=str(row.get("Name") or "").strip(),
            open=_number(row.get("OpeningPrice")),
            high=_number(row.get("HighestPrice")),
            low=_number(row.get("LowestPrice")),
            close=_number(row.get("ClosingPrice")),
            change=_number(row.get("Change")),
            volume=_number(row.get("TradeVolume")),
            value=_number(row.get("TradeValue")),
            shares=None,
            as_of_ms=roc_close_ms(row.get("Date")),
        )
        for row in _rows(payload, "twse daily quotes")
        if row.get("Code")
    ]


def parse_tpex_daily(payload: Any) -> list[TwDailyQuote]:
    """Parses TPEx main-board daily close quotes.

    Args:
        payload: Body of ``tpex_mainboard_daily_close_quotes``.

    Returns:
        Quotes (warrants and bonds included; callers select by code).
    """
    return [
        TwDailyQuote(
            code=str(row.get("SecuritiesCompanyCode") or "").strip(),
            name=str(row.get("CompanyName") or "").strip(),
            open=_number(row.get("Open")),
            high=_number(row.get("High")),
            low=_number(row.get("Low")),
            close=_number(row.get("Close")),
            change=_number(row.get("Change")),
            volume=_number(row.get("TradingShares")),
            value=_number(row.get("TransactionAmount")),
            shares=_number(row.get("Capitals")),
            as_of_ms=roc_close_ms(row.get("Date")),
        )
        for row in _rows(payload, "tpex daily quotes")
        if row.get("SecuritiesCompanyCode")
    ]


def is_etf_code(code: str) -> bool:
    """Tells whether a Taiwan code is an ETF (``00`` codes such as ``0050``).

    Args:
        code: Security code.

    Returns:
        True for ETF codes.
    """
    return bool(re.fullmatch(r"00[0-9]{2,4}[A-Z]?", code))


def is_stock_code(code: str) -> bool:
    """Tells whether a Taiwan code is a four-digit stock (or TDR).

    Args:
        code: Security code.

    Returns:
        True for four-digit codes not starting with ``0``.
    """
    return bool(re.fullmatch(r"[1-9][0-9]{3}", code))


class TaiwanOpenData:
    """Reads TWSE and TPEx open data (no caching here).

    Attributes:
        twse: Client for ``TWSE_OPENAPI``.
        tpex: Client for ``TPEX_OPENAPI``.
    """

    def __init__(self, twse: http.VenueHttp, tpex: http.VenueHttp) -> None:
        """Binds the clients.

        Args:
            twse: Rate-limited client for ``TWSE_OPENAPI``.
            tpex: Rate-limited client for ``TPEX_OPENAPI``.
        """
        self.twse = twse
        self.tpex = tpex

    async def companies(self, market: str) -> list[TwCompany]:
        """Downloads the company profiles of one market.

        Args:
            market: ``twse`` or ``tpex``.

        Returns:
            Companies.
        """
        if market == "twse":
            return parse_twse_companies(await self.twse.get(TWSE_COMPANIES))
        return parse_tpex_companies(await self.tpex.get(TPEX_COMPANIES))

    async def daily(self, market: str) -> list[TwDailyQuote]:
        """Downloads the official daily quotes of one market.

        Args:
            market: ``twse`` or ``tpex``.

        Returns:
            Quotes.
        """
        if market == "twse":
            return parse_twse_daily(await self.twse.get(TWSE_DAILY))
        return parse_tpex_daily(await self.tpex.get(TPEX_DAILY))

    async def close(self) -> None:
        """Closes owned clients."""
        await self.twse.close()
        await self.tpex.close()
