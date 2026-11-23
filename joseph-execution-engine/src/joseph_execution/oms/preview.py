"""Normalization, cost estimates and preview tokens."""

from __future__ import annotations

import dataclasses
import decimal
import hashlib
import hmac

from joseph_execution.domain import decimals
from joseph_execution.domain import enums
from joseph_execution.domain import equities
from joseph_execution.domain import errors
from joseph_execution.domain import ids
from joseph_execution.domain import intents
from joseph_execution.domain import market


@dataclasses.dataclass(frozen=True, slots=True)
class FeeSchedule:
    """Fee assumptions for estimates (venue schedules vary per account).

    Attributes:
        maker_bps: Maker fee in basis points.
        taker_bps: Taker fee in basis points.
    """

    maker_bps: decimal.Decimal = decimal.Decimal(2)
    taker_bps: decimal.Decimal = decimal.Decimal(5)


@dataclasses.dataclass(frozen=True, slots=True)
class Estimates:
    """Cost estimates of an order.

    Attributes:
        reference_price: Expected execution price.
        notional: Quantity x price x multiplier.
        fee: Estimated fee.
        slippage_pct: Estimated taker slippage (market orders).
        margin_required: Margin needed (0 for reduce-only).
    """

    reference_price: decimal.Decimal | None
    notional: decimal.Decimal | None
    fee: decimal.Decimal | None
    slippage_pct: decimal.Decimal | None
    margin_required: decimal.Decimal | None


def normalize(
    request: intents.OrderIntentRequest, instrument: market.InstrumentMeta
) -> tuple[intents.OrderIntentRequest, list[str]]:
    """Rounds quantity down to the lot and prices to the tick.

    Args:
        request: Draft request.
        instrument: Instrument metadata.

    Returns:
        Normalized request and a warning per changed value.
    """
    warnings: list[str] = []
    qty = decimals.floor_to_step(request.quantity_base, instrument.lot_size)
    if qty != request.quantity_base:
        warnings.append(
            f"Quantity rounded down to {decimals.fmt(qty)} (lot size "
            f"{decimals.fmt(instrument.lot_size)})."
        )

    def price(
        value: decimal.Decimal | None, label: str
    ) -> decimal.Decimal | None:
        """Rounds one price to the tick and records a warning.

        Args:
            value: Price or ``None``.
            label: Field label.

        Returns:
            Rounded price.
        """
        if value is None:
            return None
        tick = instrument.tick_for(value)
        rounded = decimals.round_to_step(value, tick)
        if rounded != value:
            warnings.append(
                f"{label} rounded to {decimals.fmt(rounded)} (tick "
                f"{decimals.fmt(tick)})."
            )
        return rounded

    bracket = request.bracket
    stop = bracket.stop_loss
    new_stop = None
    if stop is not None:
        trigger = price(stop.trigger, "Stop-loss")
        assert trigger is not None
        new_stop = intents.StopLossSpec(trigger=trigger, basis=stop.basis)
    legs = []
    for leg in bracket.take_profit:
        trigger = price(leg.trigger, "Take-profit")
        assert trigger is not None
        legs.append(
            intents.TakeProfitSpec(trigger=trigger, fraction=leg.fraction)
        )
    if qty <= decimals.ZERO:
        raise errors.ExecutionError(
            "INVALID_LOT_SIZE",
            f"quantity rounds to zero with lot size "
            f"{decimals.fmt(instrument.lot_size)}",
        )
    normalized = dataclasses.replace(
        request,
        quantity_base=qty,
        limit_price=price(request.limit_price, "Limit price"),
        trigger_price=price(request.trigger_price, "Trigger price"),
        bracket=intents.Bracket(stop_loss=new_stop, take_profit=tuple(legs)),
        preview_token=None,
    )
    return normalized, warnings


def estimate(
    request: intents.OrderIntentRequest,
    instrument: market.InstrumentMeta | None,
    quote: market.MarketQuote | None,
    book: market.BookView | None,
    fees: FeeSchedule,
) -> Estimates:
    """Estimates execution price, notional, fee, slippage and margin.

    Args:
        request: Normalized request.
        instrument: Instrument metadata.
        quote: Latest quote.
        book: Order book (market orders walk it).
        fees: Fee schedule.

    Returns:
        Estimates with ``None`` for anything that cannot be known.
    """
    slippage = None
    ref: decimal.Decimal | None
    if request.order_type is enums.OrderType.MARKET:
        ref = None
        if book is not None and not book.stale:
            ref = book.walk(request.side, request.quantity_base)
            slippage = book.slippage_pct(request.side, request.quantity_base)
        if ref is None and quote is not None:
            ref = (
                quote.best_ask
                if request.side is enums.OrderSide.BUY
                else quote.best_bid
            ) or quote.reference_price
    else:
        ref = request.limit_price or request.trigger_price
    if ref is None or instrument is None:
        return Estimates(ref, None, None, slippage, None)
    notional = request.quantity_base * ref * instrument.contract_multiplier
    rate = fees.taker_bps
    if request.time_in_force is enums.TimeInForce.POST_ONLY:
        rate = fees.maker_bps
    fee = notional * rate / decimals.BPS
    venue = equities.parse_instrument_id(request.instrument_id).venue
    if equities.is_taiwan(venue):
        fee = equities.taiwan_fee(
            request.side, notional, instrument.market_type
        )
    margin = (
        decimals.ZERO
        if request.reduce_only
        else (notional / request.leverage + fee)
    )
    return Estimates(ref, notional, fee, slippage, margin)


class PreviewSigner:
    """Issues and verifies HMAC preview tokens.

    A token binds the tenant, the normalized request and an expiry, so a
    confirmed order cannot differ from what the user previewed.
    """

    def __init__(self, secret: bytes, ttl_ms: int = 30_000) -> None:
        """Initializes the signer.

        Args:
            secret: Server-side HMAC key (never sent to clients).
            ttl_ms: Token lifetime.
        """
        self._secret = secret
        self.ttl_ms = ttl_ms

    def _mac(
        self,
        tenant_id: str,
        request: intents.OrderIntentRequest,
        expires_at_ms: int,
    ) -> str:
        """Computes the MAC.

        Args:
            tenant_id: Tenant.
            request: Normalized request.
            expires_at_ms: Expiry.

        Returns:
            Hex MAC.
        """
        body = request.to_contract()
        body.pop("preview_token", None)
        message = f"{tenant_id}|{expires_at_ms}|{ids.canonical_json(body)}"
        return hmac.new(
            self._secret, message.encode("utf-8"), hashlib.sha256
        ).hexdigest()

    def sign(
        self, tenant_id: str, request: intents.OrderIntentRequest, now_ms: int
    ) -> tuple[str, int]:
        """Creates a token.

        Args:
            tenant_id: Tenant.
            request: Normalized request.
            now_ms: Current time.

        Returns:
            ``(token, expires_at_ms)``.
        """
        expires = now_ms + self.ttl_ms
        return f"{expires}.{self._mac(tenant_id, request, expires)}", expires

    def verify(
        self,
        tenant_id: str,
        request: intents.OrderIntentRequest,
        token: str,
        now_ms: int,
    ) -> None:
        """Verifies a token against the submitted request.

        Args:
            tenant_id: Tenant.
            request: Submitted request.
            token: Preview token.
            now_ms: Current time.

        Raises:
            ExecutionError: ``PREVIEW_REQUIRED`` when the token does not
                match the request, ``PREVIEW_EXPIRED`` when it lapsed.
        """
        expires_text, _, mac = token.partition(".")
        try:
            expires = int(expires_text)
        except ValueError as err:
            raise errors.ExecutionError(
                "PREVIEW_REQUIRED", "malformed preview token"
            ) from err
        expected = self._mac(tenant_id, request, expires)
        if not hmac.compare_digest(expected, mac):
            raise errors.ExecutionError(
                "PREVIEW_REQUIRED",
                "order differs from the previewed order; preview again",
            )
        if now_ms > expires:
            raise errors.ExecutionError(
                "PREVIEW_EXPIRED", "preview expired; preview again"
            )
