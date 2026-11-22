"""Converts market-engine signals into strategy intents.

Only ENTRY_CONFIRMED signals create intents, one per enabled deployment of
the exact strategy version. Each intent uses the idempotency key
``signal:<signal_id>:<deployment_id>``, so redelivered signals never create
a second order.
"""

from __future__ import annotations

import decimal
from collections.abc import Mapping

from joseph_execution.audit import service as audit_service
from joseph_execution.domain import decimals
from joseph_execution.domain import deployments
from joseph_execution.domain import enums
from joseph_execution.domain import errors
from joseph_execution.domain import identity
from joseph_execution.domain import intents
from joseph_execution.domain import market
from joseph_execution.domain import signals
from joseph_execution.oms import service as oms_service
from joseph_execution.oms import valuation
from joseph_execution.ports import market as market_port
from joseph_execution.ports import repositories
from joseph_execution.ports import services

STRATEGY_USER = "strategy-engine"


class SignalIntakeService:
    """Handles ``POST /internal/v1/signals``."""

    def __init__(
        self,
        uow_factory: repositories.UnitOfWorkFactory,
        oms: oms_service.OmsService,
        market_data: market_port.MarketDataGateway,
        audit_log: audit_service.AuditLog,
        clock: services.Clock,
    ) -> None:
        """Initializes the service.

        Args:
            uow_factory: Unit of work factory.
            oms: OMS.
            market_data: Market gateway (sizing references).
            audit_log: Audit log.
            clock: Time source.
        """
        self._uow = uow_factory
        self._oms = oms
        self._market = market_data
        self._audit = audit_log
        self._clock = clock

    async def ingest(self, body: Mapping[str, object]) -> dict[str, object]:
        """Processes one signal.

        Args:
            body: Contract ``Signal``.

        Returns:
            Per-deployment results.
        """
        signal = signals.SignalView.from_contract(body)
        if signal.kind != "ENTRY_CONFIRMED":
            return {
                "signal_id": signal.signal_id,
                "accepted": True,
                "results": [],
                "note": "non-entry signals create no orders",
            }
        with self._uow() as uow:
            candidates = [
                d
                for d in uow.deployments.list(
                    signal.tenant_id, signal.strategy_version_id
                )
                if d.state is enums.DeploymentState.ENABLED
                or (
                    d.mode is enums.DeploymentMode.AUTO_LIVE
                    and d.state is enums.DeploymentState.PENDING_CONSENT
                )
            ]
        principal = identity.Principal(signal.tenant_id, STRATEGY_USER)
        results: list[dict[str, object]] = []
        for deployment in candidates:
            key = f"signal:{signal.signal_id}:{deployment.deployment_id}"
            try:
                existing = self._oms.lookup(principal, key)
                if existing is not None:
                    results.append(self._summarize(deployment, existing))
                    continue
                request = await self._build_request(signal, deployment)
                outcome = await self._oms.submit_intent(
                    principal,
                    request,
                    key,
                    deployment_id=deployment.deployment_id,
                    signal_age_ms=max(
                        0, self._clock.now_ms() - signal.bar_close_ms
                    ),
                )
            except errors.ExecutionError as err:
                with self._uow() as uow:
                    self._audit.record(
                        uow,
                        tenant_id=signal.tenant_id,
                        actor=f"service:{STRATEGY_USER}",
                        action="signal.rejected",
                        entity_type="signal",
                        entity_id=signal.signal_id,
                        account_id=deployment.account_id,
                        payload={
                            "deployment_id": deployment.deployment_id,
                            "code": err.code,
                            "reason": err.message,
                        },
                    )
                results.append(
                    {
                        "deployment_id": deployment.deployment_id,
                        "error": {"code": err.code, "message": err.message},
                    }
                )
                continue
            results.append(self._summarize(deployment, outcome))
        return {
            "signal_id": signal.signal_id,
            "accepted": True,
            "results": results,
        }

    @staticmethod
    def _summarize(
        deployment: deployments.Deployment,
        outcome: oms_service.SubmitResult,
    ) -> dict[str, object]:
        """Summarizes one deployment's outcome.

        Args:
            deployment: Deployment.
            outcome: Submission result.

        Returns:
            Result row.
        """
        return {
            "deployment_id": deployment.deployment_id,
            "intent_id": outcome.intent.intent_id,
            "decision": outcome.decision.decision.value,
            "order_ids": [o.order_id for o in outcome.orders],
            "replayed": outcome.replayed,
        }

    async def _build_request(
        self,
        signal: signals.SignalView,
        deployment: deployments.Deployment,
    ) -> intents.OrderIntentRequest:
        """Sizes a market entry with venue-held protection.

        Args:
            signal: Signal.
            deployment: Deployment.

        Returns:
            Request.

        Raises:
            ExecutionError: ``VALIDATION_FAILED`` or ``MARKET_STALE``.
        """
        instrument = await self._market.instrument(signal.instrument_id)
        if instrument is None:
            raise errors.ExecutionError(
                "MARKET_STALE", "instrument metadata unavailable"
            )
        quote = await valuation.quote_or_none(
            self._market, signal.instrument_id
        )
        entry = signal.entry_price or (
            None if quote is None else quote.reference_price
        )
        if entry is None:
            raise errors.ExecutionError("MARKET_STALE", "no entry reference")
        long = signal.direction == "LONG"
        stop = signal.stop_price
        if stop is not None and (
            (long and stop >= entry) or (not long and stop <= entry)
        ):
            raise errors.ExecutionError(
                "VALIDATION_FAILED", "stop is on the wrong side of entry"
            )
        qty = await self._size(deployment, instrument, entry, stop)
        bracket_stop = None
        if stop is not None:
            bracket_stop = intents.StopLossSpec(
                decimals.round_to_step(stop, instrument.tick_size),
                enums.PriceBasis.MARK,
            )
        take: tuple[intents.TakeProfitSpec, ...] = ()
        if signal.target_price is not None:
            take = (
                intents.TakeProfitSpec(
                    decimals.round_to_step(
                        signal.target_price, instrument.tick_size
                    ),
                    decimals.ONE,
                ),
            )
        return intents.OrderIntentRequest(
            account_id=deployment.account_id,
            instrument_id=signal.instrument_id,
            side=enums.OrderSide.BUY if long else enums.OrderSide.SELL,
            position_effect=enums.PositionEffect.OPEN,
            order_type=enums.OrderType.MARKET,
            quantity_base=qty,
            time_in_force=enums.TimeInForce.IOC,
            reduce_only=False,
            mode=deployment.mode.order_mode,
            source=enums.IntentSource.STRATEGY,
            leverage=decimals.ONE,
            bracket=intents.Bracket(stop_loss=bracket_stop, take_profit=take),
            strategy_version_id=signal.strategy_version_id,
            signal_id=signal.signal_id,
        )

    async def _size(
        self,
        deployment: deployments.Deployment,
        instrument: market.InstrumentMeta,
        entry: decimal.Decimal,
        stop: decimal.Decimal | None,
    ) -> decimal.Decimal:
        """Computes the order quantity.

        Args:
            deployment: Deployment.
            instrument: Instrument.
            entry: Entry reference.
            stop: Stop price.

        Returns:
            Quantity floored to the lot and capped by max notional.

        Raises:
            ExecutionError: ``VALIDATION_FAILED`` when sizing is impossible.
        """
        sizing = deployment.sizing
        if sizing.model == "FIXED_QTY":
            assert sizing.fixed_qty is not None
            raw = sizing.fixed_qty
        else:
            if stop is None:
                raise errors.ExecutionError(
                    "VALIDATION_FAILED", "RISK_PCT sizing needs a stop price"
                )
            with self._uow() as uow:
                account = uow.accounts.get(deployment.account_id)
            assert account is not None and sizing.risk_pct is not None
            val = await valuation.value_account(
                self._uow,
                self._market,
                account,
                deployment.mode.order_mode,
                self._clock.now_ms(),
                60_000,
            )
            if val.equity is None:
                raise errors.ExecutionError(
                    "VALIDATION_FAILED", "equity unknown; cannot size"
                )
            risk_amount = val.equity * sizing.risk_pct / decimals.HUNDRED
            raw = risk_amount / abs(entry - stop)
        cap = deployment.limits.max_notional / (
            entry * instrument.contract_multiplier
        )
        qty = decimals.floor_to_step(min(raw, cap), instrument.lot_size)
        if qty < instrument.min_qty or qty <= 0:
            raise errors.ExecutionError(
                "VALIDATION_FAILED", "position size is below the minimum"
            )
        return qty
