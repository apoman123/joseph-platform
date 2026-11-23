"""Account service: paper provisioning, venue connections, summaries.

Credentials are sealed by the vault before persistence and are never
returned, logged or audited. Withdrawal-capable keys are refused, and DEX
connections accept only delegated agent/API keys, never seed phrases.
Broker connections carry only the broker account number: broker logins
(IB Gateway, Shioaji keys and certificates) are deployment secrets read
from the environment or secret files, never sent through the browser.

Manual LIVE orders also need a server-side, time-limited LIVE arming of the
account, granted with a step-up code (verified exactly like AUTO_LIVE
deployment consent) and an explicit risk acknowledgement, bounded by
``JOSEPH_LIVE_ARM_MAX_MS`` (8 hours by default, never more than 24 hours).
Grants, denials and revocations are audited; revoking credentials or losing
LIVE on re-verification disarms the account.
"""

from __future__ import annotations

import dataclasses
import decimal
import re
from collections.abc import Mapping

from joseph_execution.audit import service as audit_service
from joseph_execution.domain import accounts
from joseph_execution.domain import enums
from joseph_execution.domain import equities
from joseph_execution.domain import errors
from joseph_execution.domain import identity
from joseph_execution.oms import valuation
from joseph_execution.ports import market as market_port
from joseph_execution.ports import repositories
from joseph_execution.ports import services
from joseph_execution.ports import trading

LIVE_VENUES = ("binance", "hyperliquid", "lighter", "bitget", "mexc")
DEX_VENUES = ("hyperliquid", "lighter")
_FORBIDDEN_FIELDS = (
    "seed",
    "mnemonic",
    "main_private_key",
    "master_key",
    "wallet_private_key",
)
BROKER_ACCOUNT_FIELD = "account"
"""The only credential field of a broker connection (account number)."""

_BROKER_ACCOUNT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{2,31}$")
_OPEN = (
    enums.OrderState.RESERVED,
    enums.OrderState.SUBMITTING,
    enums.OrderState.ACKNOWLEDGED,
    enums.OrderState.PARTIALLY_FILLED,
    enums.OrderState.CANCEL_PENDING,
    enums.OrderState.UNKNOWN_RECONCILING,
)


class AccountService:
    """Manages accounts and builds ``AccountSummary`` read models."""

    def __init__(  # noqa: PLR0917
        self,
        uow_factory: repositories.UnitOfWorkFactory,
        resolver: trading.AdapterResolver,
        market_data: market_port.MarketDataGateway,
        vault: services.CredentialVault,
        audit_log: audit_service.AuditLog,
        clock: services.Clock,
        id_gen: services.IdGenerator,
        paper_starting_equity: decimal.Decimal,
        paper_twd_starting_cash: decimal.Decimal | None = None,
        *,
        step_up: services.StepUpVerifier,
        live_trading_enabled: bool,
        live_arm_max_ms: int = accounts.LIVE_ARM_DEFAULT_MS,
    ) -> None:
        """Initializes the service.

        Args:
            uow_factory: Unit of work factory.
            resolver: Adapter resolver.
            market_data: Market gateway.
            vault: Credential vault.
            audit_log: Audit log.
            clock: Time source.
            id_gen: Id generator.
            paper_starting_equity: Starting equity of paper accounts.
            paper_twd_starting_cash: Starting cash of the TWD paper
                account for TWSE and TPEx securities; ``None``
                disables it.
            step_up: Step-up verifier (shared with deployment consent).
            live_trading_enabled: Global LIVE gate.
            live_arm_max_ms: Longest (and default) LIVE arming, capped at
                24 hours.
        """
        self._uow = uow_factory
        self._resolver = resolver
        self._market = market_data
        self._vault = vault
        self._audit = audit_log
        self._clock = clock
        self._ids = id_gen
        self._paper_equity = paper_starting_equity
        self._paper_twd = paper_twd_starting_cash
        self._step_up = step_up
        self._live_enabled = live_trading_enabled
        self._arm_max_ms = min(live_arm_max_ms, accounts.LIVE_ARM_CEILING_MS)

    def ensure_paper_account(self, tenant_id: str) -> accounts.Account:
        """Returns (creating once) the tenant's paper account.

        Also provisions the TWD paper account (TWSE and TPEx securities)
        when one is configured.

        Args:
            tenant_id: Tenant.

        Returns:
            The USD paper account.
        """
        if self._paper_twd is not None:
            self._ensure_paper(tenant_id, "TWD", self._paper_twd)
        return self._ensure_paper(tenant_id, "USD", self._paper_equity)

    def _ensure_paper(
        self, tenant_id: str, currency: str, starting: decimal.Decimal
    ) -> accounts.Account:
        """Returns (creating once) one paper account of a tenant.

        Args:
            tenant_id: Tenant.
            currency: Account currency.
            starting: Starting cash/equity.

        Returns:
            Paper account.
        """
        account_id = accounts.paper_account_id(tenant_id, currency)
        with self._uow() as uow:
            existing = uow.accounts.get(account_id)
            if existing is not None:
                return existing
            account = accounts.Account(
                account_id=account_id,
                tenant_id=tenant_id,
                venue="mock",
                label="Paper trading"
                if currency == "USD"
                else f"Paper trading ({currency}, TWSE/TPEx)",
                status=enums.AccountStatus.CONNECTED,
                modes=(enums.OrderMode.PAPER, enums.OrderMode.SHADOW),
                currency=currency,
                created_at_ms=self._clock.now_ms(),
                starting_equity=starting,
                is_paper=True,
            )
            uow.accounts.add(account)
            self._audit.record(
                uow,
                tenant_id=tenant_id,
                actor="system",
                action="account.paper_provisioned",
                entity_type="account",
                entity_id=account_id,
                account_id=account_id,
                payload={"starting_equity": str(starting)},
            )
        return account

    def get(
        self, principal: identity.Principal, account_id: str
    ) -> accounts.Account:
        """Loads a tenant account.

        Args:
            principal: Caller.
            account_id: Account.

        Returns:
            Account.

        Raises:
            ExecutionError: ``NOT_FOUND``.
        """
        with self._uow() as uow:
            account = uow.accounts.get(account_id)
        if account is None or account.tenant_id != principal.tenant_id:
            raise errors.ExecutionError("NOT_FOUND", "account not found")
        return account

    async def list(
        self, principal: identity.Principal
    ) -> list[dict[str, object]]:
        """Lists account summaries.

        Args:
            principal: Caller.

        Returns:
            Contract ``AccountSummary`` objects.
        """
        self.ensure_paper_account(principal.tenant_id)
        with self._uow() as uow:
            items = uow.accounts.list_for_tenant(principal.tenant_id)
        return [await self._summary(a) for a in items]

    async def summary(
        self, principal: identity.Principal, account_id: str
    ) -> dict[str, object]:
        """Builds one account summary.

        Args:
            principal: Caller.
            account_id: Account.

        Returns:
            Contract ``AccountSummary``.
        """
        return await self._summary(self.get(principal, account_id))

    async def capabilities(
        self, account: accounts.Account
    ) -> accounts.TradingCapabilities:
        """Negotiates capabilities of the account's trading adapter.

        Args:
            account: Account.

        Returns:
            Capabilities.
        """
        mode = (
            enums.OrderMode.PAPER if account.is_paper else enums.OrderMode.LIVE
        )
        adapter = self._resolver.resolve(account, mode)
        return await adapter.capabilities(account.account_id)

    async def _summary(self, account: accounts.Account) -> dict[str, object]:
        """Builds a summary.

        Args:
            account: Account.

        Returns:
            Contract ``AccountSummary``.
        """
        now = self._clock.now_ms()
        caps = await self.capabilities(account)
        mode = (
            enums.OrderMode.PAPER if account.is_paper else enums.OrderMode.LIVE
        )
        adapter = self._resolver.resolve(account, mode)
        try:
            health = await adapter.health()
        except errors.ExecutionError:
            health = enums.HealthState.UNAVAILABLE
        val = await valuation.value_account(
            self._uow, self._market, account, mode, now, 15_000
        )
        snapshot = await self._cash_snapshot(account, adapter)
        equity = val.equity
        available = val.available_margin
        if snapshot is not None and not account.is_paper:
            equity, available = snapshot.equity, snapshot.available_margin
        with self._uow() as uow:
            open_orders = uow.orders.list(
                tenant_id=account.tenant_id,
                account_id=account.account_id,
                states=_OPEN,
            )
            all_positions = uow.positions.list(account_id=account.account_id)
            arming = uow.live_arming.get(account.tenant_id, account.account_id)
        armed_until = (
            arming.expires_at_ms
            if arming is not None and arming.valid_for(account, now)
            else None
        )
        position_rows = [
            p.to_contract(
                val.marks.get(p.instrument_id),
                now,
                val.marks.get(p.instrument_id) is None,
            )
            for p in all_positions
        ]
        return {
            "account_id": account.account_id,
            "tenant_id": account.tenant_id,
            "venue": account.venue,
            "label": account.label,
            "status": account.status.value,
            "modes": [m.value for m in account.modes],
            "equity": None if equity is None else str(equity),
            "available_margin": None if available is None else str(available),
            "settled_cash": None
            if snapshot is None or snapshot.settled_cash is None
            else str(snapshot.settled_cash),
            "unsettled_cash": None
            if snapshot is None or snapshot.unsettled_cash is None
            else str(snapshot.unsettled_cash),
            "currency": account.currency,
            "positions": position_rows,
            "open_orders": [o.to_contract() for o in open_orders],
            "capabilities": caps.to_contract(),
            "health": health.value,
            "stale": val.stale or not account.is_paper,
            "last_sync_ms": now if account.is_paper else None,
            "live_armed_until_ms": armed_until,
        }

    async def _cash_snapshot(
        self, account: accounts.Account, adapter: trading.TradingAdapter
    ) -> trading.VenueAccountSnapshot | None:
        """Reads balances of paper and broker accounts (else ``None``).

        Args:
            account: Account.
            adapter: Its adapter.

        Returns:
            Snapshot, or ``None`` for crypto venues and on failure.
        """
        if not account.is_paper and not equities.is_broker(account.venue):
            return None
        try:
            return await adapter.account_snapshot(account.account_id)
        except errors.ExecutionError:
            return None

    async def connect_start(
        self,
        principal: identity.Principal,
        venue: str,
        label: str,
        credentials: Mapping[str, str],
        withdrawal_disabled: bool,
    ) -> dict[str, object]:
        """Stores an encrypted venue credential and creates the account.

        Args:
            principal: Caller.
            venue: Live venue code.
            label: Display label.
            credentials: Credential fields (never echoed back).
            withdrawal_disabled: User attests withdrawals are disabled.

        Returns:
            Contract ``AccountSummary`` (PENDING_VERIFICATION).

        Raises:
            ExecutionError: ``VALIDATION_FAILED`` for unsafe credentials.
        """
        broker = equities.is_broker(venue)
        if venue not in LIVE_VENUES and not broker:
            raise errors.ExecutionError(
                "VALIDATION_FAILED", f"unsupported venue {venue!r}"
            )
        if not withdrawal_disabled:
            raise errors.ExecutionError(
                "VALIDATION_FAILED",
                "use a key without withdrawal permission and confirm it",
            )
        if not credentials:
            raise errors.ExecutionError(
                "VALIDATION_FAILED", "credentials are required"
            )
        for name in credentials:
            if any(marker in name.lower() for marker in _FORBIDDEN_FIELDS):
                raise errors.ExecutionError(
                    "VALIDATION_FAILED",
                    "never provide seed phrases or main wallet keys; use a "
                    "delegated agent/API key",
                )
        if broker:
            _validate_broker_credentials(venue, credentials)
        if venue in DEX_VENUES and not any(
            n in credentials for n in ("agent_private_key", "api_private_key")
        ):
            raise errors.ExecutionError(
                "VALIDATION_FAILED",
                f"{venue} needs a delegated agent/API key "
                "(agent_private_key or api_private_key)",
            )
        now = self._clock.now_ms()
        account_id = f"acct-{venue}-{self._ids.new_id()[-12:]}"
        credential_id = self._ids.new_id()
        envelope = self._vault.seal(dict(credentials))
        account = accounts.Account(
            account_id=account_id,
            tenant_id=principal.tenant_id,
            venue=venue,
            label=label[:120] or venue,
            status=enums.AccountStatus.PENDING_VERIFICATION,
            modes=(),
            currency=equities.BROKER_CURRENCY.get(venue, "USD"),
            created_at_ms=now,
            credential_id=credential_id,
            withdrawal_disabled=True,
        )
        with self._uow() as uow:
            uow.accounts.add(account)
            uow.credentials.add(
                credential_id, principal.tenant_id, account_id, envelope, now
            )
            self._audit.record(
                uow,
                tenant_id=principal.tenant_id,
                actor=principal.actor,
                action="credential.stored",
                entity_type="account",
                entity_id=account_id,
                account_id=account_id,
                payload={
                    "venue": venue,
                    "fields": sorted(credentials),
                    "withdrawal_disabled": True,
                },
            )
        return await self._summary(account)

    async def verify(
        self, principal: identity.Principal, account_id: str
    ) -> dict[str, object]:
        """Verifies a venue connection through its adapter.

        Args:
            principal: Caller.
            account_id: Account.

        Returns:
            Updated summary. The account gains LIVE mode only when its
            adapter negotiates AVAILABLE (credentials, healthy venue and a
            passing permission probe).
        """
        account = self.get(principal, account_id)
        if account.is_paper:
            return await self._summary(account)
        caps = await self.capabilities(account)
        status = (
            enums.AccountStatus.CONNECTED
            if caps.status is enums.TradingStatus.AVAILABLE
            else enums.AccountStatus.TRADING_UNAVAILABLE
        )
        modes: tuple[enums.OrderMode, ...] = (
            (enums.OrderMode.LIVE,)
            if status is enums.AccountStatus.CONNECTED
            else ()
        )
        updated = _replace(account, status, modes)
        with self._uow() as uow:
            uow.accounts.save(updated)
            self._audit.record(
                uow,
                tenant_id=principal.tenant_id,
                actor=principal.actor,
                action="account.verified",
                entity_type="account",
                entity_id=account_id,
                account_id=account_id,
                payload={"status": status.value, "trading": caps.status.value},
            )
            if enums.OrderMode.LIVE not in modes:
                self._disarm(uow, updated, "system", "verification")
        return await self._summary(updated)

    async def revoke(
        self, principal: identity.Principal, account_id: str
    ) -> dict[str, object]:
        """Crypto-shreds credentials and disconnects an account.

        Args:
            principal: Caller.
            account_id: Account.

        Returns:
            Updated summary.

        Raises:
            ExecutionError: ``CONFLICT`` for the paper account.
        """
        account = self.get(principal, account_id)
        if account.is_paper:
            raise errors.ExecutionError(
                "CONFLICT", "paper account cannot be revoked"
            )
        updated = _replace(account, enums.AccountStatus.DISCONNECTED, ())
        with self._uow() as uow:
            removed = uow.credentials.delete_for_account(account_id)
            uow.accounts.save(updated)
            self._audit.record(
                uow,
                tenant_id=principal.tenant_id,
                actor=principal.actor,
                action="credential.revoked",
                entity_type="account",
                entity_id=account_id,
                account_id=account_id,
                payload={"envelopes_deleted": removed},
            )
            self._disarm(uow, updated, principal.actor, "credentials_revoked")
        return await self._summary(updated)

    # ------------------------------------------------------------------
    # LIVE arming (manual orders)
    # ------------------------------------------------------------------

    async def grant_live_consent(
        self,
        principal: identity.Principal,
        account_id: str,
        step_up_code: str,
        acknowledge_risk: bool,
        duration_ms: int | None = None,
    ) -> dict[str, object]:
        """Arms manual LIVE orders on an account for a bounded time.

        Fails closed: every refusal is audited as ``live_arming.denied``.
        The step-up code is checked by the same verifier, in the same way,
        as AUTO_LIVE deployment consent.

        Args:
            principal: Caller.
            account_id: Verified venue or broker account.
            step_up_code: Step-up (MFA) code.
            acknowledge_risk: Explicit risk acknowledgement.
            duration_ms: Lifetime; ``None`` uses the configured maximum.

        Returns:
            Contract ``AccountSummary`` with ``live_armed_until_ms``.

        Raises:
            ExecutionError: ``NOT_FOUND``, ``TRADE_MODE_DISABLED`` (paper or
                unverified account), ``VALIDATION_FAILED``,
                ``STEP_UP_REQUIRED`` or ``LIVE_TRADING_DISABLED``.
        """
        account = self.get(principal, account_id)
        duration = self._arm_max_ms if duration_ms is None else duration_ms
        problem = await self._arming_problem(
            principal, account, step_up_code, acknowledge_risk, duration
        )
        if problem is not None:
            with self._uow() as uow:
                self._audit.record(
                    uow,
                    tenant_id=principal.tenant_id,
                    actor=principal.actor,
                    action="live_arming.denied",
                    entity_type="account",
                    entity_id=account_id,
                    account_id=account_id,
                    payload={"code": problem.code, "reason": problem.message},
                )
            raise problem
        now = self._clock.now_ms()
        arming = accounts.LiveArming.grant(
            account, principal.actor, now, duration
        )
        with self._uow() as uow:
            previous = uow.live_arming.get(principal.tenant_id, account_id)
            uow.live_arming.put(arming)
            self._audit.record(
                uow,
                tenant_id=principal.tenant_id,
                actor=principal.actor,
                action="live_arming.granted",
                entity_type="account",
                entity_id=account_id,
                account_id=account_id,
                payload={
                    **arming.to_contract(),
                    "duration_ms": duration,
                    "replaced_active": previous is not None
                    and previous.valid_for(account, now),
                },
            )
        return await self._summary(account)

    async def _arming_problem(  # noqa: PLR0911
        self,
        principal: identity.Principal,
        account: accounts.Account,
        step_up_code: str,
        acknowledge_risk: bool,
        duration_ms: int,
    ) -> errors.ExecutionError | None:
        """Finds the first reason an arming must be refused.

        Args:
            principal: Caller.
            account: Account to arm.
            step_up_code: Step-up code.
            acknowledge_risk: Risk acknowledgement.
            duration_ms: Requested lifetime.

        Returns:
            The refusal, or ``None`` when the arming may be granted.
        """
        if account.is_paper:
            return errors.ExecutionError(
                "TRADE_MODE_DISABLED",
                "paper accounts never trade LIVE; arm a verified venue or "
                "broker account",
            )
        if not acknowledge_risk:
            return errors.ExecutionError(
                "VALIDATION_FAILED", "risk acknowledgement is required"
            )
        if not 0 < duration_ms <= self._arm_max_ms:
            return errors.ExecutionError(
                "VALIDATION_FAILED",
                f"live arming lasts at most {_duration_text(self._arm_max_ms)}",
            )
        if not self._step_up.verify(principal.user_id, step_up_code):
            return errors.ExecutionError(
                "STEP_UP_REQUIRED", "step-up verification failed"
            )
        if not self._live_enabled:
            return errors.ExecutionError(
                "LIVE_TRADING_DISABLED",
                "live trading is disabled on this deployment",
            )
        if (
            account.status is not enums.AccountStatus.CONNECTED
            or enums.OrderMode.LIVE not in account.modes
        ):
            return errors.ExecutionError(
                "TRADE_MODE_DISABLED",
                f"verify the account first: it is {account.status.value} "
                "and LIVE is not enabled for it",
            )
        caps = await self.capabilities(account)
        if caps.status is not enums.TradingStatus.AVAILABLE:
            return errors.ExecutionError(
                "LIVE_TRADING_DISABLED",
                f"{account.venue} trading is {caps.status.value}",
            )
        return None

    async def revoke_live_consent(
        self, principal: identity.Principal, account_id: str
    ) -> dict[str, object]:
        """Disarms manual LIVE orders on an account (always allowed).

        Idempotent. Cancels, risk-reducing orders, the kill switch and
        reconciliation keep working while an account is disarmed.

        Args:
            principal: Caller.
            account_id: Account.

        Returns:
            Contract ``AccountSummary`` (``live_armed_until_ms`` null).
        """
        account = self.get(principal, account_id)
        with self._uow() as uow:
            self._disarm(uow, account, principal.actor, "user", always=True)
        return await self._summary(account)

    def _disarm(
        self,
        uow: repositories.UnitOfWork,
        account: accounts.Account,
        actor: str,
        reason: str,
        *,
        always: bool = False,
    ) -> None:
        """Revokes the account's arming inside a transaction and audits it.

        Args:
            uow: Active unit of work.
            account: Account.
            actor: Revoking actor.
            reason: ``user``, ``verification`` or ``credentials_revoked``.
            always: Audit even when no arming was active (user requests).
        """
        now = self._clock.now_ms()
        current = uow.live_arming.get(account.tenant_id, account.account_id)
        was_armed = current is not None and current.valid_for(account, now)
        if current is not None and current.revoked_at_ms is None:
            uow.live_arming.put(current.revoke(actor, now))
        if not (was_armed or always):
            return
        self._audit.record(
            uow,
            tenant_id=account.tenant_id,
            actor=actor,
            action="live_arming.revoked",
            entity_type="account",
            entity_id=account.account_id,
            account_id=account.account_id,
            payload={
                "reason": reason,
                "was_armed": was_armed,
                "expires_at_ms": None
                if current is None
                else current.expires_at_ms,
            },
        )


def _validate_broker_credentials(
    venue: str, credentials: Mapping[str, str]
) -> None:
    """Accepts only the broker account number for broker connections.

    Args:
        venue: Broker code.
        credentials: Submitted fields.

    Raises:
        ExecutionError: ``VALIDATION_FAILED`` for any other field or a
            malformed account number.
    """
    extra = sorted(set(credentials) - {BROKER_ACCOUNT_FIELD})
    if extra:
        raise errors.ExecutionError(
            "VALIDATION_FAILED",
            f"{venue} connections take only the broker account number "
            f"({BROKER_ACCOUNT_FIELD!r}); broker logins, API keys and "
            "certificates are deployment secrets (env or secret files)",
            details={"fields": extra},
        )
    number = credentials.get(BROKER_ACCOUNT_FIELD, "").strip()
    if not _BROKER_ACCOUNT.match(number):
        raise errors.ExecutionError(
            "VALIDATION_FAILED",
            f"{venue} needs the broker account number, e.g. DU1234567 "
            "(IBKR paper) or 9A95-0000000 (SinoPac)",
        )


def _duration_text(duration_ms: int) -> str:
    """Formats an arming duration for messages.

    Args:
        duration_ms: Duration.

    Returns:
        ``8 hours``, ``90 minutes`` or ``1500 ms``.
    """
    if duration_ms % 3_600_000 == 0:
        hours = duration_ms // 3_600_000
        return f"{hours} hour{'s' if hours != 1 else ''}"
    if duration_ms % 60_000 == 0:
        return f"{duration_ms // 60_000} minutes"
    return f"{duration_ms} ms"


def _replace(
    account: accounts.Account,
    status: enums.AccountStatus,
    modes: tuple[enums.OrderMode, ...],
) -> accounts.Account:
    """Returns a copy of an account with a new status and modes.

    Args:
        account: Account.
        status: New status.
        modes: New modes.

    Returns:
        Updated account.
    """
    return dataclasses.replace(account, status=status, modes=modes)
