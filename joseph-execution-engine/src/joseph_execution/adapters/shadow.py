# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Shadow adapter: records hypothetical orders, never submits them.

Shadow mode runs the real signal and risk pipeline but only records the
order Joseph would have sent, the decision-time mark and the hypothetical
fill price, so deviation against the market can be studied.
"""

from __future__ import annotations

import dataclasses
import decimal

from joseph_execution.adapters import paper
from joseph_execution.domain import accounts
from joseph_execution.domain import decimals
from joseph_execution.ports import market as market_port
from joseph_execution.ports import services
from joseph_execution.ports import trading


@dataclasses.dataclass(frozen=True, slots=True)
class ShadowRecord:
    """One hypothetical order.

    Attributes:
        client_order_id: Client id.
        instrument_id: Instrument.
        side: Side.
        quantity_base: Quantity.
        decision_mark: Mark at decision time.
        hypothetical_price: Hypothetical first fill price.
        deviation_bps: Deviation of the fill from the mark (bps).
        ts_ms: Time.
    """

    client_order_id: str
    instrument_id: str
    side: str
    quantity_base: decimal.Decimal
    decision_mark: decimal.Decimal | None
    hypothetical_price: decimal.Decimal | None
    deviation_bps: decimal.Decimal | None
    ts_ms: int


class ShadowAdapter(paper.PaperTradingAdapter):
    """Hypothetical venue that never sends orders anywhere.

    Attributes:
        records: Hypothetical orders in submission order.
    """

    def __init__(
        self, market_data: market_port.MarketDataGateway, clock: services.Clock
    ) -> None:
        """Initializes the adapter.

        Args:
            market_data: Market gateway.
            clock: Time source.
        """
        super().__init__(market_data, clock, name="shadow")
        self.records: list[ShadowRecord] = []

    async def capabilities(
        self, account_id: str
    ) -> accounts.TradingCapabilities:
        """Returns capabilities with a shadow note.

        Args:
            account_id: Account.

        Returns:
            Capabilities.
        """
        caps = await super().capabilities(account_id)
        return dataclasses.replace(
            caps,
            notes=(
                "Shadow mode: orders are recorded as hypothetical and "
                "never sent to a venue.",
            ),
        )

    async def submit_order(
        self, request: trading.VenueOrderRequest
    ) -> trading.VenueSubmitResult:
        """Records a hypothetical order and simulates its fill.

        Args:
            request: Order.

        Returns:
            Simulated result.
        """
        quote = await self._quote(request.instrument_id)
        mark = None if quote is None else quote.reference_price
        result = await super().submit_order(request)
        price = result.fills[0].price if result.fills else None
        deviation = None
        if price is not None and mark is not None and mark > 0:
            deviation = (price - mark) / mark * decimals.BPS
        self.records.append(
            ShadowRecord(
                client_order_id=request.client_order_id,
                instrument_id=request.instrument_id,
                side=request.side.value,
                quantity_base=request.quantity_base,
                decision_mark=mark,
                hypothetical_price=price,
                deviation_bps=deviation,
                ts_ms=self._clock.now_ms(),
            )
        )
        return result
