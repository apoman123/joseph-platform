# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Hyperliquid perpetuals trading adapter (API wallet / agent signing).

Sources (read 2026-11-23):

* API overview, exchange and info endpoints:
  https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api,
  ``.../api/exchange-endpoint``, ``.../api/info-endpoint`` and
  ``.../api/info-endpoint/perpetuals``
* Nonces and API wallets: ``.../api/nonces-and-api-wallets``
* Signing pitfalls: ``.../api/signing``
* Tick and lot size: ``.../api/tick-and-lot-size``
* Rate limits: ``.../api/rate-limits-and-user-limits``
* WebSocket, subscriptions, heartbeats: ``.../api/websocket``,
  ``.../api/websocket/subscriptions``,
  ``.../api/websocket/timeouts-and-heartbeats``
* Error strings: ``.../api/error-responses``
* Official SDK (MIT), ``hyperliquid/utils/signing.py`` and
  ``tests/signing_test.py`` at commit 2fdb18f9 (2026-06-04):
  https://github.com/hyperliquid-dex/hyperliquid-python-sdk

Protocol facts this module relies on:

* REST ``https://api.hyperliquid.xyz`` (``/info``, ``/exchange``); testnet
  ``https://api.hyperliquid-testnet.xyz``; WebSocket ``…/ws``.
* L1 actions are signed by an approved API (agent) wallet: the action is
  msgpack-encoded (field order matters), followed by the nonce (u64
  big-endian), a vault flag byte and, when set, ``0x00`` + ``expiresAfter``
  (u64); its keccak hash is the ``connectionId`` of a phantom agent
  ``{"source": "a" (mainnet) | "b" (testnet), "connectionId"}`` signed as
  EIP-712 ``Agent`` in the domain ``Exchange``/``1``/chain 1337/zero
  contract. Signing uses ``eth-account`` (the SDK's own dependency); the
  hash construction mirrors the SDK and is checked against its vectors.
* Nonces are millisecond timestamps, unique per signer, within
  ``(T - 2 days, T + 1 day)``. Info queries must use the master account
  address, never the agent's.
* Prices: at most 5 significant figures and ``6 - szDecimals`` decimals
  (integers always allowed), no trailing zeros; sizes use ``szDecimals``.
  Market orders are IOC limits priced off the mid with a slippage band.
* ``cloid`` is 16 bytes hex; Joseph client ids encode reversibly
  (``wire.cloid_of``).
* Limits: 1200 weight per minute per IP; exchange actions weigh 1;
  ``clearinghouseState``/``orderStatus``/``allMids`` 2; ``userRole`` 60;
  other info 20. Address limits allow 10000 initial actions plus one per
  USDC traded.
* Heartbeat: ``{"method": "ping"}``; the server closes connections silent
  for 60 s.
"""

from __future__ import annotations

import dataclasses
import decimal
import importlib
import json
import logging
from collections.abc import Mapping
from collections.abc import Sequence
from typing import Any

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
_State = enums.OrderState
VENUE = "hyperliquid"
REST_URL = "https://api.hyperliquid.xyz"
TESTNET_REST_URL = "https://api.hyperliquid-testnet.xyz"
_META_TTL_S = 600.0
# Signed actions carry ``expiresAfter`` one minute past their nonce, so a
# delayed request cannot execute later than that.
_EXPIRY_MS = 60_000
_ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
_TIF = {
    enums.TimeInForce.GTC: "Gtc",
    enums.TimeInForce.IOC: "Ioc",
    enums.TimeInForce.POST_ONLY: "Alo",
}
# Venue statuses of working orders, and of finished orders that did not
# cancel or reject (``_status_of`` maps every other status).
_OPEN = ("open", "triggered")
_DONE = {"filled": _State.FILLED}


def _libs() -> tuple[Any, Any, Any]:
    """Imports the optional signing libraries.

    Returns:
        ``(eth_account, eth_account.messages, msgpack)``.

    Raises:
        AdapterUnavailableError: When the ``hyperliquid`` extra is missing.
    """
    try:
        account = importlib.import_module("eth_account")
        messages = importlib.import_module("eth_account.messages")
        packer = importlib.import_module("msgpack")
    except ImportError:
        raise errors.AdapterUnavailableError(
            "Hyperliquid signing needs the optional dependencies: install "
            "joseph-execution-engine[hyperliquid]",
            code="UNSUPPORTED",
        ) from None
    return account, messages, packer


def signing_available() -> bool:
    """Tells whether the signing libraries are installed.

    Returns:
        True when ``eth-account`` and ``msgpack`` import.
    """
    try:
        _libs()
    except errors.AdapterUnavailableError:
        return False
    return True


def keccak(data: bytes) -> bytes:
    """Computes Keccak-256 (Ethereum's hash, not NIST SHA3-256).

    Args:
        data: Input.

    Returns:
        32-byte digest.
    """
    utils = importlib.import_module("eth_utils")
    digest: bytes = utils.keccak(data)
    return digest


def action_hash(
    action: Mapping[str, Any],
    vault_address: str | None,
    nonce: int,
    expires_after: int | None,
) -> bytes:
    """Hashes an L1 action exactly like the SDK's ``action_hash``.

    The hashed bytes are the msgpack encoding of the action, the nonce as a
    big-endian u64, then ``0x00`` without a vault or ``0x01`` and the
    20-byte vault address, then, with an expiry, ``0x00`` and
    ``expires_after`` as a big-endian u64. Hyperliquid recomputes this hash
    from the request, so any difference in field order or encoding makes
    the signature invalid.

    Args:
        action: Action (dict insertion order is the msgpack field order).
        vault_address: Vault or sub-account address, if trading for one.
        nonce: Nonce (ms).
        expires_after: Optional expiry (ms).

    Returns:
        Keccak-256 digest.
    """
    _, _, packer = _libs()
    data: bytes = packer.packb(action)
    data += nonce.to_bytes(8, "big")
    if vault_address is None:
        data += b"\x00"
    else:
        data += b"\x01" + bytes.fromhex(vault_address.removeprefix("0x"))
    if expires_after is not None:
        data += b"\x00" + expires_after.to_bytes(8, "big")
    return keccak(data)


def l1_payload(connection_id: bytes, is_mainnet: bool) -> dict[str, Any]:
    """Builds the EIP-712 typed data of a phantom agent.

    Args:
        connection_id: Action hash.
        is_mainnet: Mainnet (``source`` ``a``) or testnet (``b``).

    Returns:
        Full typed-data message.
    """
    return {
        "domain": {
            "chainId": 1337,
            "name": "Exchange",
            "verifyingContract": _ZERO_ADDRESS,
            "version": "1",
        },
        "types": {
            "Agent": [
                {"name": "source", "type": "string"},
                {"name": "connectionId", "type": "bytes32"},
            ],
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
        },
        "primaryType": "Agent",
        "message": {
            "source": "a" if is_mainnet else "b",
            "connectionId": connection_id,
        },
    }


def sign_l1_action(  # noqa: PLR0917 - mirrors the SDK's positional sign_l1_action; the signing vector tests call it so
    private_key: str,
    action: Mapping[str, Any],
    vault_address: str | None,
    nonce: int,
    expires_after: int | None,
    is_mainnet: bool,
) -> dict[str, Any]:
    """Signs an L1 action (SDK ``sign_l1_action``).

    The API wallet key signs, as EIP-712, a phantom ``Agent`` whose
    ``connectionId`` is ``action_hash`` of the action (``l1_payload``).
    The key stays in this process; only the signature is sent.

    Args:
        private_key: API wallet private key (hex).
        action: Action.
        vault_address: Vault, if any.
        nonce: Nonce.
        expires_after: Expiry, if any.
        is_mainnet: Environment.

    Returns:
        ``{"r": hex, "s": hex, "v": int}`` (hex without leading zeros,
        as the SDK's ``to_hex``).
    """
    account, messages, _ = _libs()
    digest = action_hash(action, vault_address, nonce, expires_after)
    encoded = messages.encode_typed_data(
        full_message=l1_payload(digest, is_mainnet)
    )
    signed = account.Account.from_key(private_key).sign_message(encoded)
    return {"r": hex(signed.r), "s": hex(signed.s), "v": int(signed.v)}


def address_of(private_key: str) -> str:
    """Derives the lower-case address of a private key.

    Args:
        private_key: Hex key.

    Returns:
        ``0x`` address.
    """
    account, _, _ = _libs()
    return str(account.Account.from_key(private_key).address).lower()


def price_wire(
    price: decimal.Decimal, sz_decimals: int, rounding: str
) -> decimal.Decimal:
    """Rounds a price to Hyperliquid's precision rule.

    Args:
        price: Positive price.
        sz_decimals: Asset size decimals.
        rounding: ``decimal`` rounding mode.

    Returns:
        Price with at most 5 significant figures and ``6 - sz_decimals``
        decimals (integers are always valid).
    """
    max_decimals = 6 - sz_decimals
    exponent = max(price.adjusted() - 4, -max_decimals)
    if price > 100_000:
        exponent = 0  # integers are always valid (SDK rounds to int)
    quantum = decimal.Decimal(1).scaleb(exponent)
    return price.quantize(quantum, rounding=rounding)


def valid_price(price: decimal.Decimal, sz_decimals: int) -> bool:
    """Checks the price precision rule without rounding.

    Args:
        price: Price.
        sz_decimals: Asset size decimals.

    Returns:
        True when the venue accepts the price.
    """
    if price == price.to_integral_value():
        return True
    return price_wire(price, sz_decimals, decimal.ROUND_HALF_EVEN) == price


@dataclasses.dataclass(frozen=True, slots=True)
class Asset:
    """Perpetual asset metadata from ``meta``.

    Attributes:
        index: Asset id (position in ``universe``).
        name: Coin.
        sz_decimals: Size decimals.
    """

    index: int
    name: str
    sz_decimals: int


@dataclasses.dataclass(slots=True)
class HyperliquidSession(live.Session):
    """Per-account Hyperliquid state.

    Attributes:
        user: Master account address (info queries).
        agent: API wallet address (signer).
        oids: Venue order id to client id.
        last_nonce: Last nonce used by this signer.
        leverage: Leverage and cross flag applied per asset.
    """

    user: str = ""
    agent: str = ""
    oids: dict[str, str] = dataclasses.field(default_factory=dict)
    last_nonce: int = 0
    leverage: dict[int, tuple[int, bool]] = dataclasses.field(
        default_factory=dict
    )


def map_error(text: str) -> errors.ExecutionError:  # noqa: PLR0911 - one return per error family.
    """Maps a Hyperliquid error string to a domain error.

    Hyperliquid answers with free-text errors, so the mapping matches
    phrases from its documented error responses.

    Args:
        text: Venue message.

    Returns:
        Domain error (never containing credentials).
    """
    message = text[:200]
    lowered = message.lower()
    if "insufficient margin" in lowered or "insufficient" in lowered:
        return errors.InsufficientMarginError(message, venue=VENUE)
    if (
        "does not exist" in lowered
        or "must deposit" in lowered
        or "signature" in lowered
    ):
        return errors.VenuePermissionError(
            f"permission missing: the API wallet is not approved for this "
            f"account ({message})",
            venue=VENUE,
        )
    if "never placed" in lowered or "unknown oid" in lowered:
        return errors.OrderRejectedError(message, venue=VENUE, code="NOT_FOUND")
    if "tick size" in lowered:
        return errors.OrderRejectedError(
            message, venue=VENUE, code="INVALID_TICK_SIZE"
        )
    if "minimum value" in lowered:
        return errors.OrderRejectedError(
            message, venue=VENUE, code="MIN_NOTIONAL"
        )
    if "rate limit" in lowered or "too many" in lowered:
        return errors.RateLimitedError(f"hyperliquid throttled: {message}")
    return errors.OrderRejectedError(message, venue=VENUE)


def _status_of(
    name: str, orig: decimal.Decimal, remaining: decimal.Decimal
) -> _State:
    """Maps an ``orderStatus``/``orderUpdates`` status.

    Args:
        name: Venue status (``open``, ``filled``, ``canceled``,
            ``…Canceled``, ``rejected``, ``…Rejected``, ``triggered``).
        orig: Original size.
        remaining: Remaining size.

    Returns:
        OMS state.
    """
    if name in _OPEN:
        return (
            _State.PARTIALLY_FILLED if remaining < orig else _State.ACKNOWLEDGED
        )
    if name in _DONE:
        return _DONE[name]
    if name == "rejected" or name.endswith("Rejected"):
        return _State.REJECTED
    return _State.CANCELED


def _balance(
    state: Mapping[str, Any], ts_ms: int
) -> trading.VenueAccountSnapshot:
    """Builds a balance from a clearinghouse state.

    Args:
        state: ``clearinghouseState``.
        ts_ms: Fallback time, UTC epoch milliseconds.

    Returns:
        Snapshot (available = account value - margin used).
    """
    summary = state.get("marginSummary") or {}
    equity = wire.dec(summary.get("accountValue"))
    used = wire.dec(summary.get("totalMarginUsed"))
    return trading.VenueAccountSnapshot(
        equity=equity,
        available_margin=max(decimals.ZERO, equity - used),
        currency="USDC",
        ts_ms=int(state.get("time") or ts_ms),
    )


def _fill(row: Mapping[str, Any], cid: str) -> trading.VenueFill:
    """Converts a ``WsFill``/``userFills`` row.

    Args:
        row: Fill.
        cid: Client id.

    Returns:
        Fill (fee positive when paid, negative for rebates).
    """
    return trading.VenueFill(
        exchange_fill_id=str(row.get("tid")),
        client_order_id=cid,
        price=wire.dec(row.get("px")),
        quantity_base=wire.dec(row.get("sz")),
        fee=wire.dec(row.get("fee")),
        fee_asset=str(row.get("feeToken") or "USDC"),
        liquidity="taker" if row.get("crossed") else "maker",
        ts_ms=int(row.get("time", 0)),
    )


class HyperliquidAdapter(live.LiveVenueAdapter[HyperliquidSession]):
    """Hyperliquid perpetuals via ``/exchange`` (agent-signed) and ``/info``.

    Attributes:
        REQUIRED_FIELDS: Credential fields the venue needs (the API wallet
            key and the master account address).
        SANDBOX_NAME: Name of the venue's test environment, shown in
            sandbox notes.
    """

    REQUIRED_FIELDS = ("agent_private_key", "account_address")
    SANDBOX_NAME = "the Hyperliquid testnet"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initializes the adapter (see ``LiveVenueAdapter``).

        Args:
            *args: Base arguments.
            **kwargs: Base keyword arguments (``urls`` accepts
                ``rest_url`` and ``testnet_rest_url``).
        """
        super().__init__(*args, **kwargs)
        self._urls = {
            False: self._url("rest_url", REST_URL),
            True: self._url("testnet_rest_url", TESTNET_REST_URL),
        }
        self._http: dict[bool, http.VenueHttp] = {}
        self._meta: dict[bool, tuple[float, dict[str, Asset]]] = {}

    def _client(self, sandbox: bool) -> http.VenueHttp:
        """Returns the REST client of an environment.

        Args:
            sandbox: Testnet.

        Returns:
            Client.
        """
        client = self._http.get(sandbox)
        if client is None:
            client = http.VenueHttp(
                VENUE,
                self._urls[sandbox],
                ratelimit.WindowLimiter(
                    VENUE,
                    [ratelimit.Budget("weight", 1200, 60.0)],
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
        """Closes connection pools."""
        for client in self._http.values():
            await client.aclose()

    def _credential_problem(
        self, credentials: creds_lib.VenueCredentials
    ) -> str | None:
        """Adds signing-library and address checks.

        The account's own key is refused: only a separately approved API
        wallet may sign, so the adapter never signs with the master key.

        Args:
            credentials: Credentials.

        Returns:
            Problem or ``None``.
        """
        problem = super()._credential_problem(credentials)
        if problem is not None:
            return problem
        if not signing_available():
            return (
                "Hyperliquid signing needs the optional dependencies "
                "(joseph-execution-engine[hyperliquid])"
            )
        address = credentials.get("account_address").lower()
        if not (address.startswith("0x") and len(address) == 42):
            return "account_address must be a 0x-prefixed 20-byte address"
        try:
            agent = address_of(credentials.get("agent_private_key"))
        except (ValueError, TypeError):
            return "agent_private_key is not a valid secp256k1 key"
        if agent == address:
            return (
                "agent_private_key is the account's own key; approve a "
                "separate API wallet on Hyperliquid and use its key"
            )
        return None

    def _new_session(
        self,
        account_id: str,
        credentials: creds_lib.VenueCredentials,
        sandbox: bool,
    ) -> HyperliquidSession:
        """Builds per-account state.

        Args:
            account_id: Account.
            credentials: Credentials.
            sandbox: Testnet.

        Returns:
            Session.
        """
        return HyperliquidSession(
            account_id=account_id,
            credentials=credentials,
            sandbox=sandbox,
            user=credentials.get("account_address").lower(),
            agent=address_of(credentials.get("agent_private_key")),
        )

    async def _info(
        self, sandbox: bool, body: Mapping[str, Any], weight: int = 20
    ) -> Any:
        """Posts an ``/info`` request.

        Args:
            sandbox: Testnet.
            body: Request.
            weight: Documented weight.

        Returns:
            Parsed response.
        """
        reply = await self._client(sandbox).request(
            "POST",
            "/info",
            body=json.dumps(body),
            headers={"Content-Type": "application/json"},
            costs={"weight": weight},
        )
        if reply.status != 200:
            raise map_error(str(reply.payload))
        return reply.payload

    async def _assets(self, sandbox: bool) -> dict[str, Asset]:
        """Returns cached ``meta`` (asset ids and size decimals).

        Args:
            sandbox: Testnet.

        Returns:
            Assets by coin.
        """
        now = self._monotonic()
        cached = self._meta.get(sandbox)
        if cached is not None and now - cached[0] < _META_TTL_S:
            return cached[1]
        meta = await self._info(sandbox, {"type": "meta"})
        assets = {
            str(row["name"]): Asset(i, str(row["name"]), int(row["szDecimals"]))
            for i, row in enumerate(meta.get("universe", []))
        }
        self._meta[sandbox] = (now, assets)
        return assets

    async def _asset(self, session: HyperliquidSession, coin: str) -> Asset:
        """Looks up one asset.

        Args:
            session: Session.
            coin: Coin.

        Returns:
            Asset.

        Raises:
            AdapterUnavailableError: Unknown coin (not sent).
        """
        asset = (await self._assets(session.sandbox)).get(coin)
        if asset is None:
            raise errors.AdapterUnavailableError(
                f"hyperliquid has no perpetual {coin!r}", code="UNSUPPORTED"
            )
        return asset

    def _nonce(self, session: HyperliquidSession) -> int:
        """Returns a strictly increasing millisecond nonce.

        Hyperliquid rejects a nonce its signer has already used, so two
        actions in the same millisecond get consecutive nonces.

        Args:
            session: Session.

        Returns:
            Nonce.
        """
        nonce = max(self._now_ms(), session.last_nonce + 1)
        session.last_nonce = nonce
        return nonce

    async def _exchange(
        self, session: HyperliquidSession, action: dict[str, Any]
    ) -> Any:
        """Signs and posts an action to ``/exchange``.

        The API wallet signs the action for the master account with a fresh
        nonce, no vault and an expiry ``_EXPIRY_MS`` after the nonce
        (``sign_l1_action``, testnet when the session is sandboxed); the
        body repeats the action, nonce, expiry and signature.

        Args:
            session: Session.
            action: Action.

        Returns:
            ``response.data`` of an ``ok`` answer.

        Raises:
            ExecutionError: Mapped venue errors.
        """
        nonce = self._nonce(session)
        expires = nonce + _EXPIRY_MS
        signature = sign_l1_action(
            session.credentials.get("agent_private_key"),
            action,
            None,
            nonce,
            expires,
            not session.sandbox,
        )
        body = {
            "action": action,
            "nonce": nonce,
            "signature": signature,
            "vaultAddress": None,
            "expiresAfter": expires,
        }
        reply = await self._client(session.sandbox).request(
            "POST",
            "/exchange",
            body=json.dumps(body),
            headers={"Content-Type": "application/json"},
            costs={"weight": 1},
        )
        payload = reply.payload
        if reply.status >= 500:
            raise errors.AdapterTimeoutError(
                f"hyperliquid answered HTTP {reply.status}; outcome unknown"
            )
        if not isinstance(payload, Mapping) or payload.get("status") != "ok":
            detail = (
                payload.get("response")
                if isinstance(payload, Mapping)
                else payload
            )
            raise map_error(str(detail))
        return (payload.get("response") or {}).get("data") or {}

    # -- negotiation ----------------------------------------------------

    async def _probe_health(self, sandbox: bool) -> None:
        """Checks ``allMids`` (weight 2).

        Args:
            sandbox: Testnet.

        Raises:
            AdapterUnavailableError: The venue returned no mids.
        """
        mids = await self._info(sandbox, {"type": "allMids"}, weight=2)
        if not isinstance(mids, Mapping) or not mids:
            raise errors.AdapterUnavailableError("hyperliquid returned no mids")

    async def _probe_permissions(
        self, session: HyperliquidSession
    ) -> tuple[str, ...]:
        """Checks that the API wallet is an approved agent of the account.

        ``userRole`` (weight 60) of the agent must be ``agent`` with
        ``data.user`` equal to the configured account.

        Args:
            session: Session.

        Returns:
            Notes.

        Raises:
            VenuePermissionError: The wallet is not this account's agent.
        """
        role = await self._info(
            session.sandbox, {"type": "userRole", "user": session.agent}, 60
        )
        owner = str(((role or {}).get("data") or {}).get("user", "")).lower()
        if (role or {}).get("role") != "agent" or owner != session.user:
            raise errors.VenuePermissionError(
                "permission missing: approve this API wallet for the "
                f"account on Hyperliquid (role={(role or {}).get('role')!r})",
                venue=VENUE,
            )
        await self._snapshot(session)
        band = self._options.market_slippage_bps
        return (
            f"API wallet {session.agent} is approved for {session.user}.",
            f"Market orders are IOC limits within {band} bps of the mid.",
            "Hyperliquid has no FOK time in force.",
        )

    # -- reads ----------------------------------------------------------

    async def _state(self, session: HyperliquidSession) -> Mapping[str, Any]:
        """Reads ``clearinghouseState`` (weight 2).

        Args:
            session: Session.

        Returns:
            State.
        """
        state = await self._info(
            session.sandbox,
            {"type": "clearinghouseState", "user": session.user},
            weight=2,
        )
        return state if isinstance(state, Mapping) else {}

    async def _snapshot(
        self, session: HyperliquidSession
    ) -> trading.VenueAccountSnapshot:
        """Reads balances from ``clearinghouseState``.

        Args:
            session: Session.

        Returns:
            Snapshot.
        """
        return _balance(await self._state(session), self._now_ms())

    def _positions_of(
        self, state: Mapping[str, Any]
    ) -> list[trading.VenuePosition]:
        """Extracts positions (``szi`` is signed).

        Args:
            state: ``clearinghouseState``.

        Returns:
            Non-zero positions.
        """
        result = []
        for row in state.get("assetPositions", []):
            position = row.get("position") or {}
            qty = wire.dec(position.get("szi"))
            if qty == 0:
                continue
            result.append(
                trading.VenuePosition(
                    instrument_id=self._instrument(str(position["coin"])),
                    signed_qty=qty,
                    entry_price=wire.opt_dec(position.get("entryPx")),
                )
            )
        return result

    async def _positions(
        self, session: HyperliquidSession
    ) -> list[trading.VenuePosition]:
        """Reads positions from ``clearinghouseState``.

        Args:
            session: Session.

        Returns:
            Positions.
        """
        return self._positions_of(await self._state(session))

    def _cid_of(
        self, session: HyperliquidSession, cloid: object, oid: object
    ) -> str:
        """Maps a ``cloid``/``oid`` to a client id.

        Args:
            session: Session.
            cloid: Venue cloid (may be ``None``).
            oid: Venue order id.

        Returns:
            Client id (``oid:<id>`` for foreign orders).
        """
        cid = session.oids.get(str(oid))
        if cid is None and isinstance(cloid, str):
            cid = wire.client_id_of_cloid(cloid)
        cid = cid or f"oid:{oid}"
        session.oids[str(oid)] = cid
        return cid

    def _order(
        self,
        session: HyperliquidSession,
        order: Mapping[str, Any],
        status: str,
        ts_ms: int,
    ) -> trading.VenueOrderState:
        """Converts an order object with its status.

        Args:
            session: Session.
            order: ``WsBasicOrder``/``orderStatus.order.order`` object.
            status: Venue status.
            ts_ms: Status time.

        Returns:
            State.
        """
        orig = wire.dec(order.get("origSz") or order.get("sz"))
        remaining = wire.dec(order.get("sz"))
        mapped = _status_of(status, orig, remaining)
        filled = orig - remaining
        if mapped is _State.FILLED:
            filled = orig
        return trading.VenueOrderState(
            client_order_id=self._cid_of(
                session, order.get("cloid"), order.get("oid")
            ),
            exchange_order_id=str(order.get("oid")),
            status=mapped,
            filled_base=filled,
            reason=None if mapped is not _State.REJECTED else status,
            ts_ms=ts_ms,
        )

    async def _open_orders(
        self, session: HyperliquidSession
    ) -> list[trading.VenueOrderState]:
        """Reads ``frontendOpenOrders`` (weight 20).

        Args:
            session: Session.

        Returns:
            Open orders (including untriggered stops).
        """
        rows = await self._info(
            session.sandbox,
            {"type": "frontendOpenOrders", "user": session.user},
        )
        return [
            self._order(session, row, "open", int(row.get("timestamp", 0)))
            for row in rows or []
        ]

    async def _learn_oids(
        self, session: HyperliquidSession, since: int
    ) -> None:
        """Maps venue order ids of recent OMS orders (``orderStatus``).

        Args:
            session: Session.
            since: Lower bound for OMS orders.
        """
        known = set(session.oids.values())
        for order in self._directory.recent(session.account_id, since):
            if order.client_order_id in known:
                continue
            if not order.instrument_id.startswith(f"{VENUE}:perp:"):
                continue
            await self._query(session, order.client_order_id)

    async def _fills(
        self, session: HyperliquidSession, since_ms: int
    ) -> list[trading.VenueFill]:
        """Reads ``userFillsByTime`` (at most 2000 per answer).

        Args:
            session: Session.
            since_ms: Lower bound.

        Returns:
            Fills of known client orders, oldest first.
        """
        rows = await self._info(
            session.sandbox,
            {
                "type": "userFillsByTime",
                "user": session.user,
                "startTime": since_ms,
            },
        )
        if any(str(r.get("oid")) not in session.oids for r in rows or []):
            await self._learn_oids(session, since_ms)
        fills = []
        for row in rows or []:
            cid = session.oids.get(str(row.get("oid")))
            if cid is None:
                continue
            fills.append(_fill(row, cid))
        fills.sort(key=lambda f: f.ts_ms)
        return fills

    async def _status(
        self, session: HyperliquidSession, client_order_id: str
    ) -> Mapping[str, Any] | None:
        """Reads ``orderStatus`` by cloid (weight 2).

        Args:
            session: Session.
            client_order_id: Client id.

        Returns:
            ``order`` object or ``None`` for ``unknownOid``.
        """
        reply = await self._info(
            session.sandbox,
            {
                "type": "orderStatus",
                "user": session.user,
                "oid": wire.cloid_of(client_order_id),
            },
            weight=2,
        )
        if not isinstance(reply, Mapping) or reply.get("status") != "order":
            return None
        found = reply.get("order")
        return found if isinstance(found, Mapping) else None

    async def _query(
        self, session: HyperliquidSession, client_order_id: str
    ) -> trading.VenueOrderState | None:
        """Queries an order by cloid.

        Args:
            session: Session.
            client_order_id: Client id.

        Returns:
            State or ``None``.
        """
        found = await self._status(session, client_order_id)
        if found is None:
            return None
        order = found.get("order") or {}
        session.oids[str(order.get("oid"))] = client_order_id
        return self._order(
            session,
            order,
            str(found.get("status")),
            int(found.get("statusTimestamp") or 0),
        )

    # -- trading --------------------------------------------------------

    async def _mid(
        self, session: HyperliquidSession, coin: str
    ) -> decimal.Decimal:
        """Reads the mid price (``allMids``, weight 2).

        Args:
            session: Session.
            coin: Coin.

        Returns:
            Mid.

        Raises:
            AdapterUnavailableError: No mid (not sent).
        """
        mids = await self._info(session.sandbox, {"type": "allMids"}, weight=2)
        mid = wire.opt_dec((mids or {}).get(coin))
        if mid is None or mid <= 0:
            raise errors.AdapterUnavailableError(
                f"no hyperliquid mid for {coin}"
            )
        return mid

    def _band(self, price: decimal.Decimal, buy: bool) -> decimal.Decimal:
        """Applies the slippage band in the aggressive direction.

        Args:
            price: Reference price.
            buy: Buy side.

        Returns:
            Worst acceptable price.
        """
        band = decimal.Decimal(self._options.market_slippage_bps) / decimals.BPS
        return price * (decimals.ONE + band if buy else decimals.ONE - band)

    async def _wire(
        self, session: HyperliquidSession, request: trading.VenueOrderRequest
    ) -> tuple[Asset, dict[str, Any]]:
        """Builds the order wire (field order ``a b p s r t c``).

        Args:
            session: Session.
            request: Order.

        Returns:
            Asset and order wire.

        Raises:
            AdapterUnavailableError: Unsupported (not sent).
            OrderRejectedError: Precision violations (not sent).
        """
        coin = self._symbol(request.instrument_id)
        asset = await self._asset(session, coin)
        buy = request.side is enums.OrderSide.BUY
        size = request.quantity_base
        if size <= 0 or size != round(size, asset.sz_decimals):
            raise errors.OrderRejectedError(
                f"size must have at most {asset.sz_decimals} decimals",
                venue=VENUE,
                code="INVALID_LOT_SIZE",
            )
        kind = request.order_type
        aggressive = decimal.ROUND_UP if buy else decimal.ROUND_DOWN
        trigger: dict[str, Any] | None = None
        if kind is enums.OrderType.MARKET:
            price = price_wire(
                self._band(await self._mid(session, coin), buy),
                asset.sz_decimals,
                aggressive,
            )
            order_type: dict[str, Any] = {"limit": {"tif": "Ioc"}}
        elif kind is enums.OrderType.LIMIT:
            if request.limit_price is None:
                raise AssertionError("LIMIT orders carry a limit price")
            price = request.limit_price
            tif = _TIF.get(request.time_in_force)
            if tif is None:
                raise errors.AdapterUnavailableError(
                    "hyperliquid has no FOK", code="UNSUPPORTED_ORDER_TYPE"
                )
            order_type = {"limit": {"tif": tif}}
        else:
            if request.trigger_price is None:
                raise AssertionError("stop orders carry a trigger price")
            if not valid_price(request.trigger_price, asset.sz_decimals):
                raise errors.OrderRejectedError(
                    "trigger price breaks the 5-significant-figure rule",
                    venue=VENUE,
                    code="INVALID_TICK_SIZE",
                )
            market = kind is enums.OrderType.STOP_MARKET
            if market:
                price = price_wire(
                    self._band(request.trigger_price, buy),
                    asset.sz_decimals,
                    aggressive,
                )
            else:
                if request.limit_price is None:
                    raise AssertionError(
                        "STOP_LIMIT orders carry a limit price"
                    )
                price = request.limit_price
            trigger = {
                "isMarket": market,
                "triggerPx": wire.text(request.trigger_price),
                "tpsl": "sl",
            }
            order_type = {"trigger": trigger}
        if not valid_price(price, asset.sz_decimals):
            raise errors.OrderRejectedError(
                "price breaks the 5-significant-figure rule",
                venue=VENUE,
                code="INVALID_TICK_SIZE",
            )
        order = {
            "a": asset.index,
            "b": buy,
            "p": wire.text(price),
            "s": wire.text(size),
            "r": request.reduce_only,
            "t": order_type,
            "c": wire.cloid_of(request.client_order_id),
        }
        return asset, order

    async def _leverage(
        self,
        session: HyperliquidSession,
        asset: Asset,
        request: trading.VenueOrderRequest,
    ) -> None:
        """Sets leverage and margin mode before opening (``updateLeverage``).

        Args:
            session: Session.
            asset: Asset.
            request: Order.

        Raises:
            OrderRejectedError: Fractional leverage.
        """
        if request.reduce_only:
            return
        leverage = request.leverage
        if leverage != leverage.to_integral_value() or leverage < 1:
            raise errors.OrderRejectedError(
                "hyperliquid leverage must be a whole number", venue=VENUE
            )
        wanted = (int(leverage), request.margin_mode is enums.MarginMode.CROSS)
        if session.leverage.get(asset.index) == wanted:
            return
        await self._exchange(
            session,
            {
                "type": "updateLeverage",
                "asset": asset.index,
                "isCross": wanted[1],
                "leverage": wanted[0],
            },
        )
        session.leverage[asset.index] = wanted

    def _result(
        self,
        session: HyperliquidSession,
        request: trading.VenueOrderRequest,
        status: Mapping[str, Any],
    ) -> trading.VenueSubmitResult:
        """Converts one order status of an ``order`` answer.

        Args:
            session: Session.
            request: Order.
            status: ``{"resting": …}``, ``{"filled": …}`` or ``{"error": …}``.

        Returns:
            Result.

        Raises:
            VenueRejectedError: For ``error`` statuses.
        """
        cid = request.client_order_id
        now = self._now_ms()
        if "error" in status:
            raise map_error(str(status["error"]))
        if "filled" in status:
            filled = status["filled"]
            session.oids[str(filled.get("oid"))] = cid
            total = wire.dec(filled.get("totalSz"))
            done = total >= request.quantity_base
            return trading.VenueSubmitResult(
                True,
                trading.VenueOrderState(
                    cid,
                    str(filled.get("oid")),
                    _State.FILLED if done else _State.PARTIALLY_FILLED,
                    total,
                    None,
                    now,
                ),
            )
        resting = status.get("resting") or {}
        session.oids[str(resting.get("oid"))] = cid
        return trading.VenueSubmitResult(
            True,
            trading.VenueOrderState(
                cid,
                str(resting.get("oid")),
                _State.ACKNOWLEDGED,
                decimals.ZERO,
                None,
                now,
            ),
        )

    async def _submit(
        self,
        session: HyperliquidSession,
        request: trading.VenueOrderRequest,
    ) -> trading.VenueSubmitResult:
        """Places an ``order`` action (``grouping`` ``na``).

        Args:
            session: Session.
            request: Order.

        Returns:
            Result; IOC remainders that did not fill are reported by the
            venue as an error status (rejected, never resent).
        """
        asset, order = await self._wire(session, request)
        await self._leverage(session, asset, request)
        data = await self._exchange(
            session, {"type": "order", "orders": [order], "grouping": "na"}
        )
        statuses = data.get("statuses") or [{}]
        result = self._result(session, request, statuses[0])
        if (
            request.order_type is enums.OrderType.MARKET
            and result.state.status is _State.PARTIALLY_FILLED
        ):
            result = dataclasses.replace(
                result,
                state=dataclasses.replace(
                    result.state,
                    status=_State.CANCELED,
                    reason="IOC remainder canceled",
                ),
            )
        return result

    async def _cancel(
        self, session: HyperliquidSession, client_order_id: str
    ) -> trading.VenueOrderState:
        """Cancels by cloid (``cancelByCloid``).

        Args:
            session: Session.
            client_order_id: Client id.

        Returns:
            State re-read after the cancel.

        Raises:
            OrderRejectedError: ``NOT_FOUND`` when the venue has no such
                order, before or after the cancel.
        """
        known = self._directory.find(session.account_id, client_order_id)
        coin = None if known is None else self._symbol(known.instrument_id)
        if coin is None:
            found = await self._status(session, client_order_id)
            if found is None:
                raise errors.OrderRejectedError(
                    "order not found", venue=VENUE, code="NOT_FOUND"
                )
            coin = str((found.get("order") or {}).get("coin"))
        asset = await self._asset(session, coin)
        data = await self._exchange(
            session,
            {
                "type": "cancelByCloid",
                "cancels": [
                    {
                        "asset": asset.index,
                        "cloid": wire.cloid_of(client_order_id),
                    }
                ],
            },
        )
        outcome = (data.get("statuses") or ["success"])[0]
        if isinstance(outcome, Mapping) and "error" in outcome:
            raise map_error(str(outcome["error"]))
        state = await self._query(session, client_order_id)
        if state is None:
            raise errors.OrderRejectedError(
                "canceled order vanished", venue=VENUE, code="NOT_FOUND"
            )
        return state

    async def _amend(
        self,
        session: HyperliquidSession,
        client_order_id: str,
        limit_price: decimal.Decimal | None,
        trigger_price: decimal.Decimal | None,
        quantity_base: decimal.Decimal | None,
    ) -> trading.VenueOrderState:
        """Modifies an order in place (``batchModify``, same cloid).

        Args:
            session: Session.
            client_order_id: Client id.
            limit_price: New limit.
            trigger_price: New trigger.
            quantity_base: New quantity.

        Returns:
            State re-read after the modification.

        Raises:
            OrderRejectedError: ``CONFLICT`` when the order is not open;
                ``NOT_FOUND`` when it cannot be read back.
        """
        found = await self._status(session, client_order_id)
        if found is None or found.get("status") != "open":
            raise errors.OrderRejectedError(
                "order is not open", venue=VENUE, code="CONFLICT"
            )
        current = found.get("order") or {}
        coin = str(current.get("coin"))
        asset = await self._asset(session, coin)
        known = self._directory.find(session.account_id, client_order_id)
        kind = known.order_type if known is not None else enums.OrderType.LIMIT
        venue_tif = current.get("tif")
        if venue_tif in (None, "Gtc"):
            time_in_force = enums.TimeInForce.GTC
        elif venue_tif == "Alo":
            time_in_force = enums.TimeInForce.POST_ONLY
        else:
            time_in_force = enums.TimeInForce.IOC
        request = trading.VenueOrderRequest(
            account_id=session.account_id,
            instrument_id=self._instrument(coin),
            client_order_id=client_order_id,
            side=enums.OrderSide.BUY
            if current.get("side") == "B"
            else enums.OrderSide.SELL,
            order_type=kind,
            quantity_base=quantity_base or wire.dec(current.get("sz")),
            time_in_force=time_in_force,
            reduce_only=bool(current.get("reduceOnly")),
            limit_price=limit_price or wire.opt_dec(current.get("limitPx")),
            trigger_price=trigger_price
            or wire.opt_dec(current.get("triggerPx")),
        )
        _, order = await self._wire(session, request)
        data = await self._exchange(
            session,
            {
                "type": "batchModify",
                "modifies": [
                    {"oid": wire.cloid_of(client_order_id), "order": order}
                ],
            },
        )
        outcome = (data.get("statuses") or [{}])[0]
        if isinstance(outcome, Mapping) and "error" in outcome:
            raise map_error(str(outcome["error"]))
        del asset
        state = await self._query(session, client_order_id)
        if state is None:
            raise errors.OrderRejectedError(
                "modified order not found", venue=VENUE, code="NOT_FOUND"
            )
        return state

    # -- private stream -------------------------------------------------

    def parse(
        self, session: HyperliquidSession, frame: str | bytes
    ) -> list[ws.StreamUpdate]:
        """Parses one WebSocket frame.

        Args:
            session: Session.
            frame: Raw frame.

        Returns:
            Updates.
        """
        text = frame if isinstance(frame, str) else frame.decode()
        if not text.startswith("{"):
            return []
        message = http.parse_json(text)
        channel = message.get("channel")
        data = message.get("data")
        if channel == "orderUpdates":
            return [
                ws.StreamUpdate(
                    order=self._order(
                        session,
                        row.get("order") or {},
                        str(row.get("status")),
                        int(row.get("statusTimestamp") or 0),
                    )
                )
                for row in data or []
            ]
        if channel == "userFills" and isinstance(data, Mapping):
            updates = []
            for row in data.get("fills", []):
                cid = session.oids.get(str(row.get("oid")))
                if cid is None:
                    continue
                updates.append(ws.StreamUpdate(fill=_fill(row, cid)))
            return updates
        if channel == "clearinghouseState" and isinstance(data, Mapping):
            state = data.get("clearinghouseState") or {}
            updates = [
                ws.StreamUpdate(position=p) for p in self._positions_of(state)
            ]
            updates.append(
                ws.StreamUpdate(balance=_balance(state, self._now_ms()))
            )
            return updates
        return []

    def _stream_spec(self, session: HyperliquidSession) -> ws.PrivateStreamSpec:
        """Describes the user stream.

        Args:
            session: Session.

        Returns:
            Spec (``orderUpdates``, ``userFills`` and ``clearinghouseState``
            for the master account; ping every 50 s).
        """
        url = self._urls[session.sandbox].replace("https://", "wss://") + "/ws"

        async def connect_url() -> str:
            """Returns the stream URL."""
            return url

        async def on_open(connection: ws.WsConnection) -> None:
            """Subscribes the user channels.

            Args:
                connection: Connection.
            """
            for kind in ("orderUpdates", "userFills", "clearinghouseState"):
                subscription: dict[str, Any] = {
                    "type": kind,
                    "user": session.user,
                }
                if kind == "clearinghouseState":
                    subscription["dex"] = ""
                await connection.send(
                    json.dumps(
                        {"method": "subscribe", "subscription": subscription}
                    )
                )

        async def resync() -> Sequence[ws.StreamUpdate]:
            """Returns the REST snapshot replayed as stream updates."""
            return await self._resync(session)

        return ws.PrivateStreamSpec(
            name="hyperliquid user stream",
            url=connect_url,
            on_open=on_open,
            parse=lambda frame: self.parse(session, frame),
            resync=resync,
            ping_message=json.dumps({"method": "ping"}),
            ping_interval_s=50.0,
            idle_timeout_s=90.0,
        )
