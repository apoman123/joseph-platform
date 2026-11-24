# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Minimal view of a market-engine ``Signal`` needed for execution."""

from __future__ import annotations

import dataclasses
import decimal
from collections.abc import Mapping

from joseph_execution.domain import decimals
from joseph_execution.domain import errors


@dataclasses.dataclass(frozen=True, slots=True)
class SignalView:
    """The fields of a contract ``Signal`` used to create strategy intents.

    Attributes:
        signal_id: Signal id.
        tenant_id: Tenant that owns the strategy.
        strategy_version_id: Immutable strategy version that emitted it.
        instrument_id: Canonical instrument id.
        direction: ``LONG`` or ``SHORT``.
        kind: Signal kind; only ``ENTRY_CONFIRMED`` creates orders.
        mode: Strategy execution mode, as the market engine sent it.
        bar_close_ms: Close time of the signal bar, UTC epoch milliseconds.
        entry_price: Planned entry price, if any.
        stop_price: Planned stop price, if any.
        target_price: Planned target price, if any.
    """

    signal_id: str
    tenant_id: str
    strategy_version_id: str
    instrument_id: str
    direction: str
    kind: str
    mode: str
    bar_close_ms: int
    entry_price: decimal.Decimal | None
    stop_price: decimal.Decimal | None
    target_price: decimal.Decimal | None

    @classmethod
    def from_contract(cls, body: Mapping[str, object]) -> SignalView:
        """Parses a contract ``Signal``.

        Args:
            body: Contract ``Signal`` object.

        Returns:
            Signal view.

        Raises:
            ExecutionError: With ``VALIDATION_FAILED`` when a required field
                is missing or a number is invalid.
        """
        try:
            return cls(
                signal_id=str(body["signal_id"]),
                tenant_id=str(body["tenant_id"]),
                strategy_version_id=str(body["strategy_version_id"]),
                instrument_id=str(body["instrument_id"]),
                direction=str(body["direction"]),
                kind=str(body["kind"]),
                mode=str(body["mode"]),
                bar_close_ms=int(str(body["bar_close_ms"])),
                entry_price=decimals.to_optional(body.get("entry_price")),
                stop_price=decimals.to_optional(body.get("stop_price")),
                target_price=decimals.to_optional(body.get("target_price")),
            )
        except (KeyError, ValueError) as err:
            raise errors.ExecutionError(
                "VALIDATION_FAILED", f"invalid signal: {err}"
            ) from err
