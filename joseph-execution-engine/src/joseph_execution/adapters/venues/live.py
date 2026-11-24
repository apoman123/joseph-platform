# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Template for credentialed live venue adapters (the ``TradingAdapter`` port).

Every venue adapter inherits the same guards, so a venue module only
implements its wire protocol:

* **Global gate.** With ``JOSEPH_LIVE_TRADING_ENABLED`` off, capability
  negotiation reports ``TRADING_UNAVAILABLE`` and ``health`` reports
  ``UNAVAILABLE`` without touching the network, and submit/amend refuse
  before sending. Cancels and reads still work so resting orders can be
  managed after the gate is switched off.
* **Credentials.** Without stored credentials (or with incomplete ones) the
  adapter is fail-closed exactly like ``FailClosedVenueAdapter``: it reports
  the reason and refuses every call before anything is sent.
* **Negotiation.** ``TRADING`` (``AVAILABLE``) is reported only when the
  venue is healthy and a signed permission probe passes (trading scope,
  one-way position mode, account eligibility); otherwise the reason is
  appended to the capability notes, e.g. ``AUTH_SCOPE_MISSING: permission
  missing: ...``.
* **Errors.** Definitive venue rejections (``VenueRejectedError``) of a
  submit become a ``REJECTED`` result, never an unknown outcome, so the OMS
  never resubmits them. A permission rejection invalidates the cached probe.

The OMS, Risk Gateway, kill switch, consent and audit run before any of
this; adapters are the last line, not the only one.
"""

from __future__ import annotations

import abc
import asyncio
import collections
import dataclasses
import decimal
import logging
import time
from collections.abc import AsyncIterator
from collections.abc import Awaitable
from collections.abc import Callable
from collections.abc import Mapping
from collections.abc import Sequence
from typing import ClassVar

import httpx

from joseph_execution.adapters.venues import credentials as creds_lib
from joseph_execution.adapters.venues import directory as directory_lib
from joseph_execution.adapters.venues import wire
from joseph_execution.adapters.venues import ws
from joseph_execution.domain import accounts
from joseph_execution.domain import decimals
from joseph_execution.domain import enums
from joseph_execution.domain import errors
from joseph_execution.ports import services
from joseph_execution.ports import trading

_LOG = logging.getLogger(__name__)
S = enums.OrderState
_FAILED_PROBE_TTL_S = 30.0


@dataclasses.dataclass(frozen=True, slots=True)
class VenueOptions:
    """Deployment options of one live venue adapter.

    Attributes:
        live_trading_enabled: The global LIVE gate (defense in depth).
        sandbox: Use the venue's test environment unless the account's
            credentials say otherwise.
        timeout_s: HTTP timeout.
        recv_window_ms: Signed-request validity window (Binance, MEXC).
        market_slippage_bps: Worst-price band for venues whose market
            orders are aggressive limits (Hyperliquid, Lighter).
        fills_lookback_ms: History scanned when fills are requested from
            ``since_ms=0`` (reconciliation).
        health_ttl_s: Public health probe cache.
        permission_ttl_s: Signed permission probe cache.
        max_rate_wait_s: Longest wait for rate budget before refusing.
    """

    live_trading_enabled: bool = False
    sandbox: bool = False
    timeout_s: float = 10.0
    recv_window_ms: int = 5_000
    market_slippage_bps: int = 50
    fills_lookback_ms: int = 86_400_000
    health_ttl_s: float = 15.0
    permission_ttl_s: float = 300.0
    max_rate_wait_s: float = 2.0


@dataclasses.dataclass(slots=True)
class Session:
    """Per-account state of a live adapter.

    Attributes:
        account_id: Account.
        credentials: Decrypted credentials (redacted in ``repr``).
        sandbox: True when the session targets the test environment.
        stream: Private stream counters.
    """

    account_id: str
    credentials: creds_lib.VenueCredentials
    sandbox: bool
    stream: ws.StreamStats = dataclasses.field(default_factory=ws.StreamStats)


def reason(err: errors.ExecutionError) -> str:
    """Formats an error as a capability note or reject reason.

    Args:
        err: Error.

    Returns:
        ``CODE: message``.
    """
    return f"{err.code}: {err.message}"


class LiveVenueAdapter[SessionT: Session](abc.ABC):
    """Base class implementing the guards around a venue protocol.

    Attributes:
        calls: Guarded operations invoked (bounded; safety tests).
        REQUIRED_FIELDS: Credential fields the venue needs.
        SANDBOX_NAME: Name of the venue's test environment, or ``None``
            when the venue has none (sandbox requests then fail closed).
    """

    REQUIRED_FIELDS: ClassVar[tuple[str, ...]] = ()
    SANDBOX_NAME: ClassVar[str | None] = None

    def __init__(
        self,
        documented: accounts.TradingCapabilities,
        credentials: creds_lib.CredentialSource,
        clock: services.Clock,
        options: VenueOptions | None = None,
        *,
        directory: directory_lib.OrderDirectory | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        connect: ws.ConnectFn = ws.default_connect,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        urls: Mapping[str, str] | None = None,
    ) -> None:
        """Initializes the adapter.

        Args:
            documented: Capabilities documented by the venue.
            credentials: Credential source (the vault in production).
            clock: Wall clock (venue timestamps, nonces).
            options: Deployment options.
            directory: OMS order lookups.
            transport: HTTP transport override (tests).
            connect: WebSocket connector (tests inject fakes).
            monotonic: Monotonic clock (rate limits, caches).
            sleep: Sleep function (rate limits, backoff).
            urls: Endpoint overrides such as ``rest_url``,
                ``testnet_rest_url``, ``ws_url`` (proxies, moved hosts).
        """
        self._documented = documented
        self._credentials = credentials
        self._clock = clock
        self._options = options or VenueOptions()
        self._directory = directory or directory_lib.EmptyDirectory()
        self._transport = transport
        self._connect = connect
        self._monotonic = monotonic
        self._sleep = sleep
        self._url_overrides = dict(urls or {})
        self._sessions: dict[str, tuple[int, SessionT]] = {}
        self._health: dict[bool, tuple[float, enums.HealthState, str]] = {}
        self._probes: dict[
            str, tuple[float, int, tuple[str, ...] | errors.ExecutionError]
        ] = {}
        self._seq = 0
        self._last_health: tuple[float, enums.HealthState] | None = None
        self.calls: collections.deque[str] = collections.deque(maxlen=1000)

    def __repr__(self) -> str:
        """Returns a representation without credentials."""
        return (
            f"{type(self).__name__}(venue={self.name!r}, "
            f"sandbox_default={self._options.sandbox}, "
            f"live_gate={self._options.live_trading_enabled})"
        )

    @property
    def name(self) -> str:
        """Returns the venue code."""
        return self._documented.venue

    def _url(self, key: str, default: str) -> str:
        """Returns an endpoint, honoring configured overrides.

        Args:
            key: Override key (``rest_url``, ``testnet_rest_url`` …).
            default: Documented endpoint.

        Returns:
            URL.
        """
        return self._url_overrides.get(key) or default

    @property
    def options(self) -> VenueOptions:
        """Returns the deployment options."""
        return self._options

    # ------------------------------------------------------------------
    # Venue protocol (implemented per venue)
    # ------------------------------------------------------------------

    @abc.abstractmethod
    def _new_session(
        self,
        account_id: str,
        credentials: creds_lib.VenueCredentials,
        sandbox: bool,
    ) -> SessionT:
        """Builds per-account state (no network).

        Args:
            account_id: Account.
            credentials: Credentials.
            sandbox: Test environment.

        Returns:
            Session.
        """

    def _credential_problem(
        self, credentials: creds_lib.VenueCredentials
    ) -> str | None:
        """Checks credentials before any network use.

        Args:
            credentials: Credentials.

        Returns:
            A user-facing problem, or ``None``.
        """
        missing = credentials.missing(self.REQUIRED_FIELDS)
        if missing:
            return (
                f"{self.name} credentials are incomplete; missing "
                f"{', '.join(missing)}"
            )
        sandbox = self._sandbox_for(credentials)
        if sandbox and self.SANDBOX_NAME is None:
            return f"{self.name} has no test environment for this market"
        return None

    @abc.abstractmethod
    async def _probe_health(self, sandbox: bool) -> None:
        """Checks public venue health.

        Args:
            sandbox: Test environment.

        Raises:
            ExecutionError: When the venue is unreachable or unhealthy.
        """

    @abc.abstractmethod
    async def _probe_permissions(self, session: SessionT) -> tuple[str, ...]:
        """Runs a signed probe proving the key can trade this market.

        Args:
            session: Session.

        Returns:
            Notes describing the negotiated account (mode, scope).

        Raises:
            VenuePermissionError: The key lacks trading permission.
            ExecutionError: The probe could not be completed.
        """

    @abc.abstractmethod
    async def _snapshot(
        self, session: SessionT
    ) -> trading.VenueAccountSnapshot:
        """Fetches balances and margin.

        Args:
            session: Session.

        Returns:
            Snapshot.
        """

    @abc.abstractmethod
    async def _open_orders(
        self, session: SessionT
    ) -> list[trading.VenueOrderState]:
        """Fetches open orders (including untriggered stops).

        Args:
            session: Session.

        Returns:
            Orders.
        """

    @abc.abstractmethod
    async def _positions(
        self, session: SessionT
    ) -> list[trading.VenuePosition]:
        """Fetches non-zero positions.

        Args:
            session: Session.

        Returns:
            Positions.
        """

    @abc.abstractmethod
    async def _fills(
        self, session: SessionT, since_ms: int
    ) -> list[trading.VenueFill]:
        """Fetches fills since a time.

        Args:
            session: Session.
            since_ms: Lower bound (already clamped to the lookback).

        Returns:
            Fills, oldest first.
        """

    @abc.abstractmethod
    async def _submit(
        self, session: SessionT, request: trading.VenueOrderRequest
    ) -> trading.VenueSubmitResult:
        """Places an order.

        Args:
            session: Session.
            request: Order.

        Returns:
            Venue response.

        Raises:
            VenueRejectedError: The venue refused the order.
        """

    @abc.abstractmethod
    async def _cancel(
        self, session: SessionT, client_order_id: str
    ) -> trading.VenueOrderState:
        """Cancels an order by client id.

        Args:
            session: Session.
            client_order_id: Client id.

        Returns:
            State after the cancel.

        Raises:
            OrderRejectedError: ``NOT_FOUND`` when the venue has no open
                order with that id.
        """

    @abc.abstractmethod
    async def _amend(
        self,
        session: SessionT,
        client_order_id: str,
        limit_price: decimal.Decimal | None,
        trigger_price: decimal.Decimal | None,
        quantity_base: decimal.Decimal | None,
    ) -> trading.VenueOrderState:
        """Amends a working order natively.

        Args:
            session: Session.
            client_order_id: Client id.
            limit_price: New limit.
            trigger_price: New trigger.
            quantity_base: New quantity.

        Returns:
            State after the amendment.
        """

    @abc.abstractmethod
    async def _query(
        self, session: SessionT, client_order_id: str
    ) -> trading.VenueOrderState | None:
        """Queries an order by client id.

        Args:
            session: Session.
            client_order_id: Client id.

        Returns:
            State, or ``None`` when the venue has no such order.
        """

    @abc.abstractmethod
    def _stream_spec(self, session: SessionT) -> ws.PrivateStreamSpec:
        """Describes the private stream of a session.

        Args:
            session: Session.

        Returns:
            Stream spec.
        """

    # ------------------------------------------------------------------
    # Guards and caches
    # ------------------------------------------------------------------

    def _sandbox_for(self, credentials: creds_lib.VenueCredentials) -> bool:
        """Resolves the environment of an account.

        Args:
            credentials: Credentials.

        Returns:
            True for the test environment.
        """
        if credentials.sandbox is not None:
            return credentials.sandbox
        return self._options.sandbox

    def _unavailable(self, why: str) -> accounts.TradingCapabilities:
        """Builds ``TRADING_UNAVAILABLE`` capabilities with a reason.

        Args:
            why: Reason shown to users.

        Returns:
            Capabilities.
        """
        return dataclasses.replace(
            self._documented,
            status=enums.TradingStatus.TRADING_UNAVAILABLE,
            notes=(*self._documented.notes, why),
        )

    def _session(
        self, account_id: str, credentials: creds_lib.VenueCredentials
    ) -> SessionT:
        """Returns the cached session, rebuilding it when keys change.

        Args:
            account_id: Account.
            credentials: Current credentials.

        Returns:
            Session.
        """
        fingerprint = credentials.fingerprint()
        cached = self._sessions.get(account_id)
        if cached is not None and cached[0] == fingerprint:
            return cached[1]
        session = self._new_session(
            account_id, credentials, self._sandbox_for(credentials)
        )
        self._sessions[account_id] = (fingerprint, session)
        self._probes.pop(account_id, None)
        return session

    async def _guard(
        self, account_id: str, method: str, *, trading_call: bool = False
    ) -> SessionT:
        """Records a call and refuses it before sending when unsafe.

        Args:
            account_id: Account.
            method: Port method name.
            trading_call: True for calls that create or change exposure.

        Returns:
            Session.

        Raises:
            AdapterUnavailableError: Gate off, no or incomplete credentials.
        """
        self.calls.append(method)
        if trading_call and not self._options.live_trading_enabled:
            raise errors.AdapterUnavailableError(
                f"{self.name}: live trading is switched off on this "
                "deployment (JOSEPH_LIVE_TRADING_ENABLED); nothing was sent",
                code="LIVE_TRADING_DISABLED",
            )
        credentials = await self._credentials.load(account_id)
        if credentials is None:
            raise errors.AdapterUnavailableError(
                f"{self.name}: no API credentials are stored for this "
                "account; nothing was sent",
                code="LIVE_TRADING_DISABLED",
            )
        problem = self._credential_problem(credentials)
        if problem is not None:
            raise errors.AdapterUnavailableError(
                f"{problem}; nothing was sent", code="AUTH_SCOPE_MISSING"
            )
        return self._session(account_id, credentials)

    async def _health_of(self, sandbox: bool) -> tuple[enums.HealthState, str]:
        """Returns the cached public health of an environment.

        Args:
            sandbox: Test environment.

        Returns:
            ``(state, reason)``.
        """
        now = self._monotonic()
        cached = self._health.get(sandbox)
        if cached is not None and now - cached[0] < self._options.health_ttl_s:
            self._last_health = (now, cached[1])
            return cached[1], cached[2]
        try:
            await self._probe_health(sandbox)
        except errors.ExecutionError as err:
            result = (enums.HealthState.UNAVAILABLE, reason(err))
        else:
            result = (enums.HealthState.HEALTHY, "")
        self._health[sandbox] = (now, *result)
        self._last_health = (now, result[0])
        return result

    async def _permissions(self, session: SessionT) -> tuple[str, ...]:
        """Returns the cached permission probe of a session.

        Args:
            session: Session.

        Returns:
            Notes.

        Raises:
            ExecutionError: The cached or fresh probe failure.
        """
        now = self._monotonic()
        fingerprint = session.credentials.fingerprint()
        cached = self._probes.get(session.account_id)
        if cached is not None and cached[1] == fingerprint:
            age = now - cached[0]
            outcome = cached[2]
            ttl = (
                _FAILED_PROBE_TTL_S
                if isinstance(outcome, errors.ExecutionError)
                else self._options.permission_ttl_s
            )
            if age < ttl:
                if isinstance(outcome, errors.ExecutionError):
                    raise outcome
                return outcome
        try:
            notes = await self._probe_permissions(session)
        except errors.ExecutionError as err:
            self._probes[session.account_id] = (now, fingerprint, err)
            raise
        self._probes[session.account_id] = (now, fingerprint, notes)
        return notes

    def _forget_permissions(self, account_id: str) -> None:
        """Drops a cached probe (after a permission rejection).

        Args:
            account_id: Account.
        """
        self._probes.pop(account_id, None)

    def _environment_note(self, session: SessionT) -> str:
        """Describes the session's environment for capability notes.

        Args:
            session: Session.

        Returns:
            Note.
        """
        if session.sandbox:
            return f"SANDBOX: orders go to {self.SANDBOX_NAME}; no real funds."
        return f"MAINNET: orders go to the real {self.name} market."

    def _next_seq(self) -> int:
        """Returns the next stream sequence number.

        Returns:
            Sequence.
        """
        self._seq += 1
        return self._seq

    def _now_ms(self) -> int:
        """Returns the wall clock.

        Returns:
            UTC epoch milliseconds.
        """
        return self._clock.now_ms()

    # ------------------------------------------------------------------
    # TradingAdapter port
    # ------------------------------------------------------------------

    async def capabilities(
        self, account_id: str
    ) -> accounts.TradingCapabilities:
        """Negotiates capabilities for an account at runtime.

        Args:
            account_id: Account.

        Returns:
            ``AVAILABLE`` only with credentials, a healthy venue and a
            passing permission probe; otherwise ``TRADING_UNAVAILABLE``
            with the reason in ``notes``.
        """
        if not self._options.live_trading_enabled:
            return self._unavailable(
                "Live trading is switched off on this deployment "
                "(JOSEPH_LIVE_TRADING_ENABLED=false)."
            )
        credentials = await self._credentials.load(account_id)
        if credentials is None:
            return self._unavailable(
                "No API credentials are stored for this account."
            )
        problem = self._credential_problem(credentials)
        if problem is not None:
            return self._unavailable(problem)
        session = self._session(account_id, credentials)
        health, why = await self._health_of(session.sandbox)
        if health is not enums.HealthState.HEALTHY:
            return self._unavailable(f"Venue health check failed: {why}")
        try:
            notes = await self._permissions(session)
        except errors.ExecutionError as err:
            return self._unavailable(reason(err))
        return dataclasses.replace(
            self._documented,
            status=enums.TradingStatus.AVAILABLE,
            position_modes=("ONE_WAY",),
            notes=(
                *self._documented.notes,
                self._environment_note(session),
                *notes,
            ),
        )

    async def health(self) -> enums.HealthState:
        """Returns the venue health observed by the latest negotiation.

        ``capabilities`` probes the account's environment (and caches it);
        ``health`` never touches the network itself, so an account without
        credentials or a closed gate causes no venue call at all.

        Returns:
            The last observed state while fresh, else ``UNAVAILABLE``.
        """
        if not self._options.live_trading_enabled or self._last_health is None:
            return enums.HealthState.UNAVAILABLE
        observed_at, state = self._last_health
        if self._monotonic() - observed_at > 2 * self._options.health_ttl_s:
            return enums.HealthState.UNAVAILABLE
        return state

    async def account_snapshot(
        self, account_id: str
    ) -> trading.VenueAccountSnapshot:
        """Fetches balances and margin.

        Args:
            account_id: Account.

        Returns:
            Snapshot.
        """
        session = await self._guard(account_id, "account_snapshot")
        return await self._snapshot(session)

    async def list_open_orders(
        self, account_id: str
    ) -> Sequence[trading.VenueOrderState]:
        """Lists open orders.

        Args:
            account_id: Account.

        Returns:
            Orders.
        """
        session = await self._guard(account_id, "list_open_orders")
        return await self._open_orders(session)

    async def list_positions(
        self, account_id: str
    ) -> Sequence[trading.VenuePosition]:
        """Lists non-zero positions.

        Args:
            account_id: Account.

        Returns:
            Positions.
        """
        session = await self._guard(account_id, "list_positions")
        return await self._positions(session)

    async def list_recent_fills(
        self, account_id: str, since_ms: int = 0
    ) -> Sequence[trading.VenueFill]:
        """Lists fills since a time (clamped to the lookback window).

        Args:
            account_id: Account.
            since_ms: Lower bound.

        Returns:
            Fills, oldest first.
        """
        session = await self._guard(account_id, "list_recent_fills")
        floor = self._now_ms() - self._options.fills_lookback_ms
        return await self._fills(session, max(since_ms, floor))

    async def submit_order(
        self, request: trading.VenueOrderRequest
    ) -> trading.VenueSubmitResult:
        """Submits an order; venue rejections become REJECTED results.

        Args:
            request: Order.

        Returns:
            Venue response.

        Raises:
            AdapterUnavailableError: Refused before sending.
            AdapterTimeoutError: Outcome unknown.
        """
        session = await self._guard(
            request.account_id, "submit_order", trading_call=True
        )
        if (
            request.order_type not in self._documented.order_types
            or request.time_in_force not in self._documented.time_in_force
        ):
            raise errors.AdapterUnavailableError(
                f"{self.name} does not support {request.order_type.value} "
                f"{request.time_in_force.value} orders; nothing was sent",
                code="UNSUPPORTED_ORDER_TYPE",
            )
        try:
            return await self._submit(session, request)
        except errors.VenueRejectedError as err:
            if isinstance(err, errors.VenuePermissionError):
                self._forget_permissions(request.account_id)
            _LOG.info(
                "%s rejected %s: %s",
                self.name,
                request.client_order_id,
                err.code,
            )
            return trading.VenueSubmitResult(
                False,
                trading.VenueOrderState(
                    client_order_id=request.client_order_id,
                    exchange_order_id=None,
                    status=S.REJECTED,
                    filled_base=decimals.ZERO,
                    reason=reason(err),
                    ts_ms=self._now_ms(),
                ),
            )

    async def cancel_order(
        self, account_id: str, client_order_id: str
    ) -> trading.VenueOrderState:
        """Cancels an order; an already-finished order reports its state.

        Args:
            account_id: Account.
            client_order_id: Client id.

        Returns:
            State after the cancel (``FILLED`` when a fill won the race).

        Raises:
            OrderRejectedError: ``NOT_FOUND`` when the venue has no record.
        """
        session = await self._guard(account_id, "cancel_order")
        try:
            return await self._cancel(session, client_order_id)
        except errors.OrderRejectedError as err:
            if err.code != "NOT_FOUND":
                raise
            state = await self._query(session, client_order_id)
            if state is None:
                raise
            return state

    async def amend_order(
        self,
        account_id: str,
        client_order_id: str,
        limit_price: decimal.Decimal | None,
        trigger_price: decimal.Decimal | None,
        quantity_base: decimal.Decimal | None,
    ) -> trading.VenueOrderState:
        """Amends a working order natively.

        Args:
            account_id: Account.
            client_order_id: Client id.
            limit_price: New limit.
            trigger_price: New trigger.
            quantity_base: New quantity.

        Returns:
            State after the amendment.

        Raises:
            AdapterUnavailableError: Gate off or no credentials.
            VenueRejectedError: The venue refused the amendment.
        """
        session = await self._guard(
            account_id, "amend_order", trading_call=True
        )
        if not self._documented.amend:
            raise errors.AdapterUnavailableError(
                f"{self.name} has no native amend", code="UNSUPPORTED"
            )
        return await self._amend(
            session, client_order_id, limit_price, trigger_price, quantity_base
        )

    async def get_order_by_client_id(
        self, account_id: str, client_order_id: str
    ) -> trading.VenueOrderState | None:
        """Queries an order by client id.

        Args:
            account_id: Account.
            client_order_id: Client id.

        Returns:
            State, or ``None`` when the venue confirms it has no such order.
        """
        session = await self._guard(account_id, "get_order_by_client_id")
        return await self._query(session, client_order_id)

    async def stream_account_events(
        self, account_id: str
    ) -> AsyncIterator[trading.VenueEvent]:
        """Streams orders, fills, positions and balances with resync.

        Args:
            account_id: Account.

        Yields:
            Events; after every (re)connect the REST snapshot is replayed
            with ``resync=True``.
        """
        session = await self._guard(account_id, "stream_account_events")
        spec = self._stream_spec(session)
        async for update, replayed in ws.run_private_stream(
            spec,
            connect=self._connect,
            monotonic=self._monotonic,
            sleep=self._sleep,
            stats=session.stream,
        ):
            yield trading.VenueEvent(
                account_id=account_id,
                sequence=self._next_seq(),
                order=update.order,
                fill=update.fill,
                position=update.position,
                balance=update.balance,
                resync=replayed,
            )

    async def _resync(self, session: SessionT) -> list[ws.StreamUpdate]:
        """Builds the post-connect snapshot replay.

        Open orders and recent fills let the OMS catch up on anything that
        happened while disconnected; positions and the balance follow.

        Args:
            session: Session.

        Returns:
            Updates.
        """
        since = self._now_ms() - min(self._options.fills_lookback_ms, 3_600_000)
        updates: list[ws.StreamUpdate] = [
            ws.StreamUpdate(fill=f) for f in await self._fills(session, since)
        ]
        updates.extend(
            ws.StreamUpdate(order=o) for o in await self._open_orders(session)
        )
        updates.extend(
            ws.StreamUpdate(position=p) for p in await self._positions(session)
        )
        updates.append(ws.StreamUpdate(balance=await self._snapshot(session)))
        return updates

    def _instrument(self, symbol: str) -> str:
        """Builds a canonical instrument id for a venue symbol.

        Args:
            symbol: Venue symbol.

        Returns:
            Instrument id.
        """
        return wire.instrument_of(self.name, symbol)

    def _symbol(self, instrument_id: str) -> str:
        """Extracts the venue symbol of an instrument id.

        Args:
            instrument_id: Instrument.

        Returns:
            Symbol.
        """
        return wire.symbol_of(self.name, instrument_id)
