# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""SinoPac Shioaji adapter (TWSE and TPEx stocks and ETFs).

Uses the ``shioaji`` SDK (optional extra ``sinopac``; SinoPac's
proprietary binary wheels, no OSS license, CPython abi3 wheels for Linux
x86_64/aarch64, macOS and Windows, Python 3.12 supported). Verified
against Shioaji 1.7.11:

* ``sj.Shioaji(simulation=...)``; ``api.login(api_key=..., secret_key=...,
  subscribe_trade=True)``; ``api.activate_ca(ca_path=..., ca_passwd=...,
  person_id=...)`` (required before production orders).
* ``sj.StockOrder(action, price, quantity, price_type LMT/MKT,
  time_in_force ROD/IOC/FOK, order_lot Common/IntradayOdd, order_cond
  Cash, custom_field, account)``; Common quantities are lots of
  ``contract.unit`` (1000) shares, IntradayOdd quantities are shares.
* ``api.place_order``, ``api.cancel_order``, ``api.update_order(trade,
  price=..., qty=...)`` (qty is the new, smaller quantity; odd-lot prices
  cannot change), ``api.update_status``, ``api.list_trades``,
  ``api.list_positions(unit=sj.Unit.Share)``, ``api.account_balance``,
  ``api.settlements``, ``api.trading_limits`` and the stock contract
  tree: ``api.contracts.stocks`` (``api.Contracts.Stocks`` in the legacy
  spelling) is a ``ContractCategory`` whose ``[code]`` searches every
  exchange and whose attributes ``TSE``, ``OTC`` and ``OES`` are the
  per-exchange ``ContractGroup``s (``api.Contracts.Stocks.TSE["2890"]``
  in the docs; ``Contracts(Stocks=(OES, OTC, TSE))``). TWSE codes are
  looked up in ``TSE`` and TPEx (OTC) codes in ``OTC``; the emerging
  board (``OES``, 興櫃) is never routed. Held codes (positions carry no
  exchange) take the exchange of their contract; emerging-board codes and
  codes missing from the contract list are reported as unmanaged
  holdings, never as TWSE listings. ``sj.StockOrder`` has no exchange
  field: ``place_order(contract, order)`` sends the order to the
  contract's ``exchange`` (``Exchange.TSE`` or ``Exchange.OTC``). Checked
  in the Shioaji 1.7.11 type stubs (``shioaji/_core.pyi``: ``Contracts``,
  ``ContractCategory.__getattr__ -> ContractGroup``, ``Exchange``,
  ``StockOrder``) and https://sinotrade.github.io/tutor/contract/ and
  https://sinotrade.github.io/zh/shioaji.pdf (5.3 商品檔), 2026-11-23.
* ``api.set_order_callback(cb(state, event))`` with ``StockOrder``
  (``operation.op_type``/``op_code`` "00" = success) and ``StockDeal``
  events; ``api.set_event_callback(cb(resp_code, event_code, info,
  event))`` session events (0 up, 1 down, 2 connect failed, 12
  reconnecting, 13 reconnected).

Limits: 250 order calls (place/update/cancel/update_status) per 10 s and
25 account queries per 5 s; exceeding them suspends service for a minute.

SinoPac requires the API agreement to be signed and a simulation-mode
test (login and place_order, Mon-Fri 08:00-20:00) before production
trading; until then production accounts report ``signed=False`` and this
adapter keeps them TRADING_UNAVAILABLE. Shioaji keeps no client order id:
Joseph tags each order with a 6-character ``custom_field`` and tracks the
trade id.
"""

from __future__ import annotations

import asyncio
import dataclasses
import decimal
import hashlib
import importlib
import logging
import re
from collections.abc import AsyncIterator
from collections.abc import Awaitable
from collections.abc import Callable
from collections.abc import Iterable
from collections.abc import Sequence
from typing import Any
from typing import Protocol
from typing import TypeAlias

from joseph_execution.adapters.brokers import base
from joseph_execution.adapters.brokers import capabilities as broker_caps
from joseph_execution.domain import accounts
from joseph_execution.domain import decimals
from joseph_execution.domain import enums
from joseph_execution.domain import equities
from joseph_execution.domain import errors
from joseph_execution.ports import market as market_port
from joseph_execution.ports import services
from joseph_execution.ports import trading

_LOG = logging.getLogger(__name__)
_State = enums.OrderState
BROKER = enums.Broker.SINOPAC.value
_TIFS = {
    enums.TimeInForce.DAY: "ROD",
    enums.TimeInForce.IOC: "IOC",
    enums.TimeInForce.FOK: "FOK",
}
SJ_EXCHANGES = {
    enums.Venue.TWSE.value: "TSE",
    enums.Venue.TPEX.value: "OTC",
}
"""Shioaji contract group (``api.contracts.stocks.<group>``) per venue."""
_EXCHANGE_ALIASES = {
    enums.Venue.TWSE.value: frozenset({"TSE", "TWSE"}),
    enums.Venue.TPEX.value: frozenset({"OTC"}),
}
_EMERGING = "OES"
# Trade statuses of orders still working at the exchange.
_WORKING = frozenset({"PendingSubmit", "PreSubmitted", "Submitted"})
# SDK exception names that mean the order was refused before it left, so
# the outcome is known (everything else from place_order is unknown).
_REFUSED = frozenset(
    {
        "ValidationError",
        "ShioajiValueError",
        "ShioajiTypeError",
        "AccountNotSignError",
        "AccountNotProvideError",
        "ShioajiPermissionError",
        "CaError",
        "CaExpiredError",
        "CaPasswordError",
    }
)
_TAG_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
# Identifiers SinoPac text can carry: stock accounts, with or without the
# branch (``9A95-0000001``, ``0000001``), and Taiwan national or resident
# ids (``A123456789``, old-style ``AB12345678``).
_ACCOUNT_NUMBER = re.compile(r"\b(?:[0-9A-Z]{4}-)?\d{7}\b")
_PERSON_ID = re.compile(r"\b[A-Z][A-Z1-9]\d{8}\b")
# Orders younger than this are still in the session's trade list, so their
# absence there is conclusive.
_TODAY_WINDOW_MS = 12 * 3_600_000
_COMMON = "Common"
_ODD = "IntradayOdd"
_SNAPSHOT_TTL_MS = 5_000

Runner: TypeAlias = Callable[[Callable[[], Any]], Awaitable[Any]]
"""Runs one blocking SDK call (default: a worker thread)."""


async def _thread_runner(fn: Callable[[], Any]) -> Any:
    """Runs a blocking SDK call in a worker thread.

    Args:
        fn: Call.

    Returns:
        Its result.
    """
    return await asyncio.to_thread(fn)


class SjClient(Protocol):
    """The ``sj.Shioaji`` surface used by the adapter."""

    def login(
        self, api_key: str, secret_key: str, subscribe_trade: bool = True
    ) -> list[Any]:
        """Logs in.

        Args:
            api_key: API key.
            secret_key: Secret key.
            subscribe_trade: Subscribe order/deal callbacks.

        Returns:
            Accounts.
        """
        ...

    def activate_ca(
        self, ca_path: str, ca_passwd: str, person_id: str | None = None
    ) -> bool:
        """Activates the order-signing certificate.

        Args:
            ca_path: ``.pfx`` path.
            ca_passwd: Certificate password.
            person_id: National id of the account holder.

        Returns:
            True on success.
        """
        ...

    def logout(self) -> object:
        """Logs out.

        Returns:
            SDK result.
        """
        ...

    def list_accounts(self) -> list[Any]:
        """Lists accounts.

        Returns:
            Accounts.
        """
        ...

    @property
    def contracts(self) -> Any:
        """The contract tree (``contracts.stocks[code]``)."""
        ...

    def place_order(self, contract: Any, order: Any, timeout: int = ...) -> Any:
        """Places an order.

        Args:
            contract: Contract.
            order: Stock order.
            timeout: Milliseconds.

        Returns:
            Trade.
        """
        ...

    def cancel_order(self, trade: Any, timeout: int = ...) -> Any:
        """Cancels an order.

        Args:
            trade: Trade.
            timeout: Milliseconds.

        Returns:
            Trade.
        """
        ...

    def update_order(
        self,
        trade: Any,
        price: float | None = None,
        qty: int | None = None,
        timeout: int = ...,
    ) -> Any:
        """Changes price or reduces quantity.

        Args:
            trade: Trade.
            price: New price.
            qty: New (smaller) quantity.
            timeout: Milliseconds.

        Returns:
            Trade.
        """
        ...

    def update_status(self, account: Any = None, timeout: int = ...) -> None:
        """Refreshes trades of an account.

        Args:
            account: Account.
            timeout: Milliseconds.
        """
        ...

    def list_trades(self) -> list[Any]:
        """Lists the session's trades.

        Returns:
            Trades.
        """
        ...

    def list_positions(
        self, account: Any = None, unit: Any = None, timeout: int = ...
    ) -> list[Any]:
        """Lists positions.

        Args:
            account: Account.
            unit: ``sj.Unit.Share`` for shares.
            timeout: Milliseconds.

        Returns:
            Positions.
        """
        ...

    def account_balance(self, account: Any = None, timeout: int = ...) -> Any:
        """Reads the bank balance of the settlement account.

        Args:
            account: Account.
            timeout: Milliseconds.

        Returns:
            Balance.
        """
        ...

    def settlements(self, account: Any = None, timeout: int = ...) -> list[Any]:
        """Reads pending settlements (T, T+1, T+2).

        Args:
            account: Account.
            timeout: Milliseconds.

        Returns:
            Settlements.
        """
        ...

    def trading_limits(self, account: Any = None, timeout: int = ...) -> Any:
        """Reads the account's trading limit.

        Args:
            account: Account.
            timeout: Milliseconds.

        Returns:
            Limits.
        """
        ...

    def set_order_callback(self, callback: Callable[[Any, Any], None]) -> None:
        """Sets the order/deal callback.

        Args:
            callback: ``cb(state, event)``.
        """
        ...

    def set_event_callback(
        self, callback: Callable[[int, int, str, str], None]
    ) -> None:
        """Sets the session event callback.

        Args:
            callback: ``cb(resp_code, event_code, info, event)``.
        """
        ...


class SjFactory(Protocol):
    """Creates Shioaji objects (fakes implement it in tests)."""

    def client(self, simulation: bool) -> SjClient:
        """Creates ``sj.Shioaji``.

        Args:
            simulation: Simulation mode.

        Returns:
            Client.
        """
        ...

    def stock_order(self, fields: dict[str, Any]) -> Any:
        """Creates ``sj.StockOrder``.

        Args:
            fields: Order fields.

        Returns:
            Order.
        """
        ...

    def share_unit(self) -> Any:
        """Returns ``sj.Unit.Share``."""
        ...


class ShioajiFactory:
    """``SjFactory`` backed by the real ``shioaji`` package."""

    def __init__(self) -> None:
        """Imports ``shioaji``.

        Raises:
            AdapterUnavailableError: When the ``sinopac`` extra is missing.
        """
        try:
            self._sj: Any = importlib.import_module("shioaji")
        except ImportError as err:
            raise errors.AdapterUnavailableError(
                "shioaji is not installed (install the 'sinopac' extra)"
            ) from err

    def client(self, simulation: bool) -> SjClient:
        """Creates ``sj.Shioaji``.

        Args:
            simulation: Simulation mode.

        Returns:
            Client.
        """
        client: SjClient = self._sj.Shioaji(simulation=simulation)
        return client

    def stock_order(self, fields: dict[str, Any]) -> Any:
        """Creates ``sj.StockOrder``.

        Args:
            fields: Fields (enum values as their string literals).

        Returns:
            Order.
        """
        return self._sj.StockOrder(**fields)

    def share_unit(self) -> Any:
        """Returns ``sj.Unit.Share``."""
        return self._sj.Unit.Share


# ---------------------------------------------------------------------------
# Pure mappings
# ---------------------------------------------------------------------------


def text(value: object) -> str:
    """Returns an SDK enum's string value (or the value as text).

    Args:
        value: Enum, string or ``None``.

    Returns:
        Text such as ``Submitted``.
    """
    if value is None:
        return ""
    inner = getattr(value, "value", value)
    return str(inner)


def tag(client_order_id: str) -> str:
    """Derives the 6-character ``custom_field`` of a client order id.

    Args:
        client_order_id: Joseph client id.

    Returns:
        Six uppercase letters/digits (Shioaji's custom_field limit): the
        first 8 bytes of the id's SHA-256 in base 36.
    """
    number = int.from_bytes(
        hashlib.sha256(client_order_id.encode()).digest()[:8], "big"
    )
    chars = []
    for _ in range(6):
        number, digit = divmod(number, 36)
        chars.append(_TAG_ALPHABET[digit])
    return "".join(chars)


def order_fields(
    request: trading.VenueOrderRequest, unit: int, account: Any
) -> dict[str, Any]:
    """Maps a venue request to ``sj.StockOrder`` fields.

    Args:
        request: Venue request.
        unit: Shares per board lot (``contract.unit``, 1000 on TWSE and
            TPEx).
        account: Shioaji stock account.

    Returns:
        Field mapping.

    Raises:
        AdapterUnavailableError: For orders Shioaji is not used with here
            (nothing is sent).
    """
    tif = _TIFS.get(request.time_in_force)
    if tif is None:
        raise errors.AdapterUnavailableError(
            f"time in force {request.time_in_force.value} is not available "
            "for Taiwan stock orders (ROD/IOC/FOK)",
            code="UNSUPPORTED_ORDER_TYPE",
        )
    if request.order_type is enums.OrderType.MARKET:
        price_type, price = "MKT", 0.0
    elif request.order_type is enums.OrderType.LIMIT:
        if request.limit_price is None:
            raise AssertionError("LIMIT orders carry a limit price")
        price_type, price = "LMT", base.exact_float(request.limit_price)
    else:
        raise errors.AdapterUnavailableError(
            "Shioaji stock orders are limit or market orders only",
            code="UNSUPPORTED_ORDER_TYPE",
        )
    kind = equities.lot_kind(request.quantity_base, decimal.Decimal(unit))
    if kind is equities.LotKind.MIXED or not decimals.is_multiple(
        request.quantity_base, decimals.ONE
    ):
        raise errors.AdapterUnavailableError(
            "Taiwan orders are whole board lots or one odd lot",
            code="INVALID_LOT_SIZE",
        )
    if kind is equities.LotKind.ODD:
        if price_type != "LMT" or tif != "ROD":
            raise errors.AdapterUnavailableError(
                "intraday odd-lot orders are limit ROD only",
                code="UNSUPPORTED_ORDER_TYPE",
            )
        lot, quantity = _ODD, int(request.quantity_base)
    else:
        lot, quantity = _COMMON, int(request.quantity_base) // unit
    return {
        "action": "Buy" if request.side is enums.OrderSide.BUY else "Sell",
        "price": price,
        "quantity": quantity,
        "price_type": price_type,
        "time_in_force": tif,
        "order_lot": lot,
        "order_cond": "Cash",
        "custom_field": tag(request.client_order_id),
        "account": account,
    }


def shares(quantity: object, lot: str, unit: int) -> decimal.Decimal:
    """Converts an SDK quantity to shares.

    Args:
        quantity: SDK quantity (lots for Common, shares for odd lots).
        lot: Order lot (``Common`` or ``IntradayOdd``).
        unit: Shares per lot.

    Returns:
        Shares.
    """
    count = decimal.Decimal(int(str(quantity or 0)))
    return count * unit if lot == _COMMON else count


def map_status(trade: Any) -> enums.OrderState | None:
    """Maps a Shioaji trade status to a venue state.

    Args:
        trade: ``sj.Trade``.

    Returns:
        State, or ``None`` for statuses with unclear meaning (Inactive).
    """
    status = text(trade.status.status)
    deal = int(str(getattr(trade.status, "deal_quantity", 0) or 0))
    if status in _WORKING:
        return _State.PARTIALLY_FILLED if deal > 0 else _State.ACKNOWLEDGED
    mapping = {
        "PartFilled": _State.PARTIALLY_FILLED,
        "Filled": _State.FILLED,
        "Cancelled": _State.CANCELED,
        "Failed": _State.REJECTED,
    }
    return mapping.get(status)


def mask_identifiers(
    text: str,
    known_accounts: Iterable[str] = (),
    known_ids: Iterable[str] = (),
) -> str:
    """Masks account numbers and person ids in SDK text.

    Args:
        text: Text that may name accounts or the account holder.
        known_accounts: Account numbers to mask even when they do not look
            like a SinoPac account number; their part after the branch
            (``0000001`` of ``9A95-0000001``) is masked too.
        known_ids: Person ids to remove even when they do not look like a
            Taiwan id.

    Returns:
        ``text`` with person ids replaced by ``***`` and account numbers by
        their ``base.mask`` form.
    """
    for person_id in sorted({i for i in known_ids if i}, key=len, reverse=True):
        text = text.replace(person_id, "***")
    text = _PERSON_ID.sub("***", text)
    numbers = {n for n in known_accounts if n}
    numbers |= {n.rpartition("-")[2] for n in numbers}
    numbers.discard("")
    if numbers:
        # One pass, longest first: a number that contains another one is
        # masked whole, and masked text is never masked again.
        known = re.compile(
            "|".join(
                re.escape(n) for n in sorted(numbers, key=len, reverse=True)
            )
        )
        text = known.sub(lambda m: base.mask(m.group()), text)
    return _ACCOUNT_NUMBER.sub(lambda m: base.mask(m.group()), text)


def venue_state(
    client_order_id: str,
    trade: Any,
    unit: int,
    now_ms: int,
    *,
    mask: Callable[[str], str] = mask_identifiers,
) -> trading.VenueOrderState | None:
    """Builds the venue state of a Shioaji trade.

    Args:
        client_order_id: Joseph client id.
        trade: ``sj.Trade``.
        unit: Shares per lot.
        now_ms: Report time.
        mask: Masks identifiers in the SDK's status message, which becomes
            the reason of rejected and canceled orders.

    Returns:
        State, or ``None`` when the status is unclear.
    """
    status = map_status(trade)
    if status is None:
        return None
    lot = text(getattr(trade.order, "order_lot", _COMMON)) or _COMMON
    message = getattr(trade.status, "msg", None)
    final = status in (_State.REJECTED, _State.CANCELED)
    return trading.VenueOrderState(
        client_order_id=client_order_id,
        exchange_order_id=str(trade.order.id) or None,
        status=status,
        filled_base=shares(
            getattr(trade.status, "deal_quantity", 0), lot, unit
        ),
        reason=mask(str(message)) if message and final else None,
        ts_ms=now_ms,
    )


def fill_key(trade_id: str, seq: object) -> str:
    """Builds the fill id of one deal.

    Args:
        trade_id: Shioaji trade id (order id).
        seq: Exchange deal sequence.

    Returns:
        Joseph fill id.
    """
    return f"sinopac-{trade_id}-{seq}"


def market_type(code: str) -> str:
    """Guesses the market type of a Taiwan code (ETF codes start with 00).

    Args:
        code: TWSE or TPEx code.

    Returns:
        ``etf`` or ``stock``.
    """
    return "etf" if code.startswith("00") else "stock"


def exchange_of(contract: Any) -> str:
    """Returns a Shioaji contract's exchange code (``TSE``, ``OTC``, ...).

    Args:
        contract: ``sj.Stock``/``StockInfo`` (1.7 info objects also carry
            it on ``base``) or ``None``.

    Returns:
        Exchange text, empty when unknown.
    """
    if contract is None:
        return ""
    found = text(getattr(contract, "exchange", None))
    if not found:
        found = text(getattr(getattr(contract, "base", None), "exchange", None))
    return found


def venue_of(exchange: str) -> str | None:
    """Maps a Shioaji exchange code to a Joseph venue.

    Args:
        exchange: ``TSE``, ``OTC``, ``OES``, ...

    Returns:
        ``twse`` or ``tpex``; ``None`` for exchanges Joseph does not route
        (e.g. the emerging board ``OES``).
    """
    for venue, aliases in _EXCHANGE_ALIASES.items():
        if exchange in aliases:
            return venue
    return None


def instrument_id(code: str, exchange: str) -> str | None:
    """Builds the Joseph instrument id of a Shioaji stock code.

    Args:
        code: Security code.
        exchange: Contract exchange (``TSE`` or ``OTC``).

    Returns:
        ``twse:...`` or ``tpex:...`` id; ``None`` for any other exchange,
        e.g. the emerging board ``OES`` or an unknown (empty) exchange.
    """
    venue = venue_of(exchange)
    if venue is None:
        return None
    return f"{venue}:{market_type(code)}:{code}"


def unmanaged_holding(code: str, exchange: str) -> tuple[str, str]:
    """Describes a holding outside TWSE and TPEx (not managed by Joseph).

    Args:
        code: Security code.
        exchange: Contract exchange; empty when Shioaji's contract list
            does not know the code.

    Returns:
        ``(instrument id, note)``; the id is namespaced by the Shioaji
        exchange (``oes:stock:7777``) or ``unknown``, never by a Joseph
        venue, so it cannot be mistaken for a routable listing.
    """
    where = exchange.lower() or "unknown"
    held = f"{where}:{market_type(code)}:{code}"
    if exchange == _EMERGING:
        note = (
            f"{code} is an emerging-board (興櫃) holding; Joseph routes TWSE "
            "and TPEx listings only and does not manage it."
        )
    elif exchange:
        note = (
            f"{code} is listed on Shioaji exchange {exchange}, not TWSE or "
            "TPEx; Joseph does not manage it."
        )
    else:
        note = (
            f"{code} is not in Shioaji's contract list, so its exchange is "
            "unknown; Joseph does not manage it."
        )
    return held, note


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class SinopacConfig:
    """Shioaji login and behaviour settings (secrets never repr'd).

    Attributes:
        api_key: API key.
        secret_key: Secret key.
        ca_path: Path of the ``Sinopac.pfx`` certificate.
        ca_password: Certificate password.
        person_id: National id for ``activate_ca``.
        simulation: Shioaji simulation mode.
        call_timeout_ms: SDK order-call timeout (inside the OMS's 5 s
            submit timeout).
        orders_per_10s: Order-call pacing (SinoPac limit 250 per 10 s).
        queries_per_5s: Account-query pacing (SinoPac limit 25 per 5 s).
        cancel_grace_ms: How long a requested cancel stays pending.
    """

    api_key: str = dataclasses.field(repr=False)
    secret_key: str = dataclasses.field(repr=False)
    ca_path: str | None = None
    ca_password: str | None = dataclasses.field(default=None, repr=False)
    person_id: str | None = dataclasses.field(default=None, repr=False)
    simulation: bool = True
    call_timeout_ms: int = 4_000
    orders_per_10s: int = 200
    queries_per_5s: int = 20
    cancel_grace_ms: int = 30_000


@dataclasses.dataclass(slots=True)
class _Tracked:
    """An order this adapter placed or resolved.

    Attributes:
        account_id: Joseph account.
        trade: ``sj.Trade``.
        instrument_id: Joseph instrument id.
        side: Order side.
        unit: Shares per lot.
        lot: Order lot.
        tax_class: Transaction tax class of the listing (fill fees).
    """

    account_id: str
    trade: Any
    instrument_id: str
    side: enums.OrderSide
    unit: int
    lot: str
    tax_class: equities.TaiwanTaxClass


class SinopacAdapter:
    """``TradingAdapter`` for SinoPac Shioaji (TWSE and TPEx).

    Attributes:
        calls: Methods invoked (safety tests assert none while gated).
    """

    def __init__(  # noqa: PLR0917 - catalog.build and the tests construct it positionally
        self,
        config: SinopacConfig | None,
        factory: Callable[[], SjFactory],
        clock: services.Clock,
        account_lookup: base.AccountLookup,
        gate_reason: str | None = None,
        runner: Runner = _thread_runner,
        market_data: market_port.MarketDataGateway | None = None,
    ) -> None:
        """Initializes the adapter.

        Args:
            config: Settings (``None`` when SinoPac is not configured).
            factory: Creates the Shioaji factory (first login only).
            clock: Time source.
            account_lookup: Joseph account id -> SinoPac account number.
            gate_reason: When set, nothing logs in and every call is
                refused with this reason.
            runner: Runs blocking SDK calls.
            market_data: Market gateway (asset class of a listing, which
                selects the transaction tax of fill fees); without it bond
                ETFs are charged the ETF rate.
        """
        self._config = config
        self._market = market_data
        self._tax_classes: dict[str, equities.TaiwanTaxClass] = {}
        self._factory_fn = factory
        self._factory: SjFactory | None = None
        self._clock = clock
        self._lookup = account_lookup
        self._gate = gate_reason
        if self._gate is None and config is None:
            self._gate = (
                "SinoPac credentials are not configured "
                "(JOSEPH_SINOPAC_API_KEY and JOSEPH_SINOPAC_SECRET_KEY)"
            )
        self._runner = runner
        self._sdk_lock = asyncio.Lock()
        simulation = True if config is None else config.simulation
        self._documented = broker_caps.sinopac(simulation=simulation)
        self._api: SjClient | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._health = enums.HealthState.UNAVAILABLE
        self._login_lock = asyncio.Lock()
        orders = 0 if config is None else config.orders_per_10s
        queries = 0 if config is None else config.queries_per_5s
        self._order_pacer = base.Pacer(orders, 10.0)
        self._query_pacer = base.Pacer(queries, 5.0)
        self._accounts: dict[str, str] = {}
        self._tracked: dict[str, _Tracked] = {}
        self._by_trade: dict[str, str] = {}
        self._fills: dict[str, list[trading.VenueFill]] = {}
        self._filled: dict[str, decimal.Decimal] = {}
        self._cancel_requested: dict[str, int] = {}
        self._failures: dict[str, str] = {}
        self._snapshots: dict[str, trading.VenueAccountSnapshot] = {}
        self._position_ids: dict[str, tuple[str, str | None]] = {}
        self._events = base.EventBuffer()
        self._tasks: set[asyncio.Task[Any]] = set()
        self.calls: list[str] = []

    @property
    def name(self) -> str:
        """The broker code, ``sinopac``."""
        return BROKER

    async def _run(self, fn: Callable[[], Any]) -> Any:
        """Runs one blocking SDK call at a time.

        Args:
            fn: Call.

        Returns:
            Its result.
        """
        async with self._sdk_lock:
            return await self._runner(fn)

    async def _tax_class(self, instrument: str) -> equities.TaiwanTaxClass:
        """Classifies a listing for the transaction tax (cached).

        The market engine's asset class marks bond ETFs; without market
        data the listing is charged as a stock or plain ETF.

        Args:
            instrument: Joseph instrument id.

        Returns:
            Tax class.
        """
        cached = self._tax_classes.get(instrument)
        if cached is not None:
            return cached
        meta = None
        if self._market is not None:
            try:
                meta = await self._market.instrument(instrument)
            except errors.ExecutionError:
                meta = None
        found = equities.taiwan_tax_class(
            instrument, None if meta is None else meta.asset_class
        )
        if meta is not None:
            self._tax_classes[instrument] = found
        return found

    # -- session -----------------------------------------------------------

    def _require_open(self, method: str) -> None:
        """Records a call and refuses it while gated.

        This is where a gated deployment fails closed: no login and no SDK
        call happens after a refusal.

        Args:
            method: Method name.

        Raises:
            AdapterUnavailableError: ``LIVE_TRADING_DISABLED`` when gated.
        """
        self.calls.append(method)
        if self._gate is not None:
            raise base.gated_error(BROKER, self._gate)

    async def _login(self) -> SjClient:
        """Logs in (and activates the CA) once.

        The API key and secret go only to the SDK's ``login``; the
        certificate path, password and person id only to ``activate_ca``,
        which production order signing needs. Production without a
        configured certificate fails closed before any order. Errors name
        the SDK exception's type only and do not chain it, so its text
        (which may echo credentials) stays out of tracebacks.

        Returns:
            Logged-in client.

        Raises:
            AdapterUnavailableError: When login or CA activation fails.
        """
        async with self._login_lock:
            if self._api is not None:
                return self._api
            cfg = self._config
            if cfg is None:
                raise AssertionError("an ungated adapter has a config")
            if self._factory is None:
                self._factory = self._factory_fn()
            factory = self._factory
            self._loop = asyncio.get_running_loop()
            api = factory.client(cfg.simulation)
            api.set_order_callback(self._order_callback)
            api.set_event_callback(self._event_callback)
            try:
                await self._run(
                    lambda: api.login(
                        api_key=cfg.api_key,
                        secret_key=cfg.secret_key,
                        subscribe_trade=True,
                    )
                )
            except Exception as err:  # noqa: BLE001 - the SDK raises arbitrary types; every failure fails closed, and from None drops its text, which may echo the login
                raise errors.AdapterUnavailableError(
                    f"Shioaji login failed ({type(err).__name__})"
                ) from None
            needs_ca = not cfg.simulation
            if needs_ca and not (cfg.ca_path and cfg.ca_password):
                raise errors.AdapterUnavailableError(
                    "production orders need the SinoPac CA certificate "
                    "(JOSEPH_SINOPAC_CA_PATH and JOSEPH_SINOPAC_CA_PASSWORD)"
                )
            if cfg.ca_path and cfg.ca_password:
                path, password = cfg.ca_path, cfg.ca_password
                try:
                    active = await self._run(
                        lambda: api.activate_ca(
                            ca_path=path,
                            ca_passwd=password,
                            person_id=cfg.person_id,
                        )
                    )
                except Exception as err:  # noqa: BLE001 - the SDK raises arbitrary types; every failure fails closed, and from None drops its text, which may echo the certificate details
                    raise errors.AdapterUnavailableError(
                        f"Shioaji CA activation failed ({type(err).__name__})"
                    ) from None
                if not active:
                    raise errors.AdapterUnavailableError(
                        "Shioaji CA activation was refused"
                    )
            self._api = api
            self._health = enums.HealthState.HEALTHY
            return api

    def _broker_account(self, account_id: str) -> str:
        """Resolves (and remembers) the SinoPac account of a Joseph account.

        Args:
            account_id: Joseph account.

        Returns:
            Account number.

        Raises:
            AdapterUnavailableError: When none is bound.
        """
        number = self._accounts.get(account_id)
        if number is None:
            number = self._lookup(account_id)
            if not number:
                raise errors.AdapterUnavailableError(
                    "no SinoPac account number is connected to this account"
                )
            self._accounts[account_id] = number
        return number

    def bind(self, account_id: str) -> bool:
        """Binds a Joseph account for routing broker events.

        Args:
            account_id: Joseph account.

        Returns:
            True when an account number is known.
        """
        if self._gate is not None:
            return False
        try:
            self._broker_account(account_id)
        except errors.AdapterUnavailableError:
            return False
        return True

    async def _stock_account(self, api: SjClient, number: str) -> Any | None:
        """Finds the Shioaji stock account with a number.

        Args:
            api: Client.
            number: ``broker_id-account_id`` or ``account_id``.

        Returns:
            Account or ``None``.
        """
        for item in await self._run(api.list_accounts):
            kind = text(getattr(item, "account_type", ""))
            if kind not in ("S", "Stock"):
                continue
            full = f"{item.broker_id}-{item.account_id}"
            if number in (str(item.account_id), full):
                return item
        return None

    async def _session(
        self, method: str, account_id: str
    ) -> tuple[SjClient, Any]:
        """Opens a gated, logged-in session for a Joseph account.

        Args:
            method: Calling method (recorded).
            account_id: Joseph account.

        Returns:
            ``(client, stock account)``.

        Raises:
            AdapterUnavailableError: When the account is not available.
        """
        self._require_open(method)
        number = self._broker_account(account_id)
        api = await self._login()
        account = await self._stock_account(api, number)
        if account is None:
            raise errors.AdapterUnavailableError(
                f"the SinoPac login has no stock account {base.mask(number)}"
            )
        return api, account

    async def close(self) -> None:
        """Logs out."""
        api, self._api = self._api, None
        if api is not None:
            await self._run(api.logout)

    # -- callbacks -----------------------------------------------------------

    def _order_callback(self, state: Any, event: Any) -> None:
        """Receives SDK order/deal events on an SDK thread.

        Adapter state is only touched on the event loop, so the event is
        handed over with ``call_soon_threadsafe``.

        Args:
            state: ``sj.OrderState``.
            event: Event mapping.
        """
        loop = self._loop
        if loop is not None:
            loop.call_soon_threadsafe(self._on_order, state, event)

    def _event_callback(
        self, resp_code: int, event_code: int, info: str, event: str
    ) -> None:
        """Receives SDK session events on an SDK thread.

        Args:
            resp_code: Response code.
            event_code: Session event code.
            info: Text.
            event: Event name.
        """
        del resp_code, event  # Unused.
        loop = self._loop
        if loop is not None:
            loop.call_soon_threadsafe(self._on_session, event_code, info)

    def _mask(self, text: str) -> str:
        """Masks identifiers in SDK text before it is kept or logged.

        Args:
            text: SDK text (session info, order message).

        Returns:
            ``mask_identifiers`` of the text with the bound account numbers
            and the configured person id.
        """
        person_id = None if self._config is None else self._config.person_id
        return mask_identifiers(
            text, self._accounts.values(), [person_id] if person_id else []
        )

    def _state(
        self, client_order_id: str, trade: Any, unit: int, now_ms: int
    ) -> trading.VenueOrderState | None:
        """Builds a trade's venue state with identifiers masked.

        Args:
            client_order_id: Joseph client id.
            trade: ``sj.Trade``.
            unit: Shares per lot.
            now_ms: Report time.

        Returns:
            ``venue_state`` of the trade.
        """
        return venue_state(
            client_order_id, trade, unit, now_ms, mask=self._mask
        )

    def _on_session(self, event_code: int, info: str) -> None:
        """Tracks session health; resyncs after a reconnect.

        The text is logged with account numbers and person ids masked.

        Args:
            event_code: Session event code.
            info: Text.
        """
        # Shioaji session events: 0 up, 13 reconnected, 12 reconnecting,
        # 1 down, 2 connect failed.
        if event_code in (0, 13):
            self._health = enums.HealthState.HEALTHY
            if event_code == 13:
                task = asyncio.ensure_future(self.resync())
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)
        elif event_code == 12:
            self._health = enums.HealthState.DEGRADED
        elif event_code in (1, 2):
            self._health = enums.HealthState.UNAVAILABLE
        _LOG.info("Shioaji session event %s: %s", event_code, self._mask(info))

    def _client_id(self, trade_id: str, custom: str) -> str | None:
        """Finds the Joseph client id of a Shioaji trade.

        Args:
            trade_id: Trade id.
            custom: ``custom_field``.

        Returns:
            Client id or ``None``.
        """
        cid = self._by_trade.get(trade_id)
        if cid is not None:
            return cid
        matches = [c for c in self._tracked if custom and tag(c) == custom]
        return matches[0] if len(matches) == 1 else None

    def _on_order(self, state: Any, event: Any) -> None:
        """Dispatches an order or deal event.

        Args:
            state: ``sj.OrderState``.
            event: Event mapping.
        """
        kind = text(state)
        if kind in ("SDEAL", "StockDeal"):
            self._on_deal(event)
        elif kind in ("SORDER", "StockOrder"):
            self._on_order_event(event)

    def _on_order_event(self, event: Any) -> None:
        """Applies a StockOrder event (new, cancel, update, reject).

        The SDK's message is masked (``mask_identifiers``) before it becomes
        an order reason or a failure.

        Args:
            event: Event mapping.
        """
        operation = event.get("operation") or {}
        order = event.get("order") or {}
        trade_id = str(order.get("id") or "")
        cid = self._client_id(trade_id, str(order.get("custom_field") or ""))
        if cid is None:
            return
        tracked = self._tracked[cid]
        op_type = str(operation.get("op_type") or "")
        ok = str(operation.get("op_code") or "") == "00"
        # The SDK's message can name the account; only the masked text is
        # reported or raised.
        message = self._mask(str(operation.get("op_msg") or "")) or None
        filled = self._filled.get(cid, decimals.ZERO)
        if op_type == "New" and ok:
            status = (
                _State.PARTIALLY_FILLED if filled > 0 else _State.ACKNOWLEDGED
            )
        elif op_type == "Cancel" and ok:
            status = _State.CANCELED
            self._cancel_requested.pop(cid, None)
        elif op_type in ("New", "Reject"):
            status = _State.REJECTED
        else:
            self._cancel_requested.pop(cid, None)
            self._failures[cid] = message or f"{op_type} failed"
            return
        self._events.order(
            tracked.account_id,
            trading.VenueOrderState(
                client_order_id=cid,
                exchange_order_id=trade_id or None,
                status=status,
                filled_base=filled,
                reason=message if status is not _State.ACKNOWLEDGED else None,
                ts_ms=self._clock.now_ms(),
            ),
        )

    def _on_deal(self, event: Any) -> None:
        """Applies a StockDeal event (one fill).

        Args:
            event: Event mapping.
        """
        trade_id = str(event.get("trade_id") or "")
        cid = self._client_id(trade_id, str(event.get("custom_field") or ""))
        if cid is None:
            return
        tracked = self._tracked[cid]
        lot = str(event.get("order_lot") or tracked.lot)
        qty = shares(event.get("quantity"), lot, tracked.unit)
        price = base.exact_decimal(float(event.get("price") or 0.0))
        seq = event.get("exchange_seq") or event.get("seqno")
        ts_ms = (
            base.to_ms(float(event.get("ts") or 0.0)) or self._clock.now_ms()
        )
        fill = trading.VenueFill(
            exchange_fill_id=fill_key(trade_id, seq),
            client_order_id=cid,
            price=price,
            quantity_base=qty,
            fee=equities.taiwan_fee(
                tracked.side, qty * price, tracked.tax_class, ts_ms
            ),
            fee_asset="TWD",
            liquidity="taker",
            ts_ms=ts_ms,
        )
        if not self._events.fill(tracked.account_id, fill):
            return
        self._fills.setdefault(tracked.account_id, []).append(fill)
        total = self._filled.get(cid, decimals.ZERO) + qty
        self._filled[cid] = total
        ordered = shares(
            getattr(tracked.trade.order, "quantity", 0),
            tracked.lot,
            tracked.unit,
        )
        self._events.order(
            tracked.account_id,
            trading.VenueOrderState(
                client_order_id=cid,
                exchange_order_id=trade_id or None,
                status=_State.FILLED
                if total >= ordered
                else _State.PARTIALLY_FILLED,
                filled_base=total,
                reason=None,
                ts_ms=self._clock.now_ms(),
            ),
        )

    # -- sync ----------------------------------------------------------------

    async def _refresh(self, api: SjClient, account: Any) -> list[Any]:
        """Refreshes trade statuses from SinoPac.

        Args:
            api: Client.
            account: Stock account.

        Returns:
            Trades of the session.
        """
        await self._order_pacer.acquire()
        await self._run(lambda: api.update_status(account))
        trades: list[Any] = await self._run(api.list_trades)
        return trades

    def _match(self, client_order_id: str, trades: Sequence[Any]) -> Any | None:
        """Finds the trade of a client id by trade id or custom_field tag.

        Args:
            client_order_id: Client id.
            trades: Trades.

        Returns:
            Trade or ``None``.
        """
        tracked = self._tracked.get(client_order_id)
        if tracked is not None:
            for trade in trades:
                if str(trade.order.id) == str(tracked.trade.order.id):
                    return trade
            return tracked.trade
        wanted = tag(client_order_id)
        found = [
            t
            for t in trades
            if str(getattr(t.order, "custom_field", "") or "") == wanted
        ]
        return found[0] if len(found) == 1 else None

    async def resync(self) -> int:
        """Re-reads trade statuses and buffers changes.

        Returns:
            Events buffered.
        """
        if self._gate is not None or self._api is None:
            return 0
        api = self._api
        emitted = 0
        accounts_seen: set[str] = set()
        for joseph, number in list(self._accounts.items()):
            if number in accounts_seen:
                continue
            accounts_seen.add(number)
            account = await self._stock_account(api, number)
            if account is None:
                continue
            trades = await self._refresh(api, account)
            for cid, tracked in list(self._tracked.items()):
                if tracked.account_id != joseph:
                    continue
                trade = self._match(cid, trades)
                if trade is None:
                    continue
                tracked.trade = trade
                state = self._state(
                    cid, trade, tracked.unit, self._clock.now_ms()
                )
                if state is not None and self._events.order(joseph, state):
                    emitted += 1
        return emitted

    def event_accounts(self) -> list[str]:
        """Lists Joseph accounts with buffered events.

        Returns:
            Account ids.
        """
        return self._events.account_ids()

    def drain_events(self, account_id: str) -> list[trading.VenueEvent]:
        """Returns and clears buffered events of an account.

        Args:
            account_id: Joseph account.

        Returns:
            Events.
        """
        return self._events.drain(account_id)

    # -- TradingAdapter ------------------------------------------------------

    async def capabilities(
        self, account_id: str
    ) -> accounts.TradingCapabilities:
        """Negotiates capabilities (logs in when allowed).

        Fails closed: a gated deployment, an account without a SinoPac
        account number or stock account, a failed login and, in production,
        an account SinoPac reports as not signed (API agreement and
        simulation test outstanding) all report TRADING_UNAVAILABLE.

        Args:
            account_id: Joseph account.

        Returns:
            AVAILABLE, or TRADING_UNAVAILABLE with the reason.
        """
        if self._gate is not None:
            return base.unavailable(self._documented, self._gate)
        try:
            _, account = await self._session("capabilities", account_id)
        except errors.AdapterUnavailableError as err:
            return base.unavailable(self._documented, err.message)
        if not self._documented.simulation and not getattr(
            account, "signed", False
        ):
            return base.unavailable(
                self._documented,
                "SinoPac reports the stock account as not signed: sign the "
                "API agreement and pass the simulation-mode test (login and "
                "place_order) before production trading.",
            )
        return self._documented

    async def health(self) -> enums.HealthState:
        """Returns session health."""
        if self._gate is not None:
            return enums.HealthState.UNAVAILABLE
        return self._health

    async def account_snapshot(
        self, account_id: str
    ) -> trading.VenueAccountSnapshot:
        """Reads cash, pending settlements and the trading limit.

        Buying power is the bank balance net of pending T+1/T+2
        settlements, capped by SinoPac's trading limit; equity adds the
        market value of positions. Cached for five seconds (SinoPac allows
        25 account queries per 5 s).

        Args:
            account_id: Joseph account.

        Returns:
            Snapshot in TWD.
        """
        api, account = await self._session("account_snapshot", account_id)
        now = self._clock.now_ms()
        cached = self._snapshots.get(account_id)
        if cached is not None and now - cached.ts_ms < _SNAPSHOT_TTL_MS:
            return cached
        await self._query_pacer.acquire()
        balance = await self._run(lambda: api.account_balance(account))
        cash = base.exact_decimal(float(balance.acc_balance))
        await self._query_pacer.acquire()
        rows = await self._run(lambda: api.settlements(account))
        unsettled = sum(
            (
                base.exact_decimal(float(getattr(r, "t1_money", 0.0) or 0.0))
                + base.exact_decimal(float(getattr(r, "t2_money", 0.0) or 0.0))
                for r in rows
            ),
            decimals.ZERO,
        )
        await self._query_pacer.acquire()
        limits = await self._run(lambda: api.trading_limits(account))
        available = cash + unsettled
        limit = getattr(limits, "trading_available", None)
        if limit is not None:
            available = min(available, decimal.Decimal(int(limit)))
        positions = await self._positions(api, account)
        value = sum((v for _, v in positions), decimals.ZERO)
        snapshot = trading.VenueAccountSnapshot(
            equity=cash + unsettled + value,
            available_margin=available,
            currency="TWD",
            ts_ms=now,
            settled_cash=cash,
            unsettled_cash=unsettled,
        )
        self._snapshots[account_id] = snapshot
        return snapshot

    async def _positions(
        self, api: SjClient, account: Any
    ) -> list[tuple[trading.VenuePosition, decimal.Decimal]]:
        """Reads positions in shares with their market value.

        Args:
            api: Client.
            account: Stock account.

        Returns:
            ``(position, market value)`` pairs.
        """
        if self._factory is None:
            raise AssertionError("the SDK factory exists after login")
        unit = self._factory.share_unit()
        await self._query_pacer.acquire()
        rows = await self._run(lambda: api.list_positions(account, unit=unit))
        known: dict[str, str] = {}
        for tracked in self._tracked.values():
            ref = equities.parse_instrument_id(tracked.instrument_id)
            if equities.is_taiwan(ref.venue):
                known[ref.symbol] = tracked.instrument_id
        result = []
        for row in rows:
            code = str(row.code)
            qty = decimal.Decimal(int(str(row.quantity or 0)))
            if text(row.direction) == "Sell":
                qty = -qty
            held, note = (
                (known[code], None)
                if code in known
                else await self._holding(api, code)
            )
            last = base.exact_decimal(
                float(getattr(row, "last_price", 0.0) or 0.0)
            )
            result.append(
                (
                    trading.VenuePosition(
                        instrument_id=held,
                        signed_qty=qty,
                        entry_price=base.exact_decimal(float(row.price)),
                        unmanaged_note=note,
                    ),
                    qty * last,
                )
            )
        return result

    async def _holding(
        self, api: SjClient, code: str
    ) -> tuple[str, str | None]:
        """Resolves the listing of a held code.

        Positions carry no exchange, so the exchange field of the code's
        Shioaji contract decides: ``TSE`` is TWSE and ``OTC`` is TPEx.
        Anything else (the emerging board ``OES``, or a code missing from
        the contract list) is reported as unmanaged rather than guessed.
        Codes with a contract are cached; missing ones are looked up again
        next time (the contract list may still be downloading).

        Args:
            api: Client.
            code: Security code.

        Returns:
            ``(instrument id, unmanaged note)``; the note is ``None`` for
            TWSE and TPEx listings.
        """
        cached = self._position_ids.get(code)
        if cached is not None:
            return cached
        try:
            contract = await self._run(lambda: api.contracts.stocks[code])
        except (KeyError, IndexError):
            contract = None
        exchange = exchange_of(contract)
        listed = instrument_id(code, exchange)
        resolved: tuple[str, str | None] = (
            (listed, None)
            if listed is not None
            else unmanaged_holding(code, exchange)
        )
        if exchange:
            self._position_ids[code] = resolved
        return resolved

    async def list_positions(
        self, account_id: str
    ) -> Sequence[trading.VenuePosition]:
        """Lists the account's holdings in shares.

        TWSE and TPEx listings carry their instrument id; other holdings
        (emerging board, codes Shioaji cannot resolve) are listed with an
        ``unmanaged_note`` so reconciliation leaves them alone.

        Args:
            account_id: Joseph account.

        Returns:
            Positions.
        """
        api, account = await self._session("list_positions", account_id)
        return [p for p, _ in await self._positions(api, account)]

    async def list_open_orders(
        self, account_id: str
    ) -> Sequence[trading.VenueOrderState]:
        """Lists working Joseph orders of the account.

        Args:
            account_id: Joseph account.

        Returns:
            States.
        """
        api, account = await self._session("list_open_orders", account_id)
        trades = await self._refresh(api, account)
        now = self._clock.now_ms()
        result = []
        for cid, tracked in self._tracked.items():
            if tracked.account_id != account_id:
                continue
            trade = self._match(cid, trades)
            state = (
                None
                if trade is None
                else self._state(cid, trade, tracked.unit, now)
            )
            if state is not None and state.status in (
                _State.ACKNOWLEDGED,
                _State.PARTIALLY_FILLED,
            ):
                result.append(state)
        return result

    async def list_recent_fills(
        self, account_id: str, since_ms: int = 0
    ) -> Sequence[trading.VenueFill]:
        """Lists fills received by this process since a time.

        Args:
            account_id: Joseph account.
            since_ms: Lower bound.

        Returns:
            Fills (deal reports of the current session).

        Raises:
            AdapterUnavailableError: ``LIVE_TRADING_DISABLED`` when gated.
        """
        self._require_open("list_recent_fills")
        return [
            f for f in self._fills.get(account_id, []) if f.ts_ms >= since_ms
        ]

    async def submit_order(
        self, request: trading.VenueOrderRequest
    ) -> trading.VenueSubmitResult:
        """Places a TWSE or TPEx stock order.

        The code is looked up in the venue's Shioaji contract group
        (``TSE`` for ``twse``, ``OTC`` for ``tpex``), so a code listed on
        the other exchange (or on the emerging board) is refused. A client
        id this process already placed returns its current state instead
        of placing a second order. SDK errors are reported by type only and
        not chained, so their text (which may name the account) stays out
        of tracebacks.

        Args:
            request: Venue request.

        Returns:
            Result (deals arrive through the callback).

        Raises:
            AdapterUnavailableError: Refused before sending.
            AdapterTimeoutError: Sent, but the outcome is unknown.
        """
        api, account = await self._session("submit_order", request.account_id)
        cid = request.client_order_id
        now = self._clock.now_ms()
        existing = self._tracked.get(cid)
        if existing is not None:
            state = self._state(cid, existing.trade, existing.unit, now)
            if state is None:
                raise errors.AdapterTimeoutError("SinoPac order is unclear")
            return trading.VenueSubmitResult(
                state.status is not _State.REJECTED, state
            )
        ref = equities.parse_instrument_id(request.instrument_id)
        group = SJ_EXCHANGES.get(ref.venue)
        if group is None:
            raise errors.AdapterUnavailableError(
                "SinoPac routes TWSE and TPEx listings only, not "
                f"{request.instrument_id}",
                code="UNSUPPORTED",
            )
        name = equities.DISPLAY_NAMES[ref.venue]
        try:
            contract = await self._run(
                lambda: getattr(api.contracts.stocks, group)[ref.symbol]
            )
        except (KeyError, IndexError, AttributeError):
            raise errors.AdapterUnavailableError(
                f"unknown {name} code {ref.symbol}", code="UNSUPPORTED"
            ) from None
        if venue_of(exchange_of(contract)) != ref.venue:
            raise errors.AdapterUnavailableError(
                f"{ref.symbol} is not a {name} listing", code="UNSUPPORTED"
            )
        unit = int(float(getattr(contract, "unit", 1000) or 1000))
        fields = order_fields(request, unit, account)
        # The OMS read this metadata for its risk checks, so it is cached.
        tax_class = await self._tax_class(request.instrument_id)
        if self._factory is None:
            raise AssertionError("the SDK factory exists after login")
        order = self._factory.stock_order(fields)
        timeout = (
            4_000 if self._config is None else self._config.call_timeout_ms
        )
        await self._order_pacer.acquire()
        try:
            trade = await self._run(
                lambda: api.place_order(contract, order, timeout=timeout)
            )
        except Exception as err:  # noqa: BLE001 - the SDK raises arbitrary types; from None drops their text, which may echo the account
            if type(err).__name__ in _REFUSED:
                raise errors.AdapterUnavailableError(
                    f"SinoPac refused the order ({type(err).__name__})"
                ) from None
            raise errors.AdapterTimeoutError(
                f"SinoPac order outcome unknown ({type(err).__name__})"
            ) from None
        self._tracked[cid] = _Tracked(
            request.account_id,
            trade,
            request.instrument_id,
            request.side,
            unit,
            str(fields["order_lot"]),
            tax_class,
        )
        self._by_trade[str(trade.order.id)] = cid
        state = self._state(cid, trade, unit, self._clock.now_ms())
        if state is None:
            raise errors.AdapterTimeoutError("SinoPac order status unclear")
        return trading.VenueSubmitResult(
            state.status is not _State.REJECTED, state
        )

    async def cancel_order(
        self, account_id: str, client_order_id: str
    ) -> trading.VenueOrderState:
        """Requests cancellation (the answer arrives as an order event).

        Args:
            account_id: Joseph account.
            client_order_id: Client id.

        Returns:
            State when SinoPac reports it final.

        Raises:
            ExecutionError: ``NOT_FOUND`` for unknown orders.
            AdapterTimeoutError: Cancel pending (the OMS reconciles).
        """
        api, account = await self._session("cancel_order", account_id)
        tracked = self._tracked.get(client_order_id)
        trade = tracked.trade if tracked is not None else None
        if trade is None:
            trade = self._match(
                client_order_id, await self._refresh(api, account)
            )
        if trade is None:
            raise errors.ExecutionError("NOT_FOUND", "unknown SinoPac order")
        unit = 1000 if tracked is None else tracked.unit
        now = self._clock.now_ms()
        state = self._state(client_order_id, trade, unit, now)
        if state is not None and state.status not in (
            _State.ACKNOWLEDGED,
            _State.PARTIALLY_FILLED,
        ):
            return state
        self._cancel_requested[client_order_id] = now
        self._failures.pop(client_order_id, None)
        await self._order_pacer.acquire()
        updated = await self._run(lambda: api.cancel_order(trade))
        state = self._state(client_order_id, updated or trade, unit, now)
        if state is not None and state.status in (
            _State.CANCELED,
            _State.FILLED,
        ):
            self._cancel_requested.pop(client_order_id, None)
            return state
        raise errors.AdapterTimeoutError("SinoPac cancel pending")

    async def amend_order(
        self,
        account_id: str,
        client_order_id: str,
        limit_price: decimal.Decimal | None,
        trigger_price: decimal.Decimal | None,
        quantity_base: decimal.Decimal | None,
    ) -> trading.VenueOrderState:
        """Changes the price or reduces the quantity of a working order.

        Args:
            account_id: Joseph account.
            client_order_id: Client id.
            limit_price: New limit price (not for odd lots).
            trigger_price: Unsupported (no stop orders).
            quantity_base: New, smaller quantity in shares.

        Returns:
            State after the update.

        Raises:
            ExecutionError: ``NOT_FOUND``, ``UNSUPPORTED``,
                ``INVALID_LOT_SIZE`` or ``CONFLICT``.
        """
        api, _ = await self._session("amend_order", account_id)
        tracked = self._tracked.get(client_order_id)
        if tracked is None:
            raise errors.ExecutionError("NOT_FOUND", "unknown SinoPac order")
        if trigger_price is not None:
            raise errors.ExecutionError(
                "UNSUPPORTED", "SinoPac stock orders have no trigger price"
            )
        if limit_price is not None and tracked.lot == _ODD:
            raise errors.ExecutionError(
                "UNSUPPORTED", "intraday odd-lot prices cannot be changed"
            )
        trade = tracked.trade
        self._failures.pop(client_order_id, None)
        if quantity_base is not None:
            current = shares(trade.order.quantity, tracked.lot, tracked.unit)
            if quantity_base >= current:
                raise errors.ExecutionError(
                    "UNSUPPORTED", "SinoPac can only reduce an order quantity"
                )
            per = tracked.unit if tracked.lot == _COMMON else 1
            if not decimals.is_multiple(quantity_base, decimal.Decimal(per)):
                raise errors.ExecutionError(
                    "INVALID_LOT_SIZE", "quantity must stay in whole lots"
                )
            new_qty = int(quantity_base) // per
            await self._order_pacer.acquire()
            trade = (
                await self._run(lambda: api.update_order(trade, qty=new_qty))
                or trade
            )
        if limit_price is not None:
            price = base.exact_float(limit_price)
            current_trade = trade
            await self._order_pacer.acquire()
            trade = (
                await self._run(
                    lambda: api.update_order(current_trade, price=price)
                )
                or trade
            )
        tracked.trade = trade
        problem = self._failures.get(client_order_id)
        if problem is not None:
            raise errors.ExecutionError("CONFLICT", problem)
        state = self._state(
            client_order_id, trade, tracked.unit, self._clock.now_ms()
        )
        if state is None:
            raise errors.ExecutionError("CONFLICT", "SinoPac order is unclear")
        return state

    async def get_order_by_client_id(
        self, account_id: str, client_order_id: str
    ) -> trading.VenueOrderState | None:
        """Finds an order after refreshing statuses from SinoPac.

        Absence is confirmed only for orders young enough to be in today's
        trade list; otherwise the outcome stays unknown.

        Args:
            account_id: Joseph account.
            client_order_id: Client id.

        Returns:
            State, or ``None`` when SinoPac confirms it never had it.

        Raises:
            AdapterTimeoutError: When SinoPac cannot confirm either way.
        """
        api, account = await self._session("get_order_by_client_id", account_id)
        trades = await self._refresh(api, account)
        trade = self._match(client_order_id, trades)
        now = self._clock.now_ms()
        if trade is None:
            created = base.client_order_ms(client_order_id)
            if created is None or now - created > _TODAY_WINDOW_MS:
                raise errors.AdapterTimeoutError(
                    "SinoPac cannot confirm the absence of an older order"
                )
            return None
        tracked = self._tracked.get(client_order_id)
        unit = 1000 if tracked is None else tracked.unit
        if tracked is None:
            lot = text(getattr(trade.order, "order_lot", _COMMON)) or _COMMON
            side = (
                enums.OrderSide.SELL
                if text(trade.order.action) == "Sell"
                else enums.OrderSide.BUY
            )
            code = str(getattr(trade.contract, "code", ""))
            found = instrument_id(code, exchange_of(trade.contract))
            if found is None:
                found, _ = await self._holding(api, code)
            self._tracked[client_order_id] = _Tracked(
                account_id,
                trade,
                found,
                side,
                unit,
                lot,
                await self._tax_class(found),
            )
            self._by_trade[str(trade.order.id)] = client_order_id
        else:
            tracked.trade = trade
        state = self._state(client_order_id, trade, unit, now)
        requested = self._cancel_requested.get(client_order_id)
        grace = 30_000 if self._config is None else self._config.cancel_grace_ms
        pending_cancel = (
            requested is not None
            and state is not None
            and state.status in (_State.ACKNOWLEDGED, _State.PARTIALLY_FILLED)
            and now - requested < grace
        )
        if state is None or pending_cancel:
            raise errors.AdapterTimeoutError("SinoPac order state pending")
        return state

    async def stream_account_events(
        self, account_id: str
    ) -> AsyncIterator[trading.VenueEvent]:
        """Streams buffered events of an account.

        Args:
            account_id: Joseph account.

        Yields:
            Events.

        Raises:
            AdapterUnavailableError: ``LIVE_TRADING_DISABLED`` when gated.
        """
        self._require_open("stream_account_events")
        async for event in self._events.stream(account_id):
            yield event
