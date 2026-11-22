"""Minimal view of a market-engine ``Signal`` needed for execution."""

from __future__ import annotations

import dataclasses
import decimal
from collections.abc import Mapping

from joseph_execution.domain import decimals
from joseph_execution.domain import errors


@dataclasses.dataclass(frozen=True, slots=True)
class SignalView:
    """Fields of a contract ``Signal`` used to create strategy intents.

    Attributes:
        signal_id: Signal id.
        tenant_id: Tenant.
        strategy_version_id: Strategy version.
        instrument_id: Instrument.
        direction: ``LONG`` or ``SHORT``.
        kind: Signal kind; only ``ENTRY_CONFIRMED`` creates orders.
        mode: Strategy execution mode.
        bar_close_ms: Close of the signal bar.
        entry_price: Planned entry.
        stop_price: Planned stop.
        target_price: Planned target.
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
            body: Contract signal.

        Returns:
            Signal view.

        Raises:
            ExecutionError: With ``VALIDATION_FAILED`` on bad values.
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
