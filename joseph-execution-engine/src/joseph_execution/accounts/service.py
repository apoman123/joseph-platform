"""Account service: paper provisioning, venue connections, summaries.

Credentials are sealed by the vault before persistence and are never
returned, logged or audited. Withdrawal-capable keys are refused, and DEX
connections accept only delegated agent/API keys, never seed phrases.
"""

from __future__ import annotations

import dataclasses
import decimal
from collections.abc import Mapping

from joseph_execution.audit import service as audit_service
from joseph_execution.domain import accounts
from joseph_execution.domain import enums
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
        """
        self._uow = uow_factory
        self._resolver = resolver
        self._market = market_data
        self._vault = vault
        self._audit = audit_log
        self._clock = clock
        self._ids = id_gen
        self._paper_equity = paper_starting_equity

    def ensure_paper_account(self, tenant_id: str) -> accounts.Account:
        """Returns (creating once) the tenant's paper account.

        Args:
            tenant_id: Tenant.

        Returns:
            Paper account.
        """
        account_id = accounts.paper_account_id(tenant_id)
        with self._uow() as uow:
            existing = uow.accounts.get(account_id)
            if existing is not None:
                return existing
            account = accounts.Account(
                account_id=account_id,
                tenant_id=tenant_id,
                venue="mock",
                label="Paper trading",
                status=enums.AccountStatus.CONNECTED,
                modes=(enums.OrderMode.PAPER, enums.OrderMode.SHADOW),
                currency="USD",
                created_at_ms=self._clock.now_ms(),
                starting_equity=self._paper_equity,
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
                payload={"starting_equity": str(self._paper_equity)},
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
        with self._uow() as uow:
            open_orders = uow.orders.list(
                tenant_id=account.tenant_id,
                account_id=account.account_id,
                states=_OPEN,
            )
            all_positions = uow.positions.list(account_id=account.account_id)
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
            "equity": None if val.equity is None else str(val.equity),
            "available_margin": None
            if val.available_margin is None
            else str(val.available_margin),
            "currency": account.currency,
            "positions": position_rows,
            "open_orders": [o.to_contract() for o in open_orders],
            "capabilities": caps.to_contract(),
            "health": health.value,
            "stale": val.stale or not account.is_paper,
            "last_sync_ms": now if account.is_paper else None,
        }

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
        if venue not in LIVE_VENUES:
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
            currency="USD",
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
        return await self._summary(updated)


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
