"""Paper trading venue simulator (PAPER mode).

Simulates a venue with resting orders, triggers, reduce-only enforcement,
book-walk slippage and maker/taker fees. Fault injection hooks reproduce
ACK loss, timeouts, rejects, duplicate and out-of-order events, dropped
fill events and cancel-versus-fill races for chaos tests. No real order is
ever sent anywhere.
"""

from __future__ import annotations

import asyncio
import dataclasses
import decimal
from collections.abc import AsyncIterator
from collections.abc import Sequence

from joseph_execution.domain import accounts
from joseph_execution.domain import decimals
from joseph_execution.domain import enums
from joseph_execution.domain import errors
from joseph_execution.domain import market
from joseph_execution.ports import market as market_port
from joseph_execution.ports import services
from joseph_execution.ports import trading

S = enums.OrderState
_ALL_TYPES = tuple(enums.OrderType)
_ALL_TIF = tuple(enums.TimeInForce)


@dataclasses.dataclass(slots=True)
class FaultPlan:
    """Fault injection switches (counters decrement when consumed).

    Attributes:
        drop_ack_next: Accept the order, then raise a timeout.
        timeout_before_next: Raise a timeout without accepting.
        reject_next: Reject the next orders.
        unavailable: Refuse everything (adapter down).
        duplicate_events: Emit every stream event twice.
        reverse_events: Emit each event batch in reverse order.
        drop_fill_events: Do not emit fill events (status only).
        fill_before_cancel_next: Fill the order before a cancel lands.
        cancel_timeout_next: Process the cancel, then raise a timeout.
        query_by_client_id: Whether the venue supports client-id queries.
        query_fails_next: Client-id queries raise a timeout.
        partial_fill_fraction: Fraction of remaining filled per tick.
    """

    drop_ack_next: int = 0
    timeout_before_next: int = 0
    reject_next: int = 0
    unavailable: bool = False
    duplicate_events: bool = False
    reverse_events: bool = False
    drop_fill_events: bool = False
    fill_before_cancel_next: int = 0
    cancel_timeout_next: int = 0
    query_by_client_id: bool = True
    query_fails_next: int = 0
    partial_fill_fraction: decimal.Decimal | None = None


@dataclasses.dataclass(slots=True)
class _SimOrder:
    """Order resting in the simulator.

    Attributes:
        request: Venue request.
        exchange_order_id: Simulated venue id.
        status: Current status.
        filled: Cumulative fill.
        triggered: Whether a stop order triggered.
        reason: Status reason.
        ts_ms: Last update.
    """

    request: trading.VenueOrderRequest
    exchange_order_id: str
    status: enums.OrderState
    filled: decimal.Decimal
    triggered: bool
    reason: str | None
    ts_ms: int

    def state(self) -> trading.VenueOrderState:
        """Returns the venue view of the order.

        Returns:
            Venue state.
        """
        return trading.VenueOrderState(
            client_order_id=self.request.client_order_id,
            exchange_order_id=self.exchange_order_id,
            status=self.status,
            filled_base=self.filled,
            reason=self.reason,
            ts_ms=self.ts_ms,
        )

    @property
    def open(self) -> bool:
        """Whether the order can still fill."""
        return self.status in (S.ACKNOWLEDGED, S.PARTIALLY_FILLED)


@dataclasses.dataclass(slots=True)
class _SimAccount:
    """Per-account simulator state.

    Attributes:
        orders: Orders by client id.
        positions: Signed quantity and entry per instrument.
        fills: Fills in time order.
        events: Undelivered stream events.
        realized: Realized PnL net of fees.
    """

    orders: dict[str, _SimOrder] = dataclasses.field(default_factory=dict)
    positions: dict[str, tuple[decimal.Decimal, decimal.Decimal | None]] = (
        dataclasses.field(default_factory=dict)
    )
    fills: list[trading.VenueFill] = dataclasses.field(default_factory=list)
    events: list[trading.VenueEvent] = dataclasses.field(default_factory=list)
    realized: decimal.Decimal = decimals.ZERO


class PaperTradingAdapter:
    """In-memory simulated venue implementing ``TradingAdapter``.

    Attributes:
        faults: Fault injection plan.
        submit_calls: Number of submit calls received (for tests).
    """

    def __init__(
        self,
        market_data: market_port.MarketDataGateway,
        clock: services.Clock,
        *,
        name: str = "paper",
        maker_bps: decimal.Decimal = decimal.Decimal(2),
        taker_bps: decimal.Decimal = decimal.Decimal(5),
        starting_equity: decimal.Decimal = decimal.Decimal(100_000),
    ) -> None:
        """Initializes the simulator.

        Args:
            market_data: Market gateway for prices and books.
            clock: Time source.
            name: Adapter name.
            maker_bps: Maker fee.
            taker_bps: Taker fee.
            starting_equity: Equity reported by ``account_snapshot``.
        """
        self._market = market_data
        self._clock = clock
        self._name = name
        self._maker = maker_bps
        self._taker = taker_bps
        self._starting = starting_equity
        self._accounts: dict[str, _SimAccount] = {}
        self._seq = 0
        self._fill_seq = 0
        self.faults = FaultPlan()
        self.submit_calls = 0

    @property
    def name(self) -> str:
        """Returns the adapter name."""
        return self._name

    def _acct(self, account_id: str) -> _SimAccount:
        """Returns (creating) simulator state of an account.

        Args:
            account_id: Account.

        Returns:
            State.
        """
        return self._accounts.setdefault(account_id, _SimAccount())

    async def capabilities(
        self, account_id: str
    ) -> accounts.TradingCapabilities:
        """Returns simulator capabilities.

        Args:
            account_id: Account.

        Returns:
            Capabilities (AVAILABLE; simulated venue-side triggers).
        """
        del account_id
        return accounts.TradingCapabilities(
            venue="mock",
            status=enums.TradingStatus.AVAILABLE,
            order_types=_ALL_TYPES,
            time_in_force=_ALL_TIF,
            native_reduce_only=True,
            native_stop_loss=True,
            native_take_profit=True,
            native_oco=False,
            amend=True,
            batch=False,
            position_modes=("ONE_WAY",),
            margin_modes=(enums.MarginMode.ISOLATED, enums.MarginMode.CROSS),
            client_order_id=True,
            query_by_client_id=self.faults.query_by_client_id,
            notes=(
                f"{self._name.capitalize()} simulator: no real orders are "
                "sent; fills are simulated from market data.",
            ),
        )

    async def health(self) -> enums.HealthState:
        """Returns simulator health."""
        return (
            enums.HealthState.UNAVAILABLE
            if self.faults.unavailable
            else enums.HealthState.HEALTHY
        )

    async def account_snapshot(
        self, account_id: str
    ) -> trading.VenueAccountSnapshot:
        """Returns simulated balances.

        Args:
            account_id: Account.

        Returns:
            Snapshot.
        """
        state = self._acct(account_id)
        equity = self._starting + state.realized
        return trading.VenueAccountSnapshot(
            equity=equity,
            available_margin=equity,
            currency="USD",
            ts_ms=self._clock.now_ms(),
        )

    async def list_open_orders(
        self, account_id: str
    ) -> Sequence[trading.VenueOrderState]:
        """Lists open orders.

        Args:
            account_id: Account.

        Returns:
            Open orders.
        """
        return [
            o.state() for o in self._acct(account_id).orders.values() if o.open
        ]

    async def list_positions(
        self, account_id: str
    ) -> Sequence[trading.VenuePosition]:
        """Lists positions.

        Args:
            account_id: Account.

        Returns:
            Positions.
        """
        return [
            trading.VenuePosition(instrument_id=i, signed_qty=q, entry_price=e)
            for i, (q, e) in self._acct(account_id).positions.items()
            if q != 0
        ]

    async def list_recent_fills(
        self, account_id: str, since_ms: int = 0
    ) -> Sequence[trading.VenueFill]:
        """Lists fills since a time.

        Args:
            account_id: Account.
            since_ms: Lower bound.

        Returns:
            Fills.
        """
        return [f for f in self._acct(account_id).fills if f.ts_ms >= since_ms]

    async def get_order_by_client_id(
        self, account_id: str, client_order_id: str
    ) -> trading.VenueOrderState | None:
        """Queries by client id.

        Args:
            account_id: Account.
            client_order_id: Client id.

        Returns:
            State or ``None``.

        Raises:
            AdapterUnavailableError: When queries are unsupported.
            AdapterTimeoutError: When a query fault is injected.
        """
        if not self.faults.query_by_client_id:
            raise errors.AdapterUnavailableError(
                "client-id queries unsupported", code="UNSUPPORTED"
            )
        if self.faults.query_fails_next:
            self.faults.query_fails_next -= 1
            raise errors.AdapterTimeoutError("query timed out")
        order = self._acct(account_id).orders.get(client_order_id)
        return None if order is None else order.state()

    async def submit_order(
        self, request: trading.VenueOrderRequest
    ) -> trading.VenueSubmitResult:
        """Accepts and possibly fills an order.

        Args:
            request: Order.

        Returns:
            Result.

        Raises:
            AdapterUnavailableError: When the simulator is down.
            AdapterTimeoutError: For injected timeouts.
        """
        self.submit_calls += 1
        faults = self.faults
        if faults.unavailable:
            raise errors.AdapterUnavailableError(f"{self._name} unavailable")
        if faults.timeout_before_next:
            faults.timeout_before_next -= 1
            raise errors.AdapterTimeoutError("submit timed out before ACK")
        state = self._acct(request.account_id)
        existing = state.orders.get(request.client_order_id)
        if existing is not None:
            return trading.VenueSubmitResult(True, existing.state())
        now = self._clock.now_ms()
        self._seq += 1
        order = _SimOrder(
            request=request,
            exchange_order_id=f"{self._name}-{self._seq}",
            status=S.ACKNOWLEDGED,
            filled=decimals.ZERO,
            triggered=False,
            reason=None,
            ts_ms=now,
        )
        state.orders[request.client_order_id] = order
        if faults.reject_next:
            faults.reject_next -= 1
            order.status = S.REJECTED
            order.reason = "simulated venue reject"
            self._emit(
                request.account_id,
                [
                    trading.VenueEvent(
                        request.account_id,
                        self._next_seq(),
                        order=order.state(),
                    )
                ],
            )
            return trading.VenueSubmitResult(False, order.state())
        fills = await self._process(request.account_id, order, initial=True)
        if faults.drop_ack_next:
            faults.drop_ack_next -= 1
            raise errors.AdapterTimeoutError("ACK lost after acceptance")
        return trading.VenueSubmitResult(True, order.state(), tuple(fills))

    async def cancel_order(
        self, account_id: str, client_order_id: str
    ) -> trading.VenueOrderState:
        """Cancels an open order (venue truth decides races).

        Args:
            account_id: Account.
            client_order_id: Client id.

        Returns:
            State after the cancel.

        Raises:
            ExecutionError: ``NOT_FOUND`` for unknown orders.
            AdapterTimeoutError: For injected cancel timeouts.
        """
        order = self._acct(account_id).orders.get(client_order_id)
        if order is None:
            raise errors.ExecutionError("NOT_FOUND", "unknown order")
        if self.faults.fill_before_cancel_next and order.open:
            self.faults.fill_before_cancel_next -= 1
            quote = await self._quote(order.request.instrument_id)
            price = order.request.limit_price or (
                None if quote is None else quote.reference_price
            )
            if price is not None:
                self._fill(
                    account_id,
                    order,
                    order.request.quantity_base - order.filled,
                    price,
                    maker=True,
                )
        if order.open:
            order.status = S.CANCELED
            order.reason = "canceled by user"
            order.ts_ms = self._clock.now_ms()
            self._emit(
                account_id,
                [
                    trading.VenueEvent(
                        account_id, self._next_seq(), order=order.state()
                    )
                ],
            )
        if self.faults.cancel_timeout_next:
            self.faults.cancel_timeout_next -= 1
            raise errors.AdapterTimeoutError("cancel response lost")
        return order.state()

    async def amend_order(
        self,
        account_id: str,
        client_order_id: str,
        limit_price: decimal.Decimal | None,
        trigger_price: decimal.Decimal | None,
        quantity_base: decimal.Decimal | None,
    ) -> trading.VenueOrderState:
        """Amends an open order.

        Args:
            account_id: Account.
            client_order_id: Client id.
            limit_price: New limit.
            trigger_price: New trigger.
            quantity_base: New quantity.

        Returns:
            State.

        Raises:
            ExecutionError: ``CONFLICT`` when the order is not open.
        """
        order = self._acct(account_id).orders.get(client_order_id)
        if order is None or not order.open:
            raise errors.ExecutionError("CONFLICT", "order is not open")
        req = order.request
        order.request = dataclasses.replace(
            req,
            limit_price=limit_price
            if limit_price is not None
            else req.limit_price,
            trigger_price=trigger_price
            if trigger_price is not None
            else req.trigger_price,
            quantity_base=quantity_base
            if quantity_base is not None
            else req.quantity_base,
        )
        order.ts_ms = self._clock.now_ms()
        return order.state()

    async def stream_account_events(
        self, account_id: str
    ) -> AsyncIterator[trading.VenueEvent]:
        """Streams events by polling the buffer.

        Args:
            account_id: Account.

        Yields:
            Events.
        """
        while True:
            for event in self.drain_events(account_id):
                yield event
            await asyncio.sleep(0.05)

    def drain_events(self, account_id: str) -> list[trading.VenueEvent]:
        """Returns and clears buffered events (applying stream faults).

        Args:
            account_id: Account.

        Returns:
            Events.
        """
        state = self._acct(account_id)
        events, state.events = state.events, []
        if self.faults.drop_fill_events:
            events = [e for e in events if e.fill is None]
        if self.faults.duplicate_events:
            events = [e for e in events for _ in range(2)]
        if self.faults.reverse_events:
            events.reverse()
        return events

    def accounts_with_open_orders(self) -> list[str]:
        """Lists accounts that have open orders.

        Returns:
            Account ids.
        """
        return [
            a
            for a, s in self._accounts.items()
            if any(o.open for o in s.orders.values())
        ]

    def account_ids(self) -> list[str]:
        """Lists accounts known to the simulator.

        Returns:
            Account ids.
        """
        return list(self._accounts)

    async def tick(self) -> int:
        """Evaluates every open order against current quotes.

        Returns:
            Number of fills produced.
        """
        produced = 0
        for account_id in self.accounts_with_open_orders():
            state = self._acct(account_id)
            for order in list(state.orders.values()):
                if order.open:
                    produced += len(
                        await self._process(account_id, order, initial=False)
                    )
        return produced

    async def _quote(self, instrument_id: str) -> market.MarketQuote | None:
        """Fetches a quote, mapping failures to ``None``.

        Args:
            instrument_id: Instrument.

        Returns:
            Quote or ``None``.
        """
        try:
            return await self._market.quote(instrument_id)
        except errors.ExecutionError:
            return None

    async def _process(  # noqa: PLR0912, PLR0915
        self, account_id: str, order: _SimOrder, initial: bool
    ) -> list[trading.VenueFill]:
        """Matches an order against the market.

        Args:
            account_id: Account.
            order: Order.
            initial: True on submission.

        Returns:
            Fills produced.
        """
        req = order.request
        quote = await self._quote(req.instrument_id)
        if quote is None:
            if initial:
                self._emit(
                    account_id,
                    [
                        trading.VenueEvent(
                            account_id, self._next_seq(), order=order.state()
                        )
                    ],
                )
            return []
        produced: list[trading.VenueFill] = []
        remaining = req.quantity_base - order.filled
        cap = self._reduce_cap(account_id, req)
        if cap is not None and cap <= 0:
            order.status = S.CANCELED
            order.reason = "reduce-only order would increase the position"
            self._emit(
                account_id,
                [
                    trading.VenueEvent(
                        account_id, self._next_seq(), order=order.state()
                    )
                ],
            )
            return []
        if cap is not None:
            remaining = min(remaining, cap)
        kind = req.order_type
        if (
            kind in (enums.OrderType.STOP_MARKET, enums.OrderType.STOP_LIMIT)
            and not order.triggered
        ):
            basis = (
                quote.mark
                if req.trigger_basis in (None, enums.PriceBasis.MARK)
                else quote.last
            )
            price = basis or quote.reference_price
            assert req.trigger_price is not None
            hit = price is not None and (
                price >= req.trigger_price
                if req.side is enums.OrderSide.BUY
                else price <= req.trigger_price
            )
            if not hit:
                if initial:
                    self._emit(
                        account_id,
                        [
                            trading.VenueEvent(
                                account_id,
                                self._next_seq(),
                                order=order.state(),
                            )
                        ],
                    )
                return []
            order.triggered = True
        if kind is enums.OrderType.MARKET or (
            kind is enums.OrderType.STOP_MARKET and order.triggered
        ):
            price = await self._walk_price(req, remaining, quote)
            if price is None:
                order.status = S.CANCELED
                order.reason = "no liquidity for market order"
            else:
                produced.append(
                    self._fill(account_id, order, remaining, price, maker=False)
                )
            if order.open and req.time_in_force in (
                enums.TimeInForce.IOC,
                enums.TimeInForce.FOK,
            ):
                order.status = S.CANCELED
                order.reason = "IOC remainder canceled"
        else:
            assert req.limit_price is not None
            best = (
                quote.best_ask
                if req.side is enums.OrderSide.BUY
                else quote.best_bid
            )
            crosses = best is not None and (
                best <= req.limit_price
                if req.side is enums.OrderSide.BUY
                else best >= req.limit_price
            )
            if (
                crosses
                and initial
                and req.time_in_force is (enums.TimeInForce.POST_ONLY)
            ):
                order.status = S.REJECTED
                order.reason = "post-only order would take liquidity"
            elif crosses:
                assert best is not None
                qty = remaining
                if not initial and self.faults.partial_fill_fraction:
                    qty = await self._partial(req.instrument_id, remaining)
                fill_price = best if initial else req.limit_price
                produced.append(
                    self._fill(
                        account_id, order, qty, fill_price, maker=not initial
                    )
                )
            elif req.time_in_force in (
                enums.TimeInForce.IOC,
                enums.TimeInForce.FOK,
            ):
                order.status = S.CANCELED
                order.reason = "IOC limit did not cross"
        order.ts_ms = self._clock.now_ms()
        if initial or produced or not order.open:
            self._emit(
                account_id,
                [
                    trading.VenueEvent(
                        account_id, self._next_seq(), order=order.state()
                    )
                ],
            )
        return produced

    async def _partial(
        self, instrument_id: str, remaining: decimal.Decimal
    ) -> decimal.Decimal:
        """Computes a partial fill quantity floored to the lot.

        Args:
            instrument_id: Instrument.
            remaining: Remaining quantity.

        Returns:
            Quantity to fill (all of it when the fraction rounds to zero).
        """
        fraction = self.faults.partial_fill_fraction or decimals.ONE
        try:
            meta = await self._market.instrument(instrument_id)
        except errors.ExecutionError:
            meta = None
        qty = remaining * fraction
        if meta is not None:
            qty = decimals.floor_to_step(qty, meta.lot_size)
        return qty if qty > 0 else remaining

    def _reduce_cap(
        self, account_id: str, req: trading.VenueOrderRequest
    ) -> decimal.Decimal | None:
        """Returns the maximum fill of a reduce-only order.

        Args:
            account_id: Account.
            req: Request.

        Returns:
            Cap, or ``None`` for non-reduce-only orders.
        """
        if not req.reduce_only:
            return None
        qty, _ = self._acct(account_id).positions.get(
            req.instrument_id, (decimals.ZERO, None)
        )
        if req.side is enums.OrderSide.SELL:
            return max(qty, decimals.ZERO)
        return max(-qty, decimals.ZERO)

    async def _walk_price(
        self,
        req: trading.VenueOrderRequest,
        quantity: decimal.Decimal,
        quote: market.MarketQuote,
    ) -> decimal.Decimal | None:
        """Prices a taker fill by walking the book.

        Args:
            req: Request.
            quantity: Quantity.
            quote: Quote fallback.

        Returns:
            Average price or ``None``.
        """
        try:
            book = await self._market.order_book(req.instrument_id, 50)
        except errors.ExecutionError:
            book = None
        if book is not None and not book.stale:
            price = book.walk(req.side, quantity)
            if price is not None:
                return price
        best = (
            quote.best_ask
            if req.side is enums.OrderSide.BUY
            else quote.best_bid
        )
        return best or quote.reference_price

    def _fill(
        self,
        account_id: str,
        order: _SimOrder,
        quantity: decimal.Decimal,
        price: decimal.Decimal,
        maker: bool,
    ) -> trading.VenueFill:
        """Books a fill and updates the simulated position.

        Args:
            account_id: Account.
            order: Order.
            quantity: Quantity.
            price: Price.
            maker: Maker (True) or taker.

        Returns:
            The fill.
        """
        state = self._acct(account_id)
        req = order.request
        self._fill_seq += 1
        rate = self._maker if maker else self._taker
        fee = quantity * price * rate / decimals.BPS
        fill = trading.VenueFill(
            exchange_fill_id=f"{self._name}-fill-{self._fill_seq}",
            client_order_id=req.client_order_id,
            price=price,
            quantity_base=quantity,
            fee=fee,
            fee_asset="USD",
            liquidity="maker" if maker else "taker",
            ts_ms=self._clock.now_ms(),
        )
        order.filled += quantity
        order.status = (
            S.FILLED
            if order.filled >= req.quantity_base
            else S.PARTIALLY_FILLED
        )
        qty, entry = state.positions.get(
            req.instrument_id, (decimals.ZERO, None)
        )
        delta = quantity if req.side is enums.OrderSide.BUY else -quantity
        if qty == 0 or (qty > 0) == (delta > 0):
            cost = (entry or decimals.ZERO) * abs(qty) + price * quantity
            new_qty = qty + delta
            state.positions[req.instrument_id] = (new_qty, cost / abs(new_qty))
        else:
            closing = min(abs(qty), quantity)
            direction = decimals.ONE if qty > 0 else -decimals.ONE
            state.realized += (price - (entry or price)) * closing * direction
            new_qty = qty + delta
            new_entry = (
                None
                if new_qty == 0
                else (entry if abs(delta) <= abs(qty) else price)
            )
            state.positions[req.instrument_id] = (new_qty, new_entry)
        state.realized -= fee
        state.fills.append(fill)
        self._emit(
            account_id,
            [trading.VenueEvent(account_id, self._next_seq(), fill=fill)],
        )
        return fill

    def _emit(self, account_id: str, events: list[trading.VenueEvent]) -> None:
        """Buffers stream events.

        Args:
            account_id: Account.
            events: Events.
        """
        self._acct(account_id).events.extend(events)

    def _next_seq(self) -> int:
        """Returns the next stream sequence number.

        Returns:
            Sequence.
        """
        self._seq += 1
        return self._seq
