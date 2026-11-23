"""Lighter (zkLighter) perpetuals trading adapter.

Sources (read 2026-11-23):

* Environments (mainnet chain 304, testnet chain 300):
  https://docs.lighter.xyz/developers/getting-started/environments
* Core concepts (integer amounts, nonces, which rejections consume a
  nonce, ``code 200`` = accepted not executed):
  https://docs.lighter.xyz/developers/getting-started/core-concepts
* API keys (indexes 0-3 reserved, 4-254 usable; auth tokens):
  https://docs.lighter.xyz/developers/integration-guides/api-keys
* Signing transactions (``client_order_index`` uint48, cancel/modify by
  client index, expiry rules):
  https://docs.lighter.xyz/developers/integration-guides/signing-transactions
* Rate limits: https://docs.lighter.xyz/developers/reference/rate-limits
* Errors and statuses:
  https://docs.lighter.xyz/developers/reference/data-structures-constants-and-errors
* WebSocket: https://docs.lighter.xyz/api/websocket
* Official SDK ``lighter-sdk`` 1.1.9 (Apache-2.0), ``lighter/signer_client.py``
  and ``nonce_manager.py``: https://github.com/elliottech/lighter-python
  (bundled native signers for Linux x86_64/arm64, built from
  github.com/elliottech/lighter-go, Apache-2.0).

Protocol facts this module relies on:

* Mainnet REST ``https://mainnet.zklighter.elliot.ai``, WebSocket
  ``wss://mainnet.zklighter.elliot.ai/stream``. Testnet
  ``https://api.testnet.lighter.xyz`` (the SDK's ``testnet.zklighter``
  host no longer resolves) and the signer must be created with
  ``chain_id=300`` there.
* Transactions are signed by the official ``SignerClient`` (native
  library) with an explicit ``(nonce, api_key_index)`` so this adapter owns
  nonce management: the nonce comes from ``GET /api/v1/nextNonce`` and is
  incremented locally under a per-key lock; an API-level rejection does
  not consume it, ``21104`` (invalid nonce) refetches it, and an unknown
  outcome forces a refetch before the next transaction (never a blind
  resend).
* ``POST /api/v1/sendTx`` (form ``tx_type``, ``tx_info``) answering
  ``code 200`` means accepted, not executed; the order appears in
  ``accountOrders``/``account_all_orders`` once the sequencer runs it.
* Amounts are integers: ``base_amount = size * 10**size_decimals``,
  ``price = price * 10**price_decimals`` (``orderBooks``); market and
  stop-market orders carry a worst acceptable price.
* ``client_order_index`` is a uint48 unique among open orders; Joseph
  derives it from the client order id (``wire.index_of``) and cancels and
  modifies by it.
* Limits: Standard accounts 60 requests per minute (Premium 24000
  weighted), ``sendTx`` 40 per minute per transaction type by default.
* Private channels need an auth token (``create_auth_token_with_expiry``,
  at most 8 hours). Clients must send a frame at least every 2 minutes.
"""

from __future__ import annotations

import asyncio
import dataclasses
import decimal
import importlib
import json
import logging
from collections.abc import Callable
from collections.abc import Mapping
from collections.abc import Sequence
from typing import Any
from typing import Protocol

from joseph_execution.adapters.venues import credentials as creds_lib
from joseph_execution.adapters.venues import http
from joseph_execution.adapters.venues import live
from joseph_execution.adapters.venues import ratelimit
from joseph_execution.adapters.venues import wire
from joseph_execution.adapters.venues import ws
from joseph_execution.domain import decimals
from joseph_execution.domain import enums
from joseph_execution.domain import errors
from joseph_execution.ports import trading

_LOG = logging.getLogger(__name__)
S = enums.OrderState
VENUE = "lighter"
REST_URL = "https://mainnet.zklighter.elliot.ai"
TESTNET_REST_URL = "https://api.testnet.lighter.xyz"
CHAIN_IDS = {False: 304, True: 300}
ORDER_LIMIT = 0
ORDER_MARKET = 1
ORDER_STOP_LOSS = 2
ORDER_STOP_LOSS_LIMIT = 3
TIF_IOC = 0
TIF_GTT = 1
TIF_POST_ONLY = 2
DEFAULT_EXPIRY = -1
IOC_EXPIRY = 0
INDEX_BITS = 48
_META_TTL_S = 600.0
_TOKEN_S = 7 * 3600
_TERMINAL_REJECT = {
    "canceled-post-only",
    "canceled-reduce-only",
    "canceled-position-not-allowed",
    "canceled-margin-not-allowed",
    "canceled-invalid-balance",
    "canceled-too-much-slippage",
}
_MARGIN = {21739, 21508, 21507, 21301, 21111, 21112}
_PERMISSION = {21120, 21108, 21109, 21110, 20013, 21136, 52002}
_RATE = {23000, 21506, 30009, 30010}
_NONCE = {21104, 21105}
_NOT_FOUND = {21600, 21709, 21715, 21707}
_TICK = {21702}
_LOT = {21701, 21706}


class Signer(Protocol):
    """The subset of ``lighter.SignerClient`` this adapter uses."""

    def check_client(self) -> str | None:
        """Verifies the key against the one registered on Lighter.

        Returns:
            Error text or ``None``.
        """
        ...

    def create_auth_token_with_expiry(
        self, deadline: int = ..., *, api_key_index: int = ...
    ) -> tuple[str | None, str | None]:
        """Creates an auth token.

        Args:
            deadline: Lifetime in seconds.
            api_key_index: Key index.

        Returns:
            ``(token, error)``.
        """
        ...

    def sign_create_order(self, *args: Any, **kwargs: Any) -> tuple[Any, ...]:
        """Signs a create-order transaction.

        Args:
            *args: Positional order fields.
            **kwargs: ``nonce`` and ``api_key_index``.

        Returns:
            ``(tx_type, tx_info, tx_hash, error)``.
        """
        ...

    def sign_cancel_order(self, *args: Any, **kwargs: Any) -> tuple[Any, ...]:
        """Signs a cancel transaction.

        Args:
            *args: ``market_index``, ``order_index``.
            **kwargs: ``nonce`` and ``api_key_index``.

        Returns:
            ``(tx_type, tx_info, tx_hash, error)``.
        """
        ...

    def sign_modify_order(self, *args: Any, **kwargs: Any) -> tuple[Any, ...]:
        """Signs a modify transaction.

        Args:
            *args: Market, index, base amount, price, trigger price.
            **kwargs: ``nonce`` and ``api_key_index``.

        Returns:
            ``(tx_type, tx_info, tx_hash, error)``.
        """
        ...


SignerFactory = Callable[[str, int, int, str, int], Signer]


def sdk_signer(
    url: str, account_index: int, api_key_index: int, key: str, chain_id: int
) -> Signer:
    """Builds the official ``SignerClient`` without SDK nonce management.

    Args:
        url: REST base URL (the signer validates it, never calls it here).
        account_index: Account index.
        api_key_index: API key index.
        key: API private key.
        chain_id: 304 mainnet, 300 testnet.

    Returns:
        Signer.

    Raises:
        AdapterUnavailableError: The ``lighter`` extra is not installed or
            the native signer cannot load on this platform.
    """
    try:
        sdk = importlib.import_module("lighter")
        nonces = importlib.import_module("lighter.nonce_manager")
    except ImportError:
        raise errors.AdapterUnavailableError(
            "Lighter signing needs the optional dependency: install "
            "joseph-execution-engine[lighter]",
            code="UNSUPPORTED",
        ) from None
    try:
        client: Signer = sdk.SignerClient(
            url,
            account_index,
            {api_key_index: key},
            nonce_management_type=nonces.NonceManagerType.NONE,
            chain_id=chain_id,
        )
    except Exception as err:
        raise errors.AdapterUnavailableError(
            f"the Lighter signer could not load the key ({type(err).__name__})",
            code="AUTH_SCOPE_MISSING",
        ) from None
    return client


def sdk_available() -> bool:
    """Tells whether ``lighter-sdk`` imports.

    Returns:
        True when installed.
    """
    try:
        importlib.import_module("lighter")
    except ImportError:
        return False
    return True


def map_error(status: int, payload: Any) -> errors.ExecutionError:
    """Maps a Lighter error answer to a domain error.

    Args:
        status: HTTP status.
        payload: ``{"code": 21739, "message": "..."}``.

    Returns:
        Domain error.
    """
    code = status
    message = str(payload)[:200]
    if isinstance(payload, Mapping):
        try:
            code = int(payload.get("code", status))
        except (TypeError, ValueError):
            code = status
        message = str(payload.get("message", ""))[:200]
    venue_code = str(code)
    text = f"{message} ({venue_code})"
    family: dict[frozenset[int], Callable[[], errors.ExecutionError]] = {
        frozenset(_MARGIN): lambda: errors.InsufficientMarginError(
            text, venue=VENUE, venue_code=venue_code
        ),
        frozenset(_PERMISSION): lambda: errors.VenuePermissionError(
            "permission missing: the Lighter API key is not registered for "
            f"this account/index or the account tier forbids it ({text})",
            venue=VENUE,
            venue_code=venue_code,
        ),
        frozenset(_RATE): lambda: errors.RateLimitedError(
            f"lighter throttled: {text}"
        ),
        frozenset(_NOT_FOUND): lambda: errors.OrderRejectedError(
            text, venue=VENUE, venue_code=venue_code, code="NOT_FOUND"
        ),
        frozenset(_TICK): lambda: errors.OrderRejectedError(
            text, venue=VENUE, venue_code=venue_code, code="INVALID_TICK_SIZE"
        ),
        frozenset(_LOT): lambda: errors.OrderRejectedError(
            text, venue=VENUE, venue_code=venue_code, code="INVALID_LOT_SIZE"
        ),
    }
    if status >= 500:
        return errors.AdapterTimeoutError(
            f"lighter answered HTTP {status}; outcome unknown"
        )
    for codes, build in family.items():
        if code in codes:
            return build()
    if status in (401, 403):
        return family[frozenset(_PERMISSION)]()
    return errors.OrderRejectedError(text, venue=VENUE, venue_code=venue_code)


@dataclasses.dataclass(frozen=True, slots=True)
class Market:
    """Perpetual market metadata from ``orderBooks``.

    Attributes:
        market_id: Market index.
        symbol: Symbol.
        size_decimals: Base amount decimals.
        price_decimals: Price decimals.
    """

    market_id: int
    symbol: str
    size_decimals: int
    price_decimals: int


@dataclasses.dataclass(slots=True)
class LighterSession(live.Session):
    """Per-account Lighter state.

    Attributes:
        account_index: Account index.
        api_key_index: API key index.
        signer: Native signer (created lazily).
        nonce: Next nonce to use (``None`` = refetch).
        lock: Serializes sign-and-send on the key.
        token: Auth token and its expiry (epoch seconds).
        indexes: Client order index to client id.
        sent: Client id to the tx hash of its create order (this process).
    """

    account_index: int = 0
    api_key_index: int = 0
    signer: Signer | None = None
    nonce: int | None = None
    lock: asyncio.Lock = dataclasses.field(default_factory=asyncio.Lock)
    token: tuple[str, int] | None = None
    indexes: dict[int, str] = dataclasses.field(default_factory=dict)
    sent: dict[str, str] = dataclasses.field(default_factory=dict)


def _scaled(value: decimal.Decimal, places: int) -> int | None:
    """Scales a decimal to the venue integer, refusing inexact values.

    Args:
        value: Decimal.
        places: Decimals of the market.

    Returns:
        Integer, or ``None`` when ``value`` has more decimals.
    """
    scaled = value.scaleb(places)
    if scaled != scaled.to_integral_value():
        return None
    return int(scaled)


def _status(name: str) -> S:
    """Maps a Lighter order status.

    Args:
        name: ``open``, ``filled``, ``canceled-…`` etc.

    Returns:
        OMS state (``ACKNOWLEDGED`` for in-progress/pending/open).
    """
    if name == "filled":
        return S.FILLED
    if name == "canceled-expired":
        return S.EXPIRED
    if name in _TERMINAL_REJECT:
        return S.REJECTED
    if name.startswith("canceled"):
        return S.CANCELED
    return S.ACKNOWLEDGED


class LighterAdapter(live.LiveVenueAdapter[LighterSession]):
    """Lighter perpetuals with the official native signer."""

    REQUIRED_FIELDS = ("api_private_key", "account_index", "api_key_index")
    SANDBOX_NAME = "the Lighter testnet"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initializes the adapter (see ``LiveVenueAdapter``).

        Args:
            *args: Base arguments.
            **kwargs: Base keyword arguments plus ``signer_factory``
                (``urls`` accepts ``rest_url`` and ``testnet_rest_url``).
        """
        self._signer_factory: SignerFactory = (
            kwargs.pop("signer_factory", None) or sdk_signer
        )
        super().__init__(*args, **kwargs)
        self._urls = {
            False: self._url("rest_url", REST_URL),
            True: self._url("testnet_rest_url", TESTNET_REST_URL),
        }
        self._http: dict[bool, http.VenueHttp] = {}
        self._markets: dict[bool, tuple[float, dict[str, Market]]] = {}

    def _client(self, sandbox: bool) -> http.VenueHttp:
        """Returns the REST client of an environment.

        Args:
            sandbox: Testnet.

        Returns:
            Client (Standard tier: 60 requests per minute).
        """
        client = self._http.get(sandbox)
        if client is None:
            client = http.VenueHttp(
                VENUE,
                self._urls[sandbox],
                ratelimit.WindowLimiter(
                    VENUE,
                    [
                        ratelimit.Budget("requests", 60, 60.0),
                        ratelimit.Budget("tx", 40, 60.0),
                    ],
                    max_wait_s=self._options.max_rate_wait_s,
                    monotonic=self._monotonic,
                    sleep=self._sleep,
                ),
                timeout_s=self._options.timeout_s,
                transport=self._transport,
            )
            self._http[sandbox] = client
        return client

    async def aclose(self) -> None:
        """Closes connection pools and the signers' SDK sessions."""
        for client in self._http.values():
            await client.aclose()
        for _, session in self._sessions.values():
            closer = getattr(session.signer, "close", None)
            if closer is not None:
                await closer()

    def _credential_problem(
        self, credentials: creds_lib.VenueCredentials
    ) -> str | None:
        """Validates indexes and the SDK before any network use.

        Args:
            credentials: Credentials.

        Returns:
            Problem or ``None``.
        """
        problem = super()._credential_problem(credentials)
        if problem is not None:
            return problem
        try:
            account = int(credentials.get("account_index"))
            key_index = int(credentials.get("api_key_index"))
        except ValueError:
            return "account_index and api_key_index must be integers"
        if account < 0 or not 4 <= key_index <= 254:
            return (
                "api_key_index must be 4-254 (0-3 are reserved for "
                "Lighter's own apps)"
            )
        if self._signer_factory is sdk_signer and not sdk_available():
            return (
                "Lighter signing needs the optional dependency "
                "(joseph-execution-engine[lighter])"
            )
        return None

    def _new_session(
        self,
        account_id: str,
        credentials: creds_lib.VenueCredentials,
        sandbox: bool,
    ) -> LighterSession:
        """Builds per-account state.

        Args:
            account_id: Account.
            credentials: Credentials.
            sandbox: Testnet.

        Returns:
            Session.
        """
        return LighterSession(
            account_id=account_id,
            credentials=credentials,
            sandbox=sandbox,
            account_index=int(credentials.get("account_index")),
            api_key_index=int(credentials.get("api_key_index")),
        )

    def _signer(self, session: LighterSession) -> Signer:
        """Returns the session's native signer.

        Args:
            session: Session.

        Returns:
            Signer.
        """
        if session.signer is None:
            session.signer = self._signer_factory(
                self._urls[session.sandbox],
                session.account_index,
                session.api_key_index,
                session.credentials.get("api_private_key"),
                CHAIN_IDS[session.sandbox],
            )
        return session.signer

    def _token(self, session: LighterSession) -> str:
        """Returns a cached auth token (7 h, refreshed 5 min early).

        Args:
            session: Session.

        Returns:
            Token.

        Raises:
            VenuePermissionError: The signer refused to create one.
        """
        now = self._now_ms() // 1000
        if session.token is not None and session.token[1] - 300 > now:
            return session.token[0]
        token, err = self._signer(session).create_auth_token_with_expiry(
            _TOKEN_S, api_key_index=session.api_key_index
        )
        if err or not token:
            raise errors.VenuePermissionError(
                "permission missing: Lighter auth token refused", venue=VENUE
            )
        session.token = (token, now + _TOKEN_S)
        return token

    async def _get(
        self,
        session_or_env: LighterSession | bool,
        path: str,
        params: Sequence[tuple[str, str]] = (),
        *,
        auth: bool = False,
    ) -> Any:
        """Sends a GET and checks ``code``.

        Args:
            session_or_env: Session (for auth) or the sandbox flag.
            path: Path.
            params: Query parameters.
            auth: Send the ``Authorization`` token.

        Returns:
            Payload.
        """
        if isinstance(session_or_env, LighterSession):
            sandbox = session_or_env.sandbox
        else:
            sandbox = session_or_env
        headers = {}
        if auth and isinstance(session_or_env, LighterSession):
            headers["Authorization"] = self._token(session_or_env)
        query = "&".join(f"{k}={v}" for k, v in params)
        reply = await self._client(sandbox).request(
            "GET", path, query=query, headers=headers, costs={"requests": 1}
        )
        payload = reply.payload
        if reply.status == 200 and isinstance(payload, Mapping):
            code = payload.get("code", 200)
            if code in (0, 200):
                return payload
        raise map_error(reply.status, payload)

    async def _markets_of(self, sandbox: bool) -> dict[str, Market]:
        """Returns cached perp markets (``GET /api/v1/orderBooks``).

        Args:
            sandbox: Testnet (market ids differ: testnet starts at 4095).

        Returns:
            Markets by symbol.
        """
        now = self._monotonic()
        cached = self._markets.get(sandbox)
        if cached is not None and now - cached[0] < _META_TTL_S:
            return cached[1]
        payload = await self._get(sandbox, "/api/v1/orderBooks")
        markets = {
            str(book["symbol"]): Market(
                int(book["market_id"]),
                str(book["symbol"]),
                int(book["supported_size_decimals"]),
                int(book["supported_price_decimals"]),
            )
            for book in payload.get("order_books", [])
            if book.get("market_type", "perp") == "perp"
        }
        self._markets[sandbox] = (now, markets)
        return markets

    async def _market(self, session: LighterSession, symbol: str) -> Market:
        """Looks up one market.

        Args:
            session: Session.
            symbol: Symbol.

        Returns:
            Market.

        Raises:
            AdapterUnavailableError: Unknown market (not sent).
        """
        market = (await self._markets_of(session.sandbox)).get(symbol)
        if market is None:
            raise errors.AdapterUnavailableError(
                f"lighter has no perpetual {symbol!r}", code="UNSUPPORTED"
            )
        return market

    async def _symbol_of_market(
        self, session: LighterSession, market_id: int
    ) -> str:
        """Finds the symbol of a market id.

        Args:
            session: Session.
            market_id: Market index.

        Returns:
            Symbol.
        """
        for market in (await self._markets_of(session.sandbox)).values():
            if market.market_id == market_id:
                return market.symbol
        return str(market_id)

    # -- transactions ---------------------------------------------------

    async def _send(
        self,
        session: LighterSession,
        sign: Callable[[int], tuple[Any, ...]],
    ) -> str:
        """Signs with the next nonce and posts ``/api/v1/sendTx``.

        Args:
            session: Session.
            sign: Signs a transaction for a nonce; returns
                ``(tx_type, tx_info, tx_hash, error)``.

        Returns:
            Transaction hash (accepted, not yet executed).

        Raises:
            ExecutionError: Mapped rejections; ``AdapterTimeoutError`` when
                the outcome is unknown (the nonce is refetched next time).
        """
        async with session.lock:
            for attempt in range(2):
                if session.nonce is None:
                    payload = await self._get(
                        session,
                        "/api/v1/nextNonce",
                        [
                            ("account_index", str(session.account_index)),
                            ("api_key_index", str(session.api_key_index)),
                        ],
                    )
                    session.nonce = int(payload["nonce"])
                nonce = session.nonce
                tx_type, tx_info, tx_hash, err = sign(nonce)
                if err:
                    raise errors.OrderRejectedError(
                        f"lighter signer refused the transaction: {err}",
                        venue=VENUE,
                    )
                try:
                    reply = await self._client(session.sandbox).request(
                        "POST",
                        "/api/v1/sendTx",
                        form={"tx_type": str(tx_type), "tx_info": tx_info},
                        costs={"requests": 1, "tx": 1},
                    )
                except errors.AdapterTimeoutError:
                    session.nonce = None
                    raise
                payload = reply.payload
                code = (
                    payload.get("code")
                    if isinstance(payload, Mapping)
                    else None
                )
                if reply.status == 200 and code == 200:
                    session.nonce = nonce + 1
                    return str((payload or {}).get("tx_hash") or tx_hash or "")
                error = map_error(reply.status, payload)
                if isinstance(error, errors.AdapterTimeoutError):
                    session.nonce = None
                    raise error
                if code in _NONCE and attempt == 0:
                    session.nonce = None
                    continue
                raise error
        raise errors.AdapterUnavailableError("lighter nonce resync failed")

    # -- negotiation ----------------------------------------------------

    async def _probe_health(self, sandbox: bool) -> None:
        """Checks ``GET /`` (status and network id).

        Args:
            sandbox: Testnet.
        """
        reply = await self._client(sandbox).request(
            "GET", "/", costs={"requests": 1}
        )
        if reply.status != 200:
            raise map_error(reply.status, reply.payload)

    async def _probe_permissions(
        self, session: LighterSession
    ) -> tuple[str, ...]:
        """Verifies the API key with the signer and reads the account.

        ``SignerClient.check_client`` compares the key with the public key
        registered for ``(account_index, api_key_index)``.

        Args:
            session: Session.

        Returns:
            Notes.

        Raises:
            VenuePermissionError: Unregistered or mismatched key.
        """
        problem = await asyncio.to_thread(self._signer(session).check_client)
        if problem:
            raise errors.VenuePermissionError(
                "permission missing: this API key is not registered for "
                f"account {session.account_index} at index "
                f"{session.api_key_index} ({problem[:120]})",
                venue=VENUE,
            )
        await self._snapshot(session)
        return (
            f"API key index {session.api_key_index} verified for account "
            f"{session.account_index}.",
            "Standard tier limits apply (60 requests per minute).",
            "Leverage and margin mode are the account's market settings.",
            "Orders are accepted asynchronously; the stream confirms them.",
        )

    # -- reads ----------------------------------------------------------

    async def _account(self, session: LighterSession) -> Mapping[str, Any]:
        """Reads ``GET /api/v1/account?by=index``.

        Args:
            session: Session.

        Returns:
            Account object.
        """
        payload = await self._get(
            session,
            "/api/v1/account",
            [("by", "index"), ("value", str(session.account_index))],
        )
        rows = payload.get("accounts") or []
        if not rows:
            raise errors.VenuePermissionError(
                "permission missing: Lighter account not found", venue=VENUE
            )
        account: Mapping[str, Any] = rows[0]
        return account

    async def _snapshot(
        self, session: LighterSession
    ) -> trading.VenueAccountSnapshot:
        """Reads balances (``total_asset_value``, ``available_balance``).

        Args:
            session: Session.

        Returns:
            Snapshot in USDC.
        """
        account = await self._account(session)
        return trading.VenueAccountSnapshot(
            equity=wire.dec(account.get("total_asset_value")),
            available_margin=wire.dec(account.get("available_balance")),
            currency="USDC",
            ts_ms=self._now_ms(),
        )

    def _position(self, row: Mapping[str, Any]) -> trading.VenuePosition:
        """Converts a position (``position`` unsigned, ``sign`` ±1).

        Args:
            row: Position object.

        Returns:
            Position.
        """
        qty = wire.dec(row.get("position"))
        if int(row.get("sign", 1)) < 0:
            qty = -qty
        return trading.VenuePosition(
            instrument_id=self._instrument(str(row.get("symbol"))),
            signed_qty=qty,
            entry_price=wire.opt_dec(row.get("avg_entry_price")),
        )

    async def _positions(
        self, session: LighterSession
    ) -> list[trading.VenuePosition]:
        """Reads positions from the account.

        Args:
            session: Session.

        Returns:
            Non-zero positions.
        """
        account = await self._account(session)
        return [
            self._position(row)
            for row in account.get("positions", [])
            if wire.dec(row.get("position")) != 0
        ]

    def _cid(self, session: LighterSession, index: object) -> str:
        """Maps a ``client_order_index`` back to a client id.

        Args:
            session: Session.
            index: Client order index.

        Returns:
            Client id (``cidx:<n>`` when unknown).
        """
        try:
            value = int(str(index))
        except ValueError:
            return f"cidx:{index}"
        cid = session.indexes.get(value)
        if cid is None:
            for known in self._directory.recent(session.account_id, 0):
                session.indexes.setdefault(
                    wire.index_of(known.client_order_id, INDEX_BITS),
                    known.client_order_id,
                )
            cid = session.indexes.get(value)
        return cid or f"cidx:{value}"

    def _order(
        self, session: LighterSession, row: Mapping[str, Any]
    ) -> trading.VenueOrderState:
        """Converts an order object.

        Args:
            session: Session.
            row: Order object.

        Returns:
            State.
        """
        status = str(row.get("status"))
        mapped = _status(status)
        filled = wire.dec(row.get("filled_base_amount"))
        if mapped is S.ACKNOWLEDGED and filled > 0:
            mapped = S.PARTIALLY_FILLED
        ts = int(row.get("updated_at") or row.get("timestamp") or 0)
        if ts and ts < 10_000_000_000:
            ts *= 1000
        return trading.VenueOrderState(
            client_order_id=self._cid(session, row.get("client_order_index")),
            exchange_order_id=str(row.get("order_index")),
            status=mapped,
            filled_base=filled,
            reason=status if mapped in (S.REJECTED, S.CANCELED) else None,
            ts_ms=ts,
        )

    async def _open_orders(
        self, session: LighterSession
    ) -> list[trading.VenueOrderState]:
        """Reads ``accountActiveOrders`` for every market (auth).

        Args:
            session: Session.

        Returns:
            Orders.
        """
        payload = await self._get(
            session,
            "/api/v1/accountActiveOrders",
            [
                ("account_index", str(session.account_index)),
                ("market_id", "255"),
            ],
            auth=True,
        )
        return [self._order(session, row) for row in payload.get("orders", [])]

    async def _fills(
        self, session: LighterSession, since_ms: int
    ) -> list[trading.VenueFill]:
        """Reads ``GET /api/v1/trades`` for the account (auth, newest 100).

        Args:
            session: Session.
            since_ms: Lower bound.

        Returns:
            Fills of known client orders, oldest first.
        """
        payload = await self._get(
            session,
            "/api/v1/trades",
            [
                ("sort_by", "timestamp"),
                ("limit", "100"),
                ("account_index", str(session.account_index)),
            ],
            auth=True,
        )
        fills = []
        for row in payload.get("trades", []):
            fill = self._fill(session, row)
            if fill is not None and fill.ts_ms >= since_ms:
                fills.append(fill)
        fills.sort(key=lambda f: f.ts_ms)
        return fills

    def _fill(
        self, session: LighterSession, row: Mapping[str, Any]
    ) -> trading.VenueFill | None:
        """Converts a trade to this account's fill.

        Fees are rates in millionths of notional (``maker_fee``/
        ``taker_fee``), applied to ``usd_amount``.

        Args:
            session: Session.
            row: Trade.

        Returns:
            Fill, or ``None`` for trades of other accounts or foreign orders.
        """
        mine = str(session.account_index)
        is_ask = str(row.get("ask_account_id")) == mine
        if not is_ask and str(row.get("bid_account_id")) != mine:
            return None
        cid = self._cid(
            session, row.get("ask_client_id" if is_ask else "bid_client_id")
        )
        if cid.startswith("cidx:"):
            return None
        maker = bool(row.get("is_maker_ask")) == is_ask
        rate = wire.dec(row.get("maker_fee" if maker else "taker_fee"))
        price = wire.dec(row.get("price"))
        size = wire.dec(row.get("size"))
        notional = wire.opt_dec(row.get("usd_amount")) or price * size
        ts = int(row.get("timestamp", 0))
        return trading.VenueFill(
            exchange_fill_id=str(row.get("trade_id")),
            client_order_id=cid,
            price=price,
            quantity_base=size,
            fee=notional * rate / decimal.Decimal(1_000_000),
            fee_asset="USDC",
            liquidity="maker" if maker else "taker",
            ts_ms=ts if ts > 10_000_000_000 else ts * 1000,
        )

    async def _query(
        self, session: LighterSession, client_order_id: str
    ) -> trading.VenueOrderState | None:
        """Queries ``accountOrders?client_order_indexes=`` (auth).

        A create transaction this process sent that is still pending is
        reported as unknown (raise), never as absent.

        Args:
            session: Session.
            client_order_id: Client id.

        Returns:
            State or ``None`` when Lighter confirms no such order.

        Raises:
            AdapterTimeoutError: The create transaction is still pending.
        """
        index = wire.index_of(client_order_id, INDEX_BITS)
        session.indexes[index] = client_order_id
        payload = await self._get(
            session,
            "/api/v1/accountOrders",
            [
                ("account_index", str(session.account_index)),
                ("client_order_indexes", str(index)),
            ],
            auth=True,
        )
        rows = payload.get("orders") or []
        if rows:
            return self._order(session, rows[0])
        tx_hash = session.sent.get(client_order_id)
        if tx_hash:
            tx = await self._get(
                session, "/api/v1/tx", [("by", "hash"), ("value", tx_hash)]
            )
            status = int(
                tx.get("status", (tx.get("tx") or {}).get("status", 0))
            )
            if status != 0:
                raise errors.AdapterTimeoutError(
                    "lighter create transaction not executed yet"
                )
        return None

    # -- trading --------------------------------------------------------

    async def _worst_price(
        self, session: LighterSession, market: Market, buy: bool
    ) -> decimal.Decimal:
        """Derives a market order's worst price from the last trade.

        Args:
            session: Session.
            market: Market.
            buy: Buy side.

        Returns:
            Price with the slippage band applied.

        Raises:
            AdapterUnavailableError: No reference price (not sent).
        """
        payload = await self._get(
            session,
            "/api/v1/orderBookDetails",
            [("market_id", str(market.market_id))],
        )
        details = (payload.get("order_book_details") or [{}])[0]
        last = wire.opt_dec(details.get("last_trade_price"))
        if last is None or last <= 0:
            raise errors.AdapterUnavailableError(
                f"no lighter reference price for {market.symbol}"
            )
        band = decimal.Decimal(self._options.market_slippage_bps) / decimals.BPS
        return last * (decimals.ONE + band if buy else decimals.ONE - band)

    async def _fields(
        self,
        session: LighterSession,
        market: Market,
        request: trading.VenueOrderRequest,
    ) -> tuple[int, int, int, int, int]:
        """Maps an order to Lighter's integer fields.

        Market and stop-market orders get a worst price (reference price
        and slippage band, rounded against the order); expiry is 0 for IOC
        and the signer's 28-day default otherwise.

        Args:
            session: Session.
            market: Market.
            request: Order.

        Returns:
            ``(price, order_type, time_in_force, trigger_price, expiry)``.

        Raises:
            AdapterUnavailableError: FOK (not supported, not sent).
            OrderRejectedError: Prices off the grid (not sent).
        """
        buy = request.side is enums.OrderSide.BUY
        kind = request.order_type
        tif = request.time_in_force
        if tif is enums.TimeInForce.FOK:
            raise errors.AdapterUnavailableError(
                "lighter has no FOK", code="UNSUPPORTED_ORDER_TYPE"
            )
        rounding = decimal.ROUND_UP if buy else decimal.ROUND_DOWN
        quantum = decimal.Decimal(1).scaleb(-market.price_decimals)
        trigger = 0
        if kind is enums.OrderType.MARKET:
            worst = await self._worst_price(session, market, buy)
            price = worst.quantize(quantum, rounding=rounding)
            order_type, venue_tif, expiry = ORDER_MARKET, TIF_IOC, IOC_EXPIRY
        elif kind is enums.OrderType.LIMIT:
            assert request.limit_price is not None
            price = request.limit_price
            order_type = ORDER_LIMIT
            venue_tif = {
                enums.TimeInForce.IOC: TIF_IOC,
                enums.TimeInForce.POST_ONLY: TIF_POST_ONLY,
            }.get(tif, TIF_GTT)
            expiry = IOC_EXPIRY if venue_tif == TIF_IOC else DEFAULT_EXPIRY
        else:
            assert request.trigger_price is not None
            scaled_trigger = _scaled(
                request.trigger_price, market.price_decimals
            )
            if scaled_trigger is None:
                raise errors.OrderRejectedError(
                    "trigger price is off the price grid",
                    venue=VENUE,
                    code="INVALID_TICK_SIZE",
                )
            trigger = scaled_trigger
            if kind is enums.OrderType.STOP_MARKET:
                band = decimal.Decimal(self._options.market_slippage_bps)
                factor = band / decimals.BPS
                worst = request.trigger_price * (
                    decimals.ONE + factor if buy else decimals.ONE - factor
                )
                price = worst.quantize(quantum, rounding=rounding)
                order_type, venue_tif = ORDER_STOP_LOSS, TIF_IOC
            else:
                assert request.limit_price is not None
                price = request.limit_price
                order_type, venue_tif = ORDER_STOP_LOSS_LIMIT, TIF_GTT
            expiry = DEFAULT_EXPIRY
        scaled_price = _scaled(price, market.price_decimals)
        if scaled_price is None or scaled_price <= 0:
            raise errors.OrderRejectedError(
                "price is off the price grid",
                venue=VENUE,
                code="INVALID_TICK_SIZE",
            )
        return scaled_price, order_type, venue_tif, trigger, expiry

    async def _submit(
        self, session: LighterSession, request: trading.VenueOrderRequest
    ) -> trading.VenueSubmitResult:
        """Signs and sends a create-order transaction.

        Args:
            session: Session.
            request: Order.

        Returns:
            ``ACKNOWLEDGED`` once accepted (the sequencer confirms later).

        Raises:
            OrderRejectedError: Amounts off the market grid (not sent).
            AdapterUnavailableError: FOK (not supported).
        """
        market = await self._market(
            session, self._symbol(request.instrument_id)
        )
        buy = request.side is enums.OrderSide.BUY
        base = _scaled(request.quantity_base, market.size_decimals)
        if base is None or base <= 0:
            raise errors.OrderRejectedError(
                f"size must have at most {market.size_decimals} decimals",
                venue=VENUE,
                code="INVALID_LOT_SIZE",
            )
        fields = await self._fields(session, market, request)
        scaled_price, order_type, venue_tif, trigger, expiry = fields
        cid = request.client_order_id
        index = wire.index_of(cid, INDEX_BITS)
        session.indexes[index] = cid
        signer = self._signer(session)

        def sign(nonce: int) -> tuple[Any, ...]:
            """Signs the order for a nonce.

            Args:
                nonce: Nonce.

            Returns:
                Signer result.
            """
            return signer.sign_create_order(
                market.market_id,
                index,
                base,
                scaled_price,
                not buy,
                order_type,
                venue_tif,
                request.reduce_only,
                trigger,
                expiry,
                nonce=nonce,
                api_key_index=session.api_key_index,
            )

        tx_hash = await self._send(session, sign)
        session.sent[cid] = tx_hash
        return trading.VenueSubmitResult(
            True,
            trading.VenueOrderState(
                cid,
                None,
                S.ACKNOWLEDGED,
                decimals.ZERO,
                "accepted by lighter",
                self._now_ms(),
            ),
        )

    async def _market_of_order(
        self, session: LighterSession, client_order_id: str
    ) -> Market:
        """Finds the market of an order (OMS directory).

        Args:
            session: Session.
            client_order_id: Client id.

        Returns:
            Market.

        Raises:
            AdapterUnavailableError: The OMS does not know the order.
        """
        known = self._directory.find(session.account_id, client_order_id)
        if known is None:
            raise errors.AdapterUnavailableError(
                f"lighter needs the market of {client_order_id}"
            )
        return await self._market(session, self._symbol(known.instrument_id))

    async def _cancel(
        self, session: LighterSession, client_order_id: str
    ) -> trading.VenueOrderState:
        """Signs a cancel by client order index.

        Args:
            session: Session.
            client_order_id: Client id.

        Returns:
            ``CANCEL_PENDING`` (the sequencer confirms through the stream),
            or the venue state when the order already finished.
        """
        current = await self._query(session, client_order_id)
        if current is None:
            raise errors.OrderRejectedError(
                "order not found", venue=VENUE, code="NOT_FOUND"
            )
        if current.status.is_terminal:
            return current
        market = await self._market_of_order(session, client_order_id)
        index = wire.index_of(client_order_id, INDEX_BITS)
        signer = self._signer(session)
        await self._send(
            session,
            lambda nonce: signer.sign_cancel_order(
                market.market_id,
                index,
                nonce=nonce,
                api_key_index=session.api_key_index,
            ),
        )
        return dataclasses.replace(
            current, status=S.CANCEL_PENDING, reason="cancel accepted"
        )

    async def _amend(
        self,
        session: LighterSession,
        client_order_id: str,
        limit_price: decimal.Decimal | None,
        trigger_price: decimal.Decimal | None,
        quantity_base: decimal.Decimal | None,
    ) -> trading.VenueOrderState:
        """Signs a modify by client order index.

        Args:
            session: Session.
            client_order_id: Client id.
            limit_price: New price.
            trigger_price: New trigger.
            quantity_base: New quantity.

        Returns:
            Current state (the modification lands asynchronously).
        """
        payload = await self._get(
            session,
            "/api/v1/accountOrders",
            [
                ("account_index", str(session.account_index)),
                (
                    "client_order_indexes",
                    str(wire.index_of(client_order_id, INDEX_BITS)),
                ),
            ],
            auth=True,
        )
        rows = payload.get("orders") or []
        if not rows:
            raise errors.OrderRejectedError(
                "order not found", venue=VENUE, code="NOT_FOUND"
            )
        row = rows[0]
        market = await self._market_of_order(session, client_order_id)
        size = quantity_base or wire.dec(row.get("remaining_base_amount"))
        price = limit_price or wire.dec(row.get("price"))
        trigger = trigger_price or wire.dec(row.get("trigger_price"))
        base = _scaled(size, market.size_decimals)
        scaled_price = _scaled(price, market.price_decimals)
        scaled_trigger = _scaled(trigger, market.price_decimals)
        if base is None or scaled_price is None or scaled_trigger is None:
            raise errors.OrderRejectedError(
                "amendment is off the market grid", venue=VENUE
            )
        signer = self._signer(session)
        await self._send(
            session,
            lambda nonce: signer.sign_modify_order(
                market.market_id,
                wire.index_of(client_order_id, INDEX_BITS),
                base,
                scaled_price,
                scaled_trigger,
                nonce=nonce,
                api_key_index=session.api_key_index,
            ),
        )
        return self._order(session, row)

    # -- private stream -------------------------------------------------

    async def parse(
        self, session: LighterSession, frame: str | bytes
    ) -> list[ws.StreamUpdate]:
        """Parses one stream frame.

        Args:
            session: Session.
            frame: Raw frame.

        Returns:
            Updates.
        """
        message = http.parse_json(
            frame if isinstance(frame, str) else frame.decode()
        )
        if not isinstance(message, Mapping):
            return []
        if "error" in message:
            error = map_error(200, message["error"])
            if isinstance(error, errors.VenuePermissionError):
                raise error
            _LOG.warning("lighter stream error: %s", error.message)
            return []
        kind = str(message.get("type", ""))
        updates: list[ws.StreamUpdate] = []
        if kind.endswith("account_all_orders"):
            for rows in (message.get("orders") or {}).values():
                updates.extend(
                    ws.StreamUpdate(order=self._order(session, r)) for r in rows
                )
        elif kind.endswith("account_all_trades"):
            trades = message.get("trades") or {}
            groups = (
                trades.values() if isinstance(trades, Mapping) else [trades]
            )
            for rows in groups:
                for row in rows:
                    fill = self._fill(session, row)
                    if fill is not None:
                        updates.append(ws.StreamUpdate(fill=fill))
        elif kind.endswith("account_all_positions"):
            positions = message.get("positions") or {}
            rows = (
                positions.values()
                if isinstance(positions, Mapping)
                else positions
            )
            for row in rows:
                named = dict(row)
                if "symbol" not in named:
                    named["symbol"] = await self._symbol_of_market(
                        session, int(named.get("market_id", -1))
                    )
                updates.append(ws.StreamUpdate(position=self._position(named)))
        return updates

    def _stream_spec(self, session: LighterSession) -> ws.PrivateStreamSpec:
        """Describes the account stream.

        Args:
            session: Session.

        Returns:
            Spec (authenticated ``account_all_orders``, plus
            ``account_all_trades`` and ``account_all_positions``; a
            ``{"type": "ping"}`` every 60 s).
        """
        url = (
            self._urls[session.sandbox].replace("https://", "wss://")
            + "/stream"
        )

        async def connect_url() -> str:
            """Returns the stream URL."""
            return url

        async def on_open(connection: ws.WsConnection) -> None:
            """Subscribes the account channels.

            Args:
                connection: Connection.
            """
            account = session.account_index
            token = self._token(session)
            for channel, auth in (
                (f"account_all_orders/{account}", True),
                (f"account_all_trades/{account}", True),
                (f"account_all_positions/{account}", True),
            ):
                message: dict[str, Any] = {
                    "type": "subscribe",
                    "channel": channel,
                }
                if auth:
                    message["auth"] = token
                await connection.send(json.dumps(message))

        async def resync() -> Sequence[ws.StreamUpdate]:
            """Replays the REST snapshot."""
            return await self._resync(session)

        return ws.PrivateStreamSpec(
            name="lighter account stream",
            url=connect_url,
            on_open=on_open,
            parse=lambda frame: self.parse(session, frame),
            resync=resync,
            ping_message=json.dumps({"type": "ping"}),
            ping_interval_s=60.0,
            idle_timeout_s=150.0,
            max_session_s=6.5 * 3600,
        )
