# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Order management service (OMS): the only owner of venue order side effects.

Pathway: ``OrderIntent -> RiskGateway -> OMS (outbox) -> TradingAdapter``.

* Every intent and every amendment is evaluated by the Risk Gateway, which
  denies by default: only an ALLOW decision creates an order, and
  protective children come only from the bracket of an allowed intent.
  The LIVE gates (global LIVE flag, account arming, AUTO_LIVE consent),
  the emergency halt and the kill switches are risk checks, so every
  intent passes through them. Manual LIVE orders must also carry a valid
  preview token.
* Intent, risk decision, order, reservation and outbox row are written in
  one transaction.
* The outbox is processed at least once; an order is only sent from
  RESERVED, so a redelivery never sends twice.
* A timeout moves the order to UNKNOWN_RECONCILING. It is resolved by
  querying the venue by client order id; it is resent only when the venue
  confirms the order does not exist, and never when the venue cannot be
  queried (manual intervention instead).
"""

from __future__ import annotations

import asyncio
import dataclasses
import decimal
import logging
from collections.abc import Sequence

from joseph_execution.audit import service as audit_service
from joseph_execution.domain import accounts
from joseph_execution.domain import decimals
from joseph_execution.domain import deployments
from joseph_execution.domain import enums
from joseph_execution.domain import equities
from joseph_execution.domain import errors
from joseph_execution.domain import identity
from joseph_execution.domain import ids
from joseph_execution.domain import intents
from joseph_execution.domain import market
from joseph_execution.domain import orders
from joseph_execution.domain import positions
from joseph_execution.domain import risk
from joseph_execution.oms import equity_inputs
from joseph_execution.oms import preview as preview_lib
from joseph_execution.oms import valuation
from joseph_execution.ports import market as market_port
from joseph_execution.ports import repositories
from joseph_execution.ports import services
from joseph_execution.ports import trading
from joseph_execution.risk import context as risk_context
from joseph_execution.risk import gateway as risk_gateway
from joseph_execution.risk import limits as risk_limits

_LOG = logging.getLogger(__name__)
_State = enums.OrderState
_DAY_MS = 86_400_000
# States from RESERVED until terminal: the order holds a reservation and
# may still trade.
_OPEN_STATES = (
    _State.RESERVED,
    _State.SUBMITTING,
    _State.ACKNOWLEDGED,
    _State.PARTIALLY_FILLED,
    _State.CANCEL_PENDING,
    _State.UNKNOWN_RECONCILING,
)


@dataclasses.dataclass(frozen=True, slots=True)
class OmsSettings:
    """OMS configuration.

    Attributes:
        live_trading_enabled: Global LIVE gate (default False); passed to
            the risk checks.
        emergency_halt: Platform halt (blocks all new exposure).
        fees: Fee assumptions of the cost estimates.
        submit_timeout_s: Timeout of each venue submit, cancel and order
            query, in seconds.
        max_resubmits: Resubmissions allowed after the venue confirms an
            order absent.
        retry_backoff_ms: Base delay of reconciliation retries, in
            milliseconds; it doubles per attempt up to 64 times the base.
        max_reconcile_attempts: Failed venue queries before an order is
            flagged for manual intervention.
        inline_dispatch: Send right after commit (else only the outbox
            worker sends).
        queue_closed_market_orders: Let DAY market orders for securities
            queue for the next open while the exchange is closed (where
            the broker allows it); otherwise they are rejected.
        twd_limit_factor: Multiplier of USD-sized money risk limits for
            TWD accounts (``JOSEPH_TWD_LIMIT_FACTOR``).
    """

    live_trading_enabled: bool = False
    emergency_halt: bool = False
    fees: preview_lib.FeeSchedule = dataclasses.field(
        default_factory=preview_lib.FeeSchedule
    )
    submit_timeout_s: float = 5.0
    max_resubmits: int = 2
    retry_backoff_ms: int = 1_000
    max_reconcile_attempts: int = 8
    inline_dispatch: bool = True
    queue_closed_market_orders: bool = False
    twd_limit_factor: decimal.Decimal = risk.DEFAULT_TWD_LIMIT_FACTOR


@dataclasses.dataclass(frozen=True, slots=True)
class SubmitResult:
    """Outcome of an intent submission.

    Attributes:
        intent: Stored intent.
        decision: Risk decision recorded with the intent.
        orders: Orders of the intent (primary and protective) in their
            current state, oldest first; empty when risk denied it.
        replayed: True when an idempotency key was replayed.
    """

    intent: intents.OrderIntent
    decision: risk.RiskDecision
    orders: tuple[orders.Order, ...]
    replayed: bool

    def to_contract(self) -> dict[str, object]:
        """Serializes to the contract ``SubmitIntentResponse``.

        Returns:
            JSON-compatible dict.
        """
        return {
            "intent": self.intent.to_contract(),
            "risk": self.decision.to_contract(),
            "orders": [o.to_contract() for o in self.orders],
            "replayed": self.replayed,
        }


@dataclasses.dataclass(frozen=True, slots=True)
class PreviewResult:
    """Order preview.

    Attributes:
        allowed: Whether the risk decision is ALLOW.
        normalized: Normalized request the token binds.
        estimates: Cost estimates.
        quote: Quote used, if any.
        decision: Risk decision.
        warnings: Normalization warnings, then the messages of WARN checks.
        token: Preview token.
        expires_at_ms: Token expiry, epoch milliseconds.
        protection: Contract ``ProtectionInfo``: how protective orders
            would be held.
    """

    allowed: bool
    normalized: intents.OrderIntentRequest
    estimates: preview_lib.Estimates
    quote: market.MarketQuote | None
    decision: risk.RiskDecision
    warnings: tuple[str, ...]
    token: str
    expires_at_ms: int
    protection: dict[str, object]

    def to_contract(self) -> dict[str, object]:
        """Serializes to the contract ``OrderPreview``.

        Unknown notional, fee and margin are serialized as ``"0"``.

        Returns:
            JSON-compatible dict.
        """
        est = self.estimates
        zero = decimals.ZERO
        return {
            "allowed": self.allowed,
            "normalized": self.normalized.to_contract(),
            "notional": decimals.fmt(est.notional or zero),
            "estimated_fee": decimals.fmt(est.fee or zero),
            "estimated_slippage_pct": decimals.fmt_optional(est.slippage_pct),
            "margin_required": decimals.fmt(est.margin_required or zero),
            "mark_price": decimals.fmt_optional(
                None if self.quote is None else self.quote.mark
            ),
            "best_bid": decimals.fmt_optional(
                None if self.quote is None else self.quote.best_bid
            ),
            "best_ask": decimals.fmt_optional(
                None if self.quote is None else self.quote.best_ask
            ),
            "risk": self.decision.to_contract(),
            "warnings": list(self.warnings),
            "preview_token": self.token,
            "expires_at_ms": self.expires_at_ms,
            "protection": self.protection,
        }


class OmsService:
    """Application service for orders, fills and positions.

    Attributes:
        settings: OMS configuration.
    """

    def __init__(  # noqa: PLR0917 - eleven injected collaborators; bootstrap passes them positionally.
        self,
        uow_factory: repositories.UnitOfWorkFactory,
        gateway: risk_gateway.RiskGateway,
        resolver: trading.AdapterResolver,
        market_data: market_port.MarketDataGateway,
        audit_log: audit_service.AuditLog,
        clock: services.Clock,
        id_gen: services.IdGenerator,
        publisher: services.EventPublisher,
        metrics: services.Metrics,
        signer: preview_lib.PreviewSigner,
        settings: OmsSettings,
    ) -> None:
        """Initializes the service.

        Args:
            uow_factory: Unit of work factory.
            gateway: Risk Gateway that authorizes every order.
            resolver: Resolves the adapter of an account and order mode.
            market_data: Market data gateway (instruments, quotes, books).
            audit_log: Audit log.
            clock: Time source.
            id_gen: Id generator.
            publisher: Publisher of user stream events.
            metrics: Metrics sink.
            signer: Preview token signer.
            settings: OMS configuration.
        """
        self._uow = uow_factory
        self._gateway = gateway
        self._resolver = resolver
        self._market = market_data
        self._audit = audit_log
        self._clock = clock
        self._ids = id_gen
        self._publisher = publisher
        self._metrics = metrics
        self._signer = signer
        self.settings = settings
        # Orders this process is dispatching or reconciling; a concurrent
        # second call for the same order returns at once.
        self._inflight: set[str] = set()
        # Lot size per instrument from fetched metadata; protective
        # take-profit legs are floored to it.
        self._lots: dict[str, decimal.Decimal] = {}

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_order(
        self, principal: identity.Principal, order_id: str
    ) -> orders.Order:
        """Loads a tenant-owned order.

        Args:
            principal: Caller.
            order_id: Order id.

        Returns:
            Order.

        Raises:
            ExecutionError: ``NOT_FOUND`` for missing or foreign orders.
        """
        with self._uow() as uow:
            order = uow.orders.get(order_id)
        if order is None or order.tenant_id != principal.tenant_id:
            raise errors.ExecutionError("NOT_FOUND", "order not found")
        return order

    def list_orders(
        self,
        principal: identity.Principal,
        account_id: str | None = None,
        states: Sequence[enums.OrderState] | None = None,
    ) -> list[orders.Order]:
        """Lists tenant orders.

        Args:
            principal: Caller.
            account_id: Optional account filter.
            states: Optional state filter.

        Returns:
            Orders of the caller's tenant, newest first (at most 500).
        """
        with self._uow() as uow:
            return uow.orders.list(
                tenant_id=principal.tenant_id,
                account_id=account_id,
                states=states,
            )

    def list_fills(
        self, principal: identity.Principal, account_id: str | None = None
    ) -> list[orders.Fill]:
        """Lists tenant fills.

        Args:
            principal: Caller.
            account_id: Optional account filter.

        Returns:
            Fills of the caller's tenant, newest first (at most 500).
        """
        with self._uow() as uow:
            return uow.fills.list(principal.tenant_id, account_id=account_id)

    async def list_positions(
        self, principal: identity.Principal, account_id: str | None = None
    ) -> list[dict[str, object]]:
        """Lists open positions as contract objects with marks.

        Args:
            principal: Caller.
            account_id: Optional account filter.

        Returns:
            Contract ``Position`` objects; the mark is ``None`` without a
            quote, and marks older than 15 seconds are flagged stale.
        """
        with self._uow() as uow:
            items = uow.positions.list(
                tenant_id=principal.tenant_id, account_id=account_id
            )
        now = self._clock.now_ms()
        result = []
        for pos in items:
            quote = await valuation.quote_or_none(
                self._market, pos.instrument_id
            )
            mark = None if quote is None else quote.reference_price
            stale = quote is None or quote.age_ms(now) > 15_000
            result.append(pos.to_contract(mark, now, stale))
        return result

    # ------------------------------------------------------------------
    # Preview and submission
    # ------------------------------------------------------------------

    async def preview(
        self,
        principal: identity.Principal,
        request: intents.OrderIntentRequest,
        deployment: deployments.Deployment | None = None,
    ) -> PreviewResult:
        """Normalizes, estimates and risk-checks a draft without side effects.

        Nothing is stored. Without instrument metadata the draft is not
        normalized and the risk checks that need metadata fail closed.

        Args:
            principal: Caller.
            request: Draft.
            deployment: Deployment for strategy drafts.

        Returns:
            Preview with a token binding the normalized request.

        Raises:
            ExecutionError: ``NOT_FOUND`` for a missing or foreign account,
                ``INVALID_LOT_SIZE`` when the quantity rounds to zero.
        """
        account = self._owned_account(principal, request.account_id)
        instrument = await self._instrument(request.instrument_id)
        warnings: list[str] = []
        normalized = dataclasses.replace(request, preview_token=None)
        if instrument is not None:
            normalized, warnings = preview_lib.normalize(request, instrument)
        ctx, est = await self._build_context(
            principal, account, normalized, instrument, deployment, None
        )
        decision = self._gateway.evaluate(ctx)
        warnings.extend(
            c.message
            for c in decision.checks
            if c.outcome is enums.CheckOutcome.WARN
        )
        token, expires = self._signer.sign(
            principal.tenant_id, normalized, ctx.now_ms
        )
        return PreviewResult(
            allowed=decision.allowed,
            normalized=normalized,
            estimates=est,
            quote=ctx.quote,
            decision=decision,
            warnings=tuple(warnings),
            token=token,
            expires_at_ms=expires,
            protection=_protection(ctx.capabilities, normalized),
        )

    async def submit_intent(  # noqa: PLR0912, PLR0915 - the order path stays in one function so its gates and writes read in order.
        self,
        principal: identity.Principal,
        request: intents.OrderIntentRequest,
        idempotency_key: str,
        *,
        deployment_id: str | None = None,
        signal_age_ms: int | None = None,
    ) -> SubmitResult:
        """Submits an intent through the Risk Gateway.

        Steps, in order:

        1. The stripped key must be 1-200 characters. A key already used
           with the same body returns the stored result (``replayed``);
           with a different body it raises ``IDEMPOTENCY_CONFLICT``.
        2. The account, and the deployment if given, must belong to the
           caller's tenant.
        3. A preview token, if present, must match the request (without
           the token) and be unexpired. A manual LIVE order without a token
           is refused.
        4. The Risk Gateway evaluates the request; its decision is final.
        5. One transaction stores the intent, decision and body hash and
           audits ``intent.created``. If ALLOW, it also adds the order in
           RESERVED, a reservation of the estimated margin (0 when
           unknown) and a SUBMIT outbox item; otherwise it audits
           ``risk.blocked`` and creates no order. A concurrent submission
           with the same key that wins the race is replayed.
        6. After commit, new orders are published and, with
           ``inline_dispatch``, sent at once.

        A denied intent is returned, not raised.

        Args:
            principal: Caller.
            request: Confirmed request (strictly validated; no rounding).
            idempotency_key: Unique key per user action.
            deployment_id: Deployment for STRATEGY intents.
            signal_age_ms: Age of the originating signal, in milliseconds.

        Returns:
            Submission result (``replayed`` for a repeated key).

        Raises:
            ExecutionError: ``IDEMPOTENCY_CONFLICT``, ``PREVIEW_REQUIRED``,
                ``PREVIEW_EXPIRED``, ``NOT_FOUND`` or ``VALIDATION_FAILED``.
        """
        key = idempotency_key.strip()
        if not key or len(key) > 200:
            raise errors.ExecutionError(
                "VALIDATION_FAILED", "Idempotency-Key must be 1-200 chars"
            )
        body_hash = request.body_hash()
        replay = self._replay(principal, key, body_hash)
        if replay is not None:
            return replay
        account = self._owned_account(principal, request.account_id)
        now = self._clock.now_ms()
        if request.preview_token:
            self._signer.verify(
                principal.tenant_id,
                dataclasses.replace(request, preview_token=None),
                request.preview_token,
                now,
            )
        elif request.mode is enums.OrderMode.LIVE and request.source.is_manual:
            raise errors.ExecutionError(
                "PREVIEW_REQUIRED", "live manual orders must be previewed"
            )
        deployment = None
        if deployment_id is not None:
            with self._uow() as uow:
                deployment = uow.deployments.get(deployment_id)
            if deployment is None or deployment.tenant_id != (
                principal.tenant_id
            ):
                raise errors.ExecutionError("NOT_FOUND", "deployment unknown")
        instrument = await self._instrument(request.instrument_id)
        ctx, est = await self._build_context(
            principal, account, request, instrument, deployment, signal_age_ms
        )
        decision = self._gateway.evaluate(ctx)
        intent_id = self._ids.new_id()
        intent = intents.OrderIntent(
            intent_id=intent_id,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            idempotency_key=key,
            client_order_id=ids.client_order_id(intent_id),
            created_at_ms=now,
            request=request,
            deployment_id=deployment_id,
        )
        created: list[orders.Order] = []
        try:
            with self._uow() as uow:
                if decision.allowed:
                    decision = decision.with_reservation(self._ids.new_id())
                stored = repositories.StoredIntent(intent, decision, body_hash)
                uow.intents.add(stored)
                self._audit.record(
                    uow,
                    tenant_id=principal.tenant_id,
                    actor=principal.actor,
                    action="intent.created",
                    entity_type="intent",
                    entity_id=intent_id,
                    account_id=request.account_id,
                    payload={
                        "request": request.to_contract(),
                        "decision": decision.decision.value,
                        "failed_checks": [c.check for c in decision.failures()],
                        "policy_version": decision.policy_version,
                    },
                )
                if decision.allowed:
                    order = self._new_order(intent, now)
                    uow.orders.add(order)
                    if decision.reservation_id is None:
                        raise AssertionError("an allowed decision is reserved")
                    uow.reservations.add(
                        decision.reservation_id,
                        principal.tenant_id,
                        request.account_id,
                        intent_id,
                        order.order_id,
                        est.margin_required or decimals.ZERO,
                        now,
                        request.mode,
                    )
                    uow.outbox.add(order.order_id, "submit", "SUBMIT", now)
                    created.append(order)
                else:
                    self._audit.record(
                        uow,
                        tenant_id=principal.tenant_id,
                        actor=principal.actor,
                        action="risk.blocked",
                        entity_type="intent",
                        entity_id=intent_id,
                        account_id=request.account_id,
                        payload={"decision": decision.to_contract()},
                    )
        except errors.ExecutionError as err:
            # The intent store rejects a key a concurrent request committed
            # first; same body -> replay it, different body -> conflict.
            if err.code == "IDEMPOTENCY_CONFLICT":
                replay = self._replay(principal, key, body_hash)
                if replay is not None:
                    return replay
            raise
        if not decision.allowed:
            self._metrics.increment(
                "risk_block_total", {"decision": decision.decision.value}
            )
        for order in created:
            self._publish_order(order)
            if self.settings.inline_dispatch:
                await self.dispatch_order(order.order_id)
        return self._load_result(principal, intent_id, replayed=False)

    def lookup(
        self, principal: identity.Principal, idempotency_key: str
    ) -> SubmitResult | None:
        """Returns the result already recorded for an idempotency key.

        Used by server-generated intents (signals) whose body may be
        re-derived differently on redelivery: the first intent wins.

        Args:
            principal: Caller.
            idempotency_key: Key.

        Returns:
            Replayed result, or ``None`` when the key is unused.
        """
        with self._uow() as uow:
            existing = uow.intents.find_by_idempotency(
                principal.tenant_id, idempotency_key
            )
        if existing is None:
            return None
        return self._load_result(
            principal, existing.intent.intent_id, replayed=True
        )

    def _replay(
        self, principal: identity.Principal, key: str, body_hash: str
    ) -> SubmitResult | None:
        """Returns the original result for a repeated idempotency key.

        Args:
            principal: Caller.
            key: Idempotency key.
            body_hash: Hash of the new body.

        Returns:
            Replayed result, or ``None`` if the key is new.

        Raises:
            ExecutionError: ``IDEMPOTENCY_CONFLICT`` when the body differs.
        """
        with self._uow() as uow:
            existing = uow.intents.find_by_idempotency(principal.tenant_id, key)
        if existing is None:
            return None
        if existing.body_hash != body_hash:
            raise errors.ExecutionError(
                "IDEMPOTENCY_CONFLICT",
                "Idempotency-Key was already used with a different order",
            )
        return self._load_result(
            principal, existing.intent.intent_id, replayed=True
        )

    def _load_result(
        self, principal: identity.Principal, intent_id: str, replayed: bool
    ) -> SubmitResult:
        """Loads an intent with its current orders.

        Args:
            principal: Caller.
            intent_id: Intent.
            replayed: Replay flag of the result.

        Returns:
            The intent, its decision and its orders, oldest first.
        """
        with self._uow() as uow:
            stored = uow.intents.get(intent_id)
            if stored is None:
                raise AssertionError("the intent was stored before loading")
            related = uow.orders.list(
                tenant_id=principal.tenant_id, intent_id=intent_id
            )
        related.sort(key=lambda o: (o.created_at_ms, o.order_id))
        return SubmitResult(
            stored.intent, stored.decision, tuple(related), replayed
        )

    def _new_order(
        self, intent: intents.OrderIntent, now_ms: int
    ) -> orders.Order:
        """Creates the intent's order and walks it to RESERVED.

        Args:
            intent: Intent.
            now_ms: Time, epoch milliseconds.

        Returns:
            Unsaved order in RESERVED (DRAFT -> VALIDATED -> RESERVED), with
            role CLOSE for closing and reducing intents, else PRIMARY.
        """
        req = intent.request
        role = (
            enums.OrderRole.CLOSE
            if req.position_effect is not enums.PositionEffect.OPEN
            else enums.OrderRole.PRIMARY
        )
        order = orders.Order(
            order_id=self._ids.new_id(),
            intent_id=intent.intent_id,
            tenant_id=intent.tenant_id,
            account_id=req.account_id,
            instrument_id=req.instrument_id,
            client_order_id=intent.client_order_id,
            mode=req.mode,
            role=role,
            side=req.side,
            order_type=req.order_type,
            time_in_force=req.time_in_force,
            quantity_base=req.quantity_base,
            reduce_only=req.reduce_only,
            state=_State.DRAFT,
            created_at_ms=now_ms,
            updated_at_ms=now_ms,
            limit_price=req.limit_price,
            trigger_price=req.trigger_price,
            trigger_basis=req.trigger_basis,
            strategy_version_id=req.strategy_version_id,
            signal_id=req.signal_id,
        )
        order.transition(_State.VALIDATED, now_ms)
        order.transition(
            _State.RESERVED, now_ms, "risk approved; margin reserved"
        )
        return order

    # ------------------------------------------------------------------
    # Risk context
    # ------------------------------------------------------------------

    def _owned_account(
        self, principal: identity.Principal, account_id: str
    ) -> accounts.Account:
        """Loads an account owned by the caller's tenant.

        Args:
            principal: Caller.
            account_id: Account.

        Returns:
            Account.

        Raises:
            ExecutionError: ``NOT_FOUND`` for missing or foreign accounts.
        """
        with self._uow() as uow:
            account = uow.accounts.get(account_id)
        if account is None or account.tenant_id != principal.tenant_id:
            raise errors.ExecutionError("NOT_FOUND", "account not found")
        return account

    async def _instrument(
        self, instrument_id: str
    ) -> market.InstrumentMeta | None:
        """Fetches instrument metadata, mapping failures to ``None``.

        Remembers the lot size for protective orders.

        Args:
            instrument_id: Instrument.

        Returns:
            Metadata, or ``None`` when unknown or the gateway fails.
        """
        try:
            meta = await self._market.instrument(instrument_id)
        except errors.ExecutionError:
            return None
        if meta is not None:
            self._lots[instrument_id] = meta.lot_size
        return meta

    async def _build_context(  # noqa: PLR0917 - preview, submit and amend pass the same six risk inputs positionally.
        self,
        principal: identity.Principal,
        account: accounts.Account,
        request: intents.OrderIntentRequest,
        instrument: market.InstrumentMeta | None,
        deployment: deployments.Deployment | None,
        signal_age_ms: int | None,
    ) -> tuple[risk_context.RiskContext, preview_lib.Estimates]:
        """Gathers every input of the risk checks.

        Unknown values stay ``None`` so the checks that need them fail
        closed.

        Args:
            principal: Caller.
            account: Account.
            request: Request.
            instrument: Instrument metadata, if known.
            deployment: Deployment, if any.
            signal_age_ms: Signal age in milliseconds, if any.

        Returns:
            The risk context and the request's cost estimates.
        """
        now = self._clock.now_ms()
        # The daily loss limit counts realized profit and loss from 00:00 UTC.
        day_start = now - (now % _DAY_MS)
        with self._uow() as uow:
            lim = risk_limits.from_mapping(
                uow.risk_limits.get(principal.tenant_id, account.account_id),
                account.currency,
                self.settings.twd_limit_factor,
            )
            position = uow.positions.get(
                account.account_id, request.instrument_id, request.mode
            )
            daily = uow.fills.realized_since(account.account_id, day_start)
            unknown_orders = uow.orders.list(
                account_id=account.account_id,
                states=[_State.UNKNOWN_RECONCILING],
            )
            unknown_positions = [
                p
                for p in uow.positions.list(
                    account_id=account.account_id, include_flat=True
                )
                if p.unknown
            ]
            switches = uow.kill_switches.list(principal.tenant_id)
            arming = uow.live_arming.get(
                principal.tenant_id, account.account_id
            )
            signal_intents = 0
            if request.signal_id is not None:
                signal_intents = uow.intents.count_for_signal(
                    principal.tenant_id,
                    request.signal_id,
                    None if deployment is None else deployment.deployment_id,
                )
            strategy_open = 0
            if request.strategy_version_id is not None:
                strategy_open = sum(
                    1
                    for o in uow.orders.list(
                        account_id=account.account_id, states=_OPEN_STATES
                    )
                    if o.strategy_version_id == request.strategy_version_id
                    and o.instrument_id == request.instrument_id
                    and not o.reduce_only
                )
            window_start = now - lim.reject_window_ms
            # Reading threshold + 1 of the newest rejects is enough to tell
            # whether the spike threshold is exceeded.
            rejects = sum(
                1
                for o in uow.orders.list(
                    account_id=account.account_id,
                    states=[_State.REJECTED],
                    limit=lim.reject_spike_threshold + 1,
                )
                if o.updated_at_ms >= window_start
            )
        adapter = self._resolver.resolve(account, request.mode)
        caps: accounts.TradingCapabilities | None
        health: enums.HealthState | None
        try:
            caps = await adapter.capabilities(account.account_id)
            health = await adapter.health()
        except errors.ExecutionError:
            caps, health = None, None
        quote = await valuation.quote_or_none(
            self._market, request.instrument_id
        )
        try:
            book = await self._market.order_book(request.instrument_id, 50)
        except errors.ExecutionError:
            book = None
        val = await valuation.value_account(
            self._uow,
            self._market,
            account,
            request.mode,
            now,
            lim.max_quote_age_ms,
            await _venue_balances(adapter, account, request.mode, caps),
        )
        est = preview_lib.estimate(
            request, instrument, quote, book, self.settings.fees, now_ms=now
        )
        open_count = sum(1 for p in val.positions if p.signed_qty != 0)
        ctx = risk_context.RiskContext(
            request=request,
            principal=principal,
            now_ms=now,
            limits=lim,
            live_trading_enabled=self.settings.live_trading_enabled,
            emergency_halt=self.settings.emergency_halt,
            account=account,
            capabilities=caps,
            adapter_health=health,
            instrument=instrument,
            quote=quote,
            book=book,
            position=position,
            open_position_count=open_count,
            gross_notional=val.gross_notional,
            equity=val.equity,
            available_margin=val.available_margin,
            daily_realized_pnl=daily,
            account_state_unknown=bool(unknown_orders or unknown_positions),
            kill_switches=switches,
            deployment=deployment,
            signal_age_ms=signal_age_ms,
            signal_intents=signal_intents,
            strategy_open_orders=strategy_open,
            recent_rejects=rejects,
            notional=est.notional,
            margin_required=est.margin_required,
            slippage_pct=est.slippage_pct,
            estimated_fee=est.fee,
            queue_closed_market_orders=(
                self.settings.queue_closed_market_orders
            ),
            live_arming=arming,
        )
        # Securities are bought with cash: sessions, price limits and the
        # broker's cash view replace the margin valuation.
        if ctx.security:
            inputs = await equity_inputs.gather(
                self._market,
                self._uow,
                adapter,
                account,
                request.mode,
                instrument,
                val,
                now,
            )
            ctx = dataclasses.replace(
                ctx,
                sessions=inputs.sessions,
                previous_close=inputs.previous_close,
                cash=inputs.cash,
                equity=inputs.equity,
                available_margin=inputs.available_margin,
                gross_notional=inputs.gross_notional,
            )
        return ctx, est

    # ------------------------------------------------------------------
    # Dispatch (outbox worker)
    # ------------------------------------------------------------------

    async def dispatch_pending(self, limit: int = 50) -> int:
        """Processes due outbox items (submits and reconciliations).

        Items of orders this process already has in flight are skipped. A
        failing item is logged and the remaining items still run.

        Args:
            limit: Maximum items to fetch.

        Returns:
            Number of due items fetched, skipped ones included.
        """
        with self._uow() as uow:
            items = uow.outbox.due(self._clock.now_ms(), limit)
        for item in items:
            if item.aggregate_id in self._inflight:
                continue
            try:
                if item.kind == "SUBMIT":
                    await self.dispatch_order(item.aggregate_id)
                else:
                    await self.reconcile_order(item.aggregate_id)
            except Exception:  # noqa: BLE001 - isolation point: logged with traceback; one failing item must not stop the batch.
                _LOG.exception("outbox item %s failed", item.outbox_id)
        return len(items)

    def _outbox_item(
        self, uow: repositories.UnitOfWork, order_id: str
    ) -> repositories.OutboxItem | None:
        """Returns the order's latest outbox item.

        Args:
            uow: Unit of work.
            order_id: Order.

        Returns:
            The last item, or ``None`` when the order has none.
        """
        items = uow.outbox.for_aggregate(order_id)
        return items[-1] if items else None

    def _finish_outbox(
        self,
        uow: repositories.UnitOfWork,
        order_id: str,
        status: str = "DONE",
        error: str | None = None,
    ) -> None:
        """Sets every PENDING outbox item of an order to a final status.

        Each item counts one more attempt.

        Args:
            uow: Unit of work.
            order_id: Order.
            status: New status: ``DONE``, ``FAILED`` or ``MANUAL``.
            error: Error text to store, if any.
        """
        for item in uow.outbox.for_aggregate(order_id):
            if item.status == "PENDING":
                uow.outbox.update(
                    item.outbox_id,
                    status,
                    item.attempts + 1,
                    item.next_attempt_ms,
                    error,
                )

    async def dispatch_order(self, order_id: str) -> None:
        """Sends a RESERVED order to its venue adapter exactly once.

        Returns at once when this process is already dispatching or
        reconciling the order. The order moves to SUBMITTING in its own
        transaction before the venue call, so a redelivered outbox item
        never sends it twice.

        Args:
            order_id: Order.
        """
        if order_id in self._inflight:
            return
        self._inflight.add(order_id)
        try:
            await self._dispatch(order_id)
        finally:
            self._inflight.discard(order_id)

    async def _dispatch(self, order_id: str) -> None:
        """Implements ``dispatch_order`` for one non-inflight order.

        A non-RESERVED order is not sent: SUBMITTING moves to
        UNKNOWN_RECONCILING, UNKNOWN_RECONCILING is left to
        reconciliation, and any other state finishes its outbox items.

        Args:
            order_id: Order.
        """
        now = self._clock.now_ms()
        with self._uow() as uow:
            order = uow.orders.get(order_id)
            if order is None:
                return
            if order.state is not _State.RESERVED:
                # SUBMITTING without a result means an earlier send was
                # interrupted: the venue may hold the order, so reconcile.
                if order.state is _State.SUBMITTING:
                    self._to_unknown(
                        uow,
                        order,
                        now,
                        "worker found order SUBMITTING without a venue result",
                    )
                elif order.state is not _State.UNKNOWN_RECONCILING:
                    self._finish_outbox(uow, order_id)
                return
            account = uow.accounts.get(order.account_id)
            stored = uow.intents.get(order.intent_id)
            if account is None or stored is None:
                raise AssertionError("an order has its account and intent")
            version = order.version
            order.transition(_State.SUBMITTING, now)
            order.submit_attempts += 1
            uow.orders.save(order, version)
            self._audit_order(
                uow,
                order,
                "order.submitted",
                {
                    "attempt": order.submit_attempts,
                    "adapter_mode": order.mode.value,
                },
            )
        await self._send(order, account, stored.intent)

    async def _send(
        self,
        order: orders.Order,
        account: accounts.Account,
        intent: intents.OrderIntent,
    ) -> None:
        """Calls the adapter for an order already marked SUBMITTING.

        ``AdapterUnavailableError`` means the order was definitely not sent
        and fails it. A timeout, ``OSError`` or any other exception leaves
        the outcome unknown and moves it to UNKNOWN_RECONCILING. Otherwise
        the venue result is applied (REJECTED when not accepted), after
        pulling fills the result reports but does not carry.

        Args:
            order: Order snapshot.
            account: Account.
            intent: Intent the order belongs to.
        """
        adapter = self._resolver.resolve(account, order.mode)
        request = trading.VenueOrderRequest(
            account_id=order.account_id,
            instrument_id=order.instrument_id,
            client_order_id=order.client_order_id,
            side=order.side,
            order_type=order.order_type,
            quantity_base=order.quantity_base,
            time_in_force=order.time_in_force,
            reduce_only=order.reduce_only,
            limit_price=order.limit_price,
            trigger_price=order.trigger_price,
            trigger_basis=order.trigger_basis,
            leverage=intent.request.leverage,
            margin_mode=intent.request.margin_mode,
            outside_rth=intent.request.outside_rth,
        )
        try:
            result = await asyncio.wait_for(
                adapter.submit_order(request), self.settings.submit_timeout_s
            )
        except errors.AdapterUnavailableError as err:
            self._fail(order.order_id, err.message)
            return
        except (errors.AdapterTimeoutError, TimeoutError, OSError) as err:
            self._mark_unknown(order.order_id, f"submit outcome unknown: {err}")
            return
        except Exception as err:  # noqa: BLE001 - isolation point: any other adapter error leaves the outcome unknown, so the order is reconciled.
            self._mark_unknown(
                order.order_id, f"submit raised {type(err).__name__}"
            )
            return
        fills = list(result.fills)
        if result.accepted and result.state.filled_base > sum(
            (f.quantity_base for f in fills), decimals.ZERO
        ):
            fills.extend(await self._fetch_fills(adapter, order))
        await self._apply_state(
            order.order_id, result.state, fills, rejected=not result.accepted
        )

    def _fail(self, order_id: str, reason: str) -> None:
        """Marks an order FAILED (definitely not sent) and releases margin.

        Also fails its pending outbox items and audits ``order.failed``.
        Missing and terminal orders are left alone.

        Args:
            order_id: Order.
            reason: Reason stored on the order and its outbox items.
        """
        now = self._clock.now_ms()
        with self._uow() as uow:
            order = uow.orders.get(order_id)
            if order is None or order.state.is_terminal:
                return
            version = order.version
            order.transition(_State.FAILED, now, reason)
            uow.orders.save(order, version)
            uow.reservations.release_for_order(order_id, now)
            self._finish_outbox(uow, order_id, "FAILED", reason)
            self._audit_order(uow, order, "order.failed", {"reason": reason})
        self._publish_order(order)

    def _to_unknown(
        self,
        uow: repositories.UnitOfWork,
        order: orders.Order,
        now: int,
        reason: str,
    ) -> None:
        """Moves an order to UNKNOWN_RECONCILING inside a transaction.

        The order's latest outbox item becomes a RECONCILE item due after
        ``retry_backoff_ms``; without one, a RECONCILE item due now is
        added.

        Args:
            uow: Unit of work of the caller's transaction.
            order: Order (mutated).
            now: Time, epoch milliseconds.
            reason: Reason stored on the order and the outbox item.
        """
        version = order.version
        order.transition(_State.UNKNOWN_RECONCILING, now, reason)
        uow.orders.save(order, version)
        item = self._outbox_item(uow, order.order_id)
        if item is not None:
            uow.outbox.update(
                item.outbox_id,
                "PENDING",
                item.attempts,
                now + self.settings.retry_backoff_ms,
                reason,
                kind="RECONCILE",
            )
        else:
            uow.outbox.add(
                order.order_id, f"reconcile-{order.version}", "RECONCILE", now
            )
        self._audit_order(uow, order, "order.unknown", {"reason": reason})
        self._metrics.increment(
            "order_unknown_total", {"mode": order.mode.value}
        )

    def _mark_unknown(self, order_id: str, reason: str) -> None:
        """Records an unknown submit outcome; never resubmits blindly.

        Only an order still in SUBMITTING is moved.

        Args:
            order_id: Order.
            reason: Reason stored on the order.
        """
        now = self._clock.now_ms()
        with self._uow() as uow:
            order = uow.orders.get(order_id)
            if order is None or order.state is not _State.SUBMITTING:
                return
            self._to_unknown(uow, order, now, reason)
        self._publish_order(order)

    async def _fetch_fills(
        self, adapter: trading.TradingAdapter, order: orders.Order
    ) -> list[trading.VenueFill]:
        """Pulls venue fills of an order (REST reconciliation).

        Args:
            adapter: Adapter of the order's account and mode.
            order: Order.

        Returns:
            Fills of the order since it was created; empty when the venue
            query raises ``ExecutionError``.
        """
        try:
            fills = await adapter.list_recent_fills(
                order.account_id, order.created_at_ms
            )
        except errors.ExecutionError:
            return []
        return [f for f in fills if f.client_order_id == order.client_order_id]

    async def reconcile_order(self, order_id: str) -> None:
        """Resolves an UNKNOWN_RECONCILING (or stuck cancel) order.

        Returns at once when this process already has the order in
        flight. A terminal order, or one neither UNKNOWN_RECONCILING nor
        CANCEL_PENDING, only finishes its outbox items. A venue that cannot
        be queried by client order id (or whose capabilities are unknown)
        flags the order for manual intervention; a failed query is retried
        with backoff. When the venue has no record, an UNKNOWN_RECONCILING
        order is resubmitted within the budget and a CANCEL_PENDING order
        fails; otherwise the venue state and fills are applied.

        Args:
            order_id: Order.
        """
        if order_id in self._inflight:
            return
        self._inflight.add(order_id)
        try:
            await self._reconcile(order_id)
        finally:
            self._inflight.discard(order_id)

    async def _reconcile(self, order_id: str) -> None:
        """Implements ``reconcile_order``.

        Args:
            order_id: Order.
        """
        now = self._clock.now_ms()
        with self._uow() as uow:
            order = uow.orders.get(order_id)
            if order is None:
                return
            item = self._outbox_item(uow, order_id)
            if order.state.is_terminal or (
                order.state
                not in (_State.UNKNOWN_RECONCILING, _State.CANCEL_PENDING)
            ):
                self._finish_outbox(uow, order_id)
                return
            account = uow.accounts.get(order.account_id)
            stored = uow.intents.get(order.intent_id)
        if account is None or stored is None:
            raise AssertionError("an order has its account and intent")
        adapter = self._resolver.resolve(account, order.mode)
        try:
            caps = await adapter.capabilities(order.account_id)
        except errors.ExecutionError:
            caps = None
        if caps is None or not caps.query_by_client_id:
            self._manual(
                order_id,
                "venue cannot be queried by client id; "
                "manual reconciliation required",
            )
            return
        try:
            state = await asyncio.wait_for(
                adapter.get_order_by_client_id(
                    order.account_id, order.client_order_id
                ),
                self.settings.submit_timeout_s,
            )
        except (errors.ExecutionError, TimeoutError, OSError) as err:
            self._retry_later(order_id, item, now, f"query failed: {err}")
            return
        if state is None:
            if order.state is _State.CANCEL_PENDING:
                self._fail(order_id, "venue has no record of the order")
                return
            await self._resubmit_if_allowed(order, account, stored.intent)
            return
        fills = await self._fetch_fills(adapter, order)
        await self._apply_state(order_id, state, fills, rejected=False)

    def _manual(self, order_id: str, reason: str) -> None:
        """Stops automatic recovery and flags manual intervention.

        The order keeps its state; its pending outbox items become MANUAL.

        Args:
            order_id: Order.
            reason: Reason stored on the order and its outbox items.
        """
        now = self._clock.now_ms()
        with self._uow() as uow:
            order = uow.orders.get(order_id)
            if order is None:
                return
            version = order.version
            order.manual_intervention = True
            order.state_reason = reason
            order.updated_at_ms = now
            # No transition bumps the version here, so do it by hand for
            # the optimistic-concurrency save.
            order.version += 1
            uow.orders.save(order, version)
            self._finish_outbox(uow, order_id, "MANUAL", reason)
            self._audit_order(
                uow, order, "order.manual_intervention", {"reason": reason}
            )
        self._metrics.increment("order_manual_intervention_total")
        self._publish_order(order)

    def _retry_later(
        self,
        order_id: str,
        item: repositories.OutboxItem | None,
        now: int,
        reason: str,
    ) -> None:
        """Schedules another reconciliation attempt with backoff.

        The delay is ``retry_backoff_ms * 2 ** min(attempts, 6)``. Once
        ``max_reconcile_attempts`` is reached the order is flagged for
        manual intervention instead. Without an outbox item nothing is
        scheduled.

        Args:
            order_id: Order.
            item: The order's latest outbox item, if any.
            now: Time, epoch milliseconds.
            reason: Reason stored on the outbox item.
        """
        if item is None:
            return
        attempts = item.attempts + 1
        if attempts >= self.settings.max_reconcile_attempts:
            self._manual(order_id, f"reconciliation exhausted: {reason}")
            return
        delay = self.settings.retry_backoff_ms * (2 ** min(attempts, 6))
        with self._uow() as uow:
            uow.outbox.update(
                item.outbox_id, "PENDING", attempts, now + delay, reason
            )

    async def _resubmit_if_allowed(
        self,
        order: orders.Order,
        account: accounts.Account,
        intent: intents.OrderIntent,
    ) -> None:
        """Resubmits an order the venue confirmed it never received.

        The order fails instead once ``submit_attempts`` (the first send
        included) exceeds ``max_resubmits``. It is re-read and resent only
        if still UNKNOWN_RECONCILING.

        Args:
            order: Order in UNKNOWN_RECONCILING.
            account: Account.
            intent: Intent the order belongs to.
        """
        now = self._clock.now_ms()
        if order.submit_attempts > self.settings.max_resubmits:
            self._fail(
                order.order_id,
                "venue confirmed absent; resubmit budget exhausted",
            )
            return
        with self._uow() as uow:
            fresh = uow.orders.get(order.order_id)
            if fresh is None or fresh.state is not _State.UNKNOWN_RECONCILING:
                return
            version = fresh.version
            fresh.transition(
                _State.SUBMITTING, now, "venue confirmed absent; resubmitting"
            )
            fresh.submit_attempts += 1
            uow.orders.save(fresh, version)
            self._audit_order(
                uow,
                fresh,
                "order.resubmitted",
                {"attempt": fresh.submit_attempts},
            )
        await self._send(fresh, account, intent)

    # ------------------------------------------------------------------
    # Applying venue truth
    # ------------------------------------------------------------------

    async def apply_venue_events(
        self, account_id: str, events: Sequence[trading.VenueEvent]
    ) -> int:
        """Applies private-stream events idempotently.

        Duplicates are ignored by venue fill id; a status report that shows
        more filled than recorded first pulls the missing fills from the
        venue. Events of unknown client orders are skipped (fills with a
        warning).

        Args:
            account_id: Account.
            events: Events in delivery order.

        Returns:
            Number of fill and order events that changed state.
        """
        changed = 0
        for event in events:
            if event.fill is not None:
                order = self._order_by_client(
                    account_id, event.fill.client_order_id
                )
                if order is None:
                    _LOG.warning(
                        "fill for unknown client order on %s", account_id
                    )
                    continue
                if await self._apply_fills(order.order_id, [event.fill]):
                    changed += 1
            if event.order is not None:
                order = self._order_by_client(
                    account_id, event.order.client_order_id
                )
                if order is None:
                    continue
                fills: list[trading.VenueFill] = []
                if event.order.filled_base > order.filled_base:
                    with self._uow() as uow:
                        account = uow.accounts.get(account_id)
                    if account is None:
                        raise AssertionError("an order has its account")
                    adapter = self._resolver.resolve(account, order.mode)
                    fills = await self._fetch_fills(adapter, order)
                if await self._apply_state(
                    order.order_id, event.order, fills, rejected=False
                ):
                    changed += 1
        return changed

    async def apply_venue_fills(
        self, account_id: str, fills: Sequence[trading.VenueFill]
    ) -> int:
        """Applies fills pulled during reconciliation.

        Args:
            account_id: Account.
            fills: Venue fills.

        Returns:
            Fills that were new; duplicates and fills of unknown client
            orders are skipped.
        """
        applied = 0
        for fill in fills:
            order = self._order_by_client(account_id, fill.client_order_id)
            if order is not None and await self._apply_fills(
                order.order_id, [fill]
            ):
                applied += 1
        return applied

    def _order_by_client(
        self, account_id: str, client_order_id: str
    ) -> orders.Order | None:
        """Loads an order by client id.

        Args:
            account_id: Account.
            client_order_id: Client order id.

        Returns:
            The order, or ``None`` when the account has no such order.
        """
        with self._uow() as uow:
            return uow.orders.get_by_client_id(account_id, client_order_id)

    async def _apply_fills(
        self, order_id: str, fills: Sequence[trading.VenueFill]
    ) -> bool:
        """Applies fills without a status report.

        Args:
            order_id: Order.
            fills: Fills.

        Returns:
            True if any fill was new.
        """
        changed, followups = self._apply_in_tx(order_id, None, fills, False)
        await self._after_change(followups)
        return changed

    async def _apply_state(
        self,
        order_id: str,
        state: trading.VenueOrderState,
        fills: Sequence[trading.VenueFill],
        rejected: bool,
    ) -> bool:
        """Applies fills then a venue status report.

        Args:
            order_id: Order.
            state: Venue state.
            fills: Fills to apply first.
            rejected: True when the venue rejected the submit.

        Returns:
            True if the order changed.
        """
        changed, followups = self._apply_in_tx(order_id, state, fills, rejected)
        await self._after_change(followups)
        return changed

    def _apply_in_tx(
        self,
        order_id: str,
        state: trading.VenueOrderState | None,
        fills: Sequence[trading.VenueFill],
        rejected: bool,
    ) -> tuple[bool, list[orders.Order]]:
        """Applies venue truth to an order in one transaction.

        New fills are recorded first, then the status report. On a change
        the order is saved; a terminal order releases its reservation and
        finishes its outbox items (an ACKNOWLEDGED or PARTIALLY_FILLED one
        only finishes them); protective children are spawned and the
        change is audited. Fills, order and position are published after
        commit.

        Args:
            order_id: Order.
            state: Venue state, if any.
            fills: Venue fills.
            rejected: True when the venue rejected the submit.

        Returns:
            ``(changed, orders to act on after commit)``: the order and any
            new protective children, or ``(False, [])`` when nothing
            changed.
        """
        now = self._clock.now_ms()
        with self._uow() as uow:
            order = uow.orders.get(order_id)
            if order is None:
                return False, []
            version = order.version
            before = (order.state, order.filled_base)
            if (
                state is not None
                and state.exchange_order_id
                and (order.exchange_order_id is None)
            ):
                order.exchange_order_id = state.exchange_order_id
            new_fills = []
            for venue_fill in fills:
                fill = self._record_fill(uow, order, venue_fill, now)
                if fill is not None:
                    new_fills.append(fill)
            if state is not None:
                self._apply_status(uow, order, state, now, rejected)
            changed = (order.state, order.filled_base) != before or bool(
                new_fills
            )
            if not changed:
                return False, []
            uow.orders.save(order, version)
            if order.state.is_terminal:
                uow.reservations.release_for_order(order.order_id, now)
                self._finish_outbox(uow, order.order_id)
            elif order.state in (_State.ACKNOWLEDGED, _State.PARTIALLY_FILLED):
                self._finish_outbox(uow, order.order_id)
            children = self._spawn_protection(uow, order, now)
            self._audit_order(
                uow,
                order,
                "order.updated",
                {
                    "state": order.state.value,
                    "filled_base": decimals.fmt(order.filled_base),
                    "new_fills": [f.exchange_fill_id for f in new_fills],
                },
            )
        for fill in new_fills:
            self._publisher.publish(
                order.tenant_id,
                "fill.created",
                "fills",
                fill.to_contract(),
                order.account_id,
            )
        self._publish_order(order)
        if new_fills:
            self._publish_position(order)
        return True, [order, *children]

    def _record_fill(
        self,
        uow: repositories.UnitOfWork,
        order: orders.Order,
        venue_fill: trading.VenueFill,
        now: int,
    ) -> orders.Fill | None:
        """Records one fill and nets it into the position.

        Args:
            uow: Unit of work.
            order: Order (mutated: fill ids, filled quantity and state).
            venue_fill: Venue fill.
            now: Time, epoch milliseconds.

        Returns:
            The new fill, or ``None`` for a duplicate (fill id known to the
            order or to the fill store).
        """
        if venue_fill.exchange_fill_id in order.fill_ids:
            return None
        position = uow.positions.get(
            order.account_id, order.instrument_id, order.mode
        )
        if position is None:
            position = positions.Position(
                position_id=positions.position_id(
                    order.account_id, order.instrument_id, order.mode.value
                ),
                tenant_id=order.tenant_id,
                account_id=order.account_id,
                instrument_id=order.instrument_id,
                mode=order.mode,
            )
        stored = uow.intents.get(order.intent_id)
        leverage = None if stored is None else stored.intent.request.leverage
        realized = position.apply_fill(
            order.side,
            venue_fill.quantity_base,
            venue_fill.price,
            venue_fill.fee,
            venue_fill.ts_ms,
            leverage,
        )
        fill = orders.Fill(
            fill_id=self._ids.new_id(),
            order_id=order.order_id,
            account_id=order.account_id,
            instrument_id=order.instrument_id,
            exchange_fill_id=venue_fill.exchange_fill_id,
            side=order.side,
            price=venue_fill.price,
            quantity_base=venue_fill.quantity_base,
            fee=venue_fill.fee,
            fee_asset=venue_fill.fee_asset,
            liquidity=venue_fill.liquidity,
            ts_ms=venue_fill.ts_ms,
            mode=order.mode,
            realized_pnl=realized,
        )
        if not uow.fills.add(fill, order.tenant_id):
            order.fill_ids.add(venue_fill.exchange_fill_id)
            return None
        order.apply_fill(fill, now)
        uow.positions.save(position)
        return fill

    def _apply_status(
        self,
        uow: repositories.UnitOfWork,
        order: orders.Order,
        state: trading.VenueOrderState,
        now: int,
        rejected: bool,
    ) -> None:
        """Applies a venue status report (fills already applied).

        Terminal orders do not change. A rejection moves to REJECTED where
        allowed. An acknowledgement, or a fill report on an order still
        SUBMITTING or UNKNOWN_RECONCILING, acknowledges it; an
        acknowledgement of a CANCEL_PENDING order means the cancel failed,
        so it returns to ACKNOWLEDGED or PARTIALLY_FILLED. CANCELED and
        EXPIRED apply only to an order that is not fully filled. Fill
        states need nothing more: the fills already moved the order.

        Args:
            uow: Unit of work (unused).
            order: Order (mutated).
            state: Venue state.
            now: Time, epoch milliseconds.
            rejected: True when the venue rejected the submit.
        """
        del uow  # Unused.
        if order.state.is_terminal:
            return
        if state.exchange_order_id:
            order.exchange_order_id = state.exchange_order_id
        target = state.status
        if rejected or target is _State.REJECTED:
            if orders.can_transition(order.state, _State.REJECTED):
                order.transition(
                    _State.REJECTED, now, state.reason or "rejected"
                )
                self._metrics.increment("order_rejected_total")
            return
        if target is _State.ACKNOWLEDGED or (
            target in (_State.PARTIALLY_FILLED, _State.FILLED)
            and order.state in (_State.SUBMITTING, _State.UNKNOWN_RECONCILING)
        ):
            if order.state in (_State.SUBMITTING, _State.UNKNOWN_RECONCILING):
                order.acknowledge(state.exchange_order_id, now)
            elif (
                order.state is _State.CANCEL_PENDING
                and target is _State.ACKNOWLEDGED
            ):
                order.transition(
                    _State.PARTIALLY_FILLED
                    if order.filled_base
                    else _State.ACKNOWLEDGED,
                    now,
                    state.reason or "cancel rejected",
                )
            return
        if target in (_State.CANCELED, _State.EXPIRED):
            if order.filled_base >= order.quantity_base:
                return
            if orders.can_transition(order.state, target):
                order.transition(target, now, state.reason)

    def _spawn_protection(
        self, uow: repositories.UnitOfWork, order: orders.Order, now: int
    ) -> list[orders.Order]:
        """Creates reduce-only SL/TP children once a primary order is done.

        Runs once per primary order, when it is terminal with fills and its
        intent has a bracket: a stop-loss (SL) for the whole filled
        quantity and one limit take-profit (TP) per leg, each leg floored
        to the lot and all legs together capped at the filled quantity.
        Children are GTC (good-till-cancelled) orders on the exit side with
        a zero reservation.

        Args:
            uow: Unit of work.
            order: Order that just changed.
            now: Time, epoch milliseconds.

        Returns:
            New child orders (RESERVED, queued in the outbox), or an empty
            list when none are due.
        """
        if order.role is not enums.OrderRole.PRIMARY or not (
            order.state.is_terminal and order.filled_base > 0
        ):
            return []
        stored = uow.intents.get(order.intent_id)
        if stored is None or stored.intent.request.bracket.empty:
            return []
        if uow.orders.list(parent_order_id=order.order_id):
            return []
        bracket = stored.intent.request.bracket
        children: list[orders.Order] = []
        exit_side = order.side.opposite

        def child(  # noqa: PLR0917 - the fields of one child order; both call sites pass them in order.
            role: enums.OrderRole,
            suffix: str,
            qty: decimal.Decimal,
            order_type: enums.OrderType,
            limit: decimal.Decimal | None,
            trigger: decimal.Decimal | None,
            basis: enums.PriceBasis | None,
        ) -> orders.Order:
            """Builds one child order in RESERVED.

            Args:
                role: Child role.
                suffix: Client id suffix.
                qty: Quantity.
                order_type: Order type.
                limit: Limit price.
                trigger: Trigger price.
                basis: Trigger basis.

            Returns:
                Child order.
            """
            item = orders.Order(
                order_id=self._ids.new_id(),
                intent_id=order.intent_id,
                tenant_id=order.tenant_id,
                account_id=order.account_id,
                instrument_id=order.instrument_id,
                client_order_id=f"{order.client_order_id}-{suffix}",
                mode=order.mode,
                role=role,
                side=exit_side,
                order_type=order_type,
                time_in_force=enums.TimeInForce.GTC,
                quantity_base=qty,
                reduce_only=True,
                state=_State.DRAFT,
                created_at_ms=now,
                updated_at_ms=now,
                limit_price=limit,
                trigger_price=trigger,
                trigger_basis=basis,
                parent_order_id=order.order_id,
                strategy_version_id=order.strategy_version_id,
                signal_id=order.signal_id,
            )
            item.transition(_State.VALIDATED, now)
            item.transition(_State.RESERVED, now, "protective order")
            return item

        if bracket.stop_loss is not None:
            children.append(
                child(
                    enums.OrderRole.STOP_LOSS,
                    "sl",
                    order.filled_base,
                    enums.OrderType.STOP_MARKET,
                    None,
                    bracket.stop_loss.trigger,
                    bracket.stop_loss.basis,
                )
            )
        remaining = order.filled_base
        # Without cached metadata, the precision of the order quantity
        # stands in for the lot.
        lot_step = self._lots.get(order.instrument_id) or _quantum(
            order.quantity_base
        )
        for index, leg in enumerate(bracket.take_profit, start=1):
            qty = decimals.floor_to_step(
                order.filled_base * leg.fraction, lot_step
            )
            qty = min(qty, remaining)
            if qty <= 0:
                continue
            remaining -= qty
            children.append(
                child(
                    enums.OrderRole.TAKE_PROFIT,
                    f"tp{index}",
                    qty,
                    enums.OrderType.LIMIT,
                    leg.trigger,
                    None,
                    None,
                )
            )
        for item in children:
            uow.orders.add(item)
            uow.reservations.add(
                self._ids.new_id(),
                item.tenant_id,
                item.account_id,
                item.intent_id,
                item.order_id,
                decimals.ZERO,
                now,
                item.mode,
            )
            uow.outbox.add(item.order_id, "submit", "SUBMIT", now)
            self._audit_order(
                uow,
                item,
                "order.protection_created",
                {"parent_order_id": order.order_id},
            )
        return children

    async def _after_change(self, changed: list[orders.Order]) -> None:
        """Runs follow-ups after a committed change.

        Dispatches new protective children (with ``inline_dispatch``) and,
        when a protective order fills, cancels its one-cancels-other (OCO)
        siblings.

        Args:
            changed: Orders changed or created.
        """
        for order in changed:
            if order.state is _State.RESERVED and self.settings.inline_dispatch:
                await self.dispatch_order(order.order_id)
            elif (
                order.role
                in (enums.OrderRole.STOP_LOSS, enums.OrderRole.TAKE_PROFIT)
                and order.state is _State.FILLED
                and order.parent_order_id is not None
            ):
                with self._uow() as uow:
                    siblings = [
                        s
                        for s in uow.orders.list(
                            parent_order_id=order.parent_order_id
                        )
                        if s.order_id != order.order_id
                        and not s.state.is_terminal
                    ]
                for sibling in siblings:
                    await self._cancel(sibling.order_id, "system:oco")

    # ------------------------------------------------------------------
    # Cancel and amend
    # ------------------------------------------------------------------

    async def cancel_order(
        self, principal: identity.Principal, order_id: str
    ) -> orders.Order:
        """Requests cancellation of a tenant order.

        Terminal and CANCEL_PENDING orders are left as they are.

        Args:
            principal: Caller.
            order_id: Order.

        Returns:
            Order after the venue response.

        Raises:
            ExecutionError: ``NOT_FOUND`` or ``ORDER_UNKNOWN`` when the
                outcome of the submit is still being reconciled.
        """
        self.get_order(principal, order_id)
        await self._cancel(order_id, principal.actor)
        return self.get_order(principal, order_id)

    async def _cancel(self, order_id: str, actor: str) -> None:
        """Cancels an order on behalf of an actor.

        A RESERVED order was never sent, so it is canceled locally and its
        reservation released. A working order moves to CANCEL_PENDING
        before the venue call; a timeout queues a RECONCILE item, a venue
        error returns it to its working state, and a venue answer is
        applied with any missing fills.

        Args:
            order_id: Order.
            actor: Audit actor.

        Raises:
            ExecutionError: ``ORDER_UNKNOWN`` (retryable) while the order is
                SUBMITTING or UNKNOWN_RECONCILING.
        """
        now = self._clock.now_ms()
        with self._uow() as uow:
            order = uow.orders.get(order_id)
            if (
                order is None
                or order.state.is_terminal
                or (order.state is _State.CANCEL_PENDING)
            ):
                return
            if order.state in (_State.SUBMITTING, _State.UNKNOWN_RECONCILING):
                raise errors.ExecutionError(
                    "ORDER_UNKNOWN",
                    "order outcome is being reconciled; cancel is not safe yet",
                    retryable=True,
                )
            version = order.version
            if order.state is _State.RESERVED:
                order.transition(_State.CANCELED, now, "canceled before submit")
                uow.orders.save(order, version)
                uow.reservations.release_for_order(order_id, now)
                self._finish_outbox(uow, order_id)
                self._audit_order(
                    uow,
                    order,
                    "order.canceled",
                    {"actor": actor, "sent": False},
                )
                self._publish_order(order)
                return
            previous = order.state
            order.transition(_State.CANCEL_PENDING, now, f"cancel by {actor}")
            uow.orders.save(order, version)
            account = uow.accounts.get(order.account_id)
            self._audit_order(
                uow, order, "order.cancel_requested", {"actor": actor}
            )
        if account is None:
            raise AssertionError("an order has its account")
        self._publish_order(order)
        adapter = self._resolver.resolve(account, order.mode)
        try:
            state = await asyncio.wait_for(
                adapter.cancel_order(order.account_id, order.client_order_id),
                self.settings.submit_timeout_s,
            )
        except (errors.AdapterTimeoutError, TimeoutError, OSError):
            with self._uow() as uow:
                uow.outbox.add(
                    order_id, f"cancel-{order.version}", "RECONCILE", now
                )
            return
        except errors.ExecutionError as err:
            with self._uow() as uow:
                fresh = uow.orders.get(order_id)
                if fresh is not None and fresh.state is _State.CANCEL_PENDING:
                    fresh_version = fresh.version
                    working = (_State.ACKNOWLEDGED, _State.PARTIALLY_FILLED)
                    restored = (
                        previous if previous in working else _State.ACKNOWLEDGED
                    )
                    fresh.transition(
                        restored, now, f"cancel failed: {err.message}"
                    )
                    uow.orders.save(fresh, fresh_version)
            return
        fills = []
        if state.filled_base > order.filled_base:
            fills = await self._fetch_fills(adapter, order)
        await self._apply_state(order_id, state, fills, rejected=False)

    async def amend_order(  # noqa: PLR0917 - public API; the trading router and tests pass the amend fields positionally.
        self,
        principal: identity.Principal,
        order_id: str,
        limit_price: decimal.Decimal | None,
        trigger_price: decimal.Decimal | None,
        quantity_base: decimal.Decimal | None,
        expected_version: int | None,
    ) -> orders.Order:
        """Amends a working order after re-validation and risk.

        Only ACKNOWLEDGED and PARTIALLY_FILLED orders on a venue that
        supports amend qualify. New values are not rounded: prices must be
        on the tick grid and the quantity a multiple of the lot. The
        amended order is re-checked by the Risk Gateway as a fresh draft;
        failures in the DUPLICATE domain are ignored because the order
        being amended is itself open.

        Args:
            principal: Caller.
            order_id: Order.
            limit_price: New limit price, or ``None`` to keep it.
            trigger_price: New trigger price, or ``None`` to keep it.
            quantity_base: New total quantity, or ``None`` to keep it.
            expected_version: Optimistic concurrency guard, if any.

        Returns:
            Amended order.

        Raises:
            ExecutionError: ``NOT_FOUND``, ``CONFLICT``, ``UNSUPPORTED``,
                ``MARKET_STALE``, ``INVALID_TICK_SIZE``,
                ``INVALID_LOT_SIZE``, or the primary code of a failing risk
                check (``RISK_LIMIT`` when it has none).
        """
        order = self.get_order(principal, order_id)
        if expected_version is not None and expected_version != order.version:
            raise errors.ExecutionError(
                "CONFLICT",
                "order changed since it was displayed",
                details={"current_version": order.version},
            )
        if order.state not in (_State.ACKNOWLEDGED, _State.PARTIALLY_FILLED):
            raise errors.ExecutionError(
                "CONFLICT", f"cannot amend an order in {order.state.value}"
            )
        account = self._owned_account(principal, order.account_id)
        adapter = self._resolver.resolve(account, order.mode)
        caps = await adapter.capabilities(order.account_id)
        if not caps.amend:
            raise errors.ExecutionError(
                "UNSUPPORTED", "venue does not support amend"
            )
        instrument = await self._instrument(order.instrument_id)
        if instrument is None:
            raise errors.ExecutionError(
                "MARKET_STALE", "instrument metadata unavailable"
            )
        for price in (limit_price, trigger_price):
            if price is not None and not equities.on_tick(price, instrument):
                raise errors.ExecutionError(
                    "INVALID_TICK_SIZE", "price is not on the tick grid"
                )
        if quantity_base is not None and not decimals.is_multiple(
            quantity_base, instrument.lot_size
        ):
            raise errors.ExecutionError(
                "INVALID_LOT_SIZE", "quantity is not a multiple of the lot"
            )
        with self._uow() as uow:
            stored = uow.intents.get(order.intent_id)
        if stored is None:
            raise AssertionError("an order has its intent")
        base = stored.intent.request
        draft = dataclasses.replace(
            base,
            quantity_base=quantity_base or order.quantity_base,
            limit_price=limit_price
            if limit_price is not None
            else order.limit_price,
            trigger_price=trigger_price
            if trigger_price is not None
            else order.trigger_price,
            reduce_only=order.reduce_only,
            preview_token=None,
            signal_id=None,
            bracket=intents.Bracket(),
        )
        if order.role is not enums.OrderRole.PRIMARY:
            draft = dataclasses.replace(
                draft,
                side=order.side,
                order_type=order.order_type,
                position_effect=enums.PositionEffect.REDUCE,
                time_in_force=order.time_in_force,
                trigger_basis=None,
            )
        ctx, _ = await self._build_context(
            principal, account, draft, instrument, None, None
        )
        decision = self._gateway.evaluate(ctx)
        blocking = [
            c
            for c in decision.failures()
            if c.domain is not enums.RiskDomain.DUPLICATE
        ]
        if blocking:
            raise errors.ExecutionError(
                decision.primary_code(),
                "amendment rejected by risk",
                details={"risk": decision.to_contract()},
            )
        state = await adapter.amend_order(
            order.account_id,
            order.client_order_id,
            limit_price,
            trigger_price,
            quantity_base,
        )
        now = self._clock.now_ms()
        with self._uow() as uow:
            fresh = uow.orders.get(order_id)
            if fresh is None:
                raise AssertionError("orders are never deleted")
            version = fresh.version
            fresh.amend(now, limit_price, trigger_price, quantity_base)
            if state.exchange_order_id:
                fresh.exchange_order_id = state.exchange_order_id
            uow.orders.save(fresh, version)
            self._audit_order(
                uow,
                fresh,
                "order.amended",
                {
                    "limit_price": decimals.fmt_optional(limit_price),
                    "trigger_price": decimals.fmt_optional(trigger_price),
                    "quantity_base": decimals.fmt_optional(quantity_base),
                    "actor": principal.actor,
                },
            )
        self._publish_order(fresh)
        return fresh

    # ------------------------------------------------------------------
    # Close
    # ------------------------------------------------------------------

    async def close_request(  # noqa: PLR0917 - public API; routers, the kill switch and tests pass these positionally.
        self,
        principal: identity.Principal,
        position_id: str,
        fraction: decimal.Decimal,
        order_type: enums.OrderType,
        limit_price: decimal.Decimal | None,
        source: enums.IntentSource = enums.IntentSource.MANUAL_TICKET,
    ) -> intents.OrderIntentRequest:
        """Builds a reduce-only close that can never flip the position.

        Securities on board-lot markets close whole board lots first (see
        ``_security_close_qty``). A partial fraction is floored to the lot.
        Market closes are IOC (immediate-or-cancel); limit closes are DAY
        for securities and GTC otherwise. Nothing is submitted.

        Args:
            principal: Caller.
            position_id: Position.
            fraction: Fraction to close (0 < f <= 1).
            order_type: Market or limit.
            limit_price: Limit price for limit closes.
            source: Intent source.

        Returns:
            Close request; ``position_effect`` is CLOSE when it covers the
            whole position, else REDUCE.

        Raises:
            ExecutionError: ``NOT_FOUND``, ``CONFLICT`` (flat),
                ``VALIDATION_FAILED``, ``MARKET_STALE`` (partial close
                without metadata) or ``INVALID_LOT_SIZE`` (fraction rounds
                to zero).
        """
        if not (decimals.ZERO < fraction <= decimals.ONE):
            raise errors.ExecutionError(
                "VALIDATION_FAILED", "fraction must be in (0, 1]"
            )
        if order_type not in (enums.OrderType.MARKET, enums.OrderType.LIMIT):
            raise errors.ExecutionError(
                "VALIDATION_FAILED", "close uses market or limit orders"
            )
        with self._uow() as uow:
            pos = uow.positions.get_by_id(position_id)
        if pos is None or pos.tenant_id != principal.tenant_id:
            raise errors.ExecutionError("NOT_FOUND", "position not found")
        if pos.signed_qty == 0:
            raise errors.ExecutionError("CONFLICT", "position is flat")
        instrument = await self._instrument(pos.instrument_id)
        qty = pos.quantity
        security = equities.is_security(pos.instrument_id)
        if security:
            qty = _security_close_qty(pos.quantity, instrument, order_type)
        if fraction < decimals.ONE:
            if instrument is None:
                raise errors.ExecutionError(
                    "MARKET_STALE", "instrument metadata unavailable"
                )
            qty = decimals.floor_to_step(qty * fraction, instrument.lot_size)
            if qty <= 0:
                raise errors.ExecutionError(
                    "INVALID_LOT_SIZE", "fraction rounds to zero"
                )
        side = (
            enums.OrderSide.SELL if pos.signed_qty > 0 else enums.OrderSide.BUY
        )
        limit_tif = enums.TimeInForce.DAY if security else enums.TimeInForce.GTC
        return intents.OrderIntentRequest(
            account_id=pos.account_id,
            instrument_id=pos.instrument_id,
            side=side,
            position_effect=enums.PositionEffect.CLOSE
            if qty == pos.quantity
            else enums.PositionEffect.REDUCE,
            order_type=order_type,
            quantity_base=qty,
            time_in_force=limit_tif
            if order_type is enums.OrderType.LIMIT
            else enums.TimeInForce.IOC,
            reduce_only=True,
            mode=pos.mode,
            source=source,
            limit_price=limit_price,
            leverage=pos.leverage,
        )

    # ------------------------------------------------------------------
    # Publishing and audit helpers
    # ------------------------------------------------------------------

    def _audit_order(
        self,
        uow: repositories.UnitOfWork,
        order: orders.Order,
        action: str,
        payload: dict[str, object],
    ) -> None:
        """Audits an order change as ``system:oms``.

        Args:
            uow: Unit of work of the change.
            order: Order.
            action: Action name, such as ``order.submitted``.
            payload: Details; the client order id and mode are added.
        """
        self._audit.record(
            uow,
            tenant_id=order.tenant_id,
            actor="system:oms",
            action=action,
            entity_type="order",
            entity_id=order.order_id,
            account_id=order.account_id,
            payload={
                "client_order_id": order.client_order_id,
                "mode": order.mode.value,
                **payload,
            },
        )

    def _publish_order(self, order: orders.Order) -> None:
        """Publishes ``order.updated``.

        Args:
            order: Order.
        """
        self._publisher.publish(
            order.tenant_id,
            "order.updated",
            "orders",
            order.to_contract(),
            order.account_id,
        )

    def _publish_position(self, order: orders.Order) -> None:
        """Publishes ``position.updated`` for an order's position.

        The event carries no mark and is flagged stale.

        Args:
            order: Order whose position changed.
        """
        with self._uow() as uow:
            pos = uow.positions.get(
                order.account_id, order.instrument_id, order.mode
            )
        if pos is not None:
            self._publisher.publish(
                order.tenant_id,
                "position.updated",
                "positions",
                pos.to_contract(None, self._clock.now_ms(), True),
                order.account_id,
            )


def _security_close_qty(
    held: decimal.Decimal,
    instrument: market.InstrumentMeta | None,
    order_type: enums.OrderType,
) -> decimal.Decimal:
    """Sizes a securities close to what one order may carry.

    Board-lot markets (TWSE, TPEx) cannot mix board and odd lots in one
    order: above one board lot the close covers the whole lots (close the
    odd remainder separately); below one lot it is an odd-lot limit order.

    Args:
        held: Shares held.
        instrument: Instrument metadata, if known.
        order_type: Close order type.

    Returns:
        Shares to close.

    Raises:
        ExecutionError: ``VALIDATION_FAILED`` for an odd-lot market close.
    """
    lot = None if instrument is None else instrument.board_lot
    kind = equities.lot_kind(held, lot)
    if kind is equities.LotKind.MIXED:
        if lot is None:
            raise AssertionError("a MIXED holding has a board lot")
        return decimals.floor_to_step(held, lot)
    if kind is equities.LotKind.ODD and order_type is enums.OrderType.MARKET:
        raise errors.ExecutionError(
            "VALIDATION_FAILED",
            "odd-lot positions close with a limit order (DAY)",
        )
    return held


def _quantum(value: decimal.Decimal) -> decimal.Decimal:
    """Returns the smallest increment implied by a decimal's exponent.

    Trailing zeros do not count, and integers give 1.

    Args:
        value: Finite quantity such as ``0.010``.

    Returns:
        Increment such as ``0.01``.
    """
    exponent = value.normalize().as_tuple().exponent
    if not isinstance(exponent, int):
        raise AssertionError("order quantities are finite")
    return decimal.Decimal(1).scaleb(min(exponent, 0))


def _protection(
    caps: accounts.TradingCapabilities | None,
    request: intents.OrderIntentRequest,
) -> dict[str, object]:
    """Describes how protective orders would be held.

    Args:
        caps: Negotiated capabilities; ``None`` (unknown) counts as no
            native protective orders.
        request: Request.

    Returns:
        Contract ``ProtectionInfo``.
    """
    native_sl = bool(caps and caps.native_stop_loss)
    native_tp = bool(caps and caps.native_take_profit)
    if request.bracket.empty:
        note = "No protective orders requested."
    elif native_sl and native_tp:
        note = (
            "Stop-loss and take-profit are placed at the venue as "
            "reduce-only trigger orders after the entry fills."
        )
    else:
        note = (
            "Protective orders are software-managed by Joseph and are "
            "not guaranteed; Auto-Live refuses this path."
        )
    return {
        "native_stop_loss": native_sl,
        "native_take_profit": native_tp,
        "note": note,
    }


async def _venue_balances(
    adapter: trading.TradingAdapter,
    account: accounts.Account,
    mode: enums.OrderMode,
    caps: accounts.TradingCapabilities | None,
) -> trading.VenueAccountSnapshot | None:
    """Fetches venue balances for LIVE risk checks on a venue account.

    Only an ``AVAILABLE`` adapter is asked (so a closed gate or missing
    credentials never cause network calls); failures yield ``None``, which
    keeps the margin check UNKNOWN and the order blocked.

    Args:
        adapter: Venue adapter.
        account: Account.
        mode: Order mode.
        caps: Negotiated capabilities.

    Returns:
        Snapshot or ``None``.
    """
    if (
        mode is not enums.OrderMode.LIVE
        or account.starting_equity is not None
        or caps is None
        or caps.status is not enums.TradingStatus.AVAILABLE
    ):
        return None
    try:
        return await adapter.account_snapshot(account.account_id)
    except errors.ExecutionError:
        return None
