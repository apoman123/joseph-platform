# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Deployment service: creation, disabling and AUTO_LIVE consent.

A deployment binds one immutable strategy version to one account in one
mode. AUTO_LIVE deployments send LIVE orders only while they carry a valid
consent, which this service grants after its LIVE gates pass.
"""

from __future__ import annotations

import dataclasses

from joseph_execution.audit import service as audit_service
from joseph_execution.domain import deployments
from joseph_execution.domain import enums
from joseph_execution.domain import errors
from joseph_execution.domain import identity
from joseph_execution.ports import repositories
from joseph_execution.ports import services
from joseph_execution.ports import trading

# Longest AUTO_LIVE consent a user can grant at once: 7 days.
_MAX_CONSENT_MS = 7 * 86_400_000


class DeploymentService:
    """Service that creates, disables and grants consent for deployments."""

    def __init__(  # noqa: PLR0917 - seven injected collaborators and flags; bootstrap passes them positionally.
        self,
        uow_factory: repositories.UnitOfWorkFactory,
        resolver: trading.AdapterResolver,
        audit_log: audit_service.AuditLog,
        clock: services.Clock,
        id_gen: services.IdGenerator,
        step_up: services.StepUpVerifier,
        live_trading_enabled: bool,
    ) -> None:
        """Initializes the service.

        Args:
            uow_factory: Unit of work factory.
            resolver: Adapter resolver (for live capability checks).
            audit_log: Audit log.
            clock: Time source.
            id_gen: Id generator.
            step_up: Verifier of step-up (multi-factor authentication, MFA)
                codes.
            live_trading_enabled: Global LIVE gate; consent is refused while
                it is off.
        """
        self._uow = uow_factory
        self._resolver = resolver
        self._audit = audit_log
        self._clock = clock
        self._ids = id_gen
        self._step_up = step_up
        self._live_enabled = live_trading_enabled

    def list(
        self, principal: identity.Principal
    ) -> list[deployments.Deployment]:
        """Lists the caller's tenant deployments.

        Args:
            principal: Caller.

        Returns:
            Deployments of the caller's tenant.
        """
        with self._uow() as uow:
            return uow.deployments.list(principal.tenant_id)

    def create(  # noqa: PLR0917 - public API; the deployments router passes the deployment fields positionally.
        self,
        principal: identity.Principal,
        strategy_version_id: str,
        account_id: str,
        mode: enums.DeploymentMode,
        limits: deployments.DeploymentLimits,
        sizing: deployments.SizingModel,
    ) -> deployments.Deployment:
        """Creates a deployment and audits it.

        PAPER/SHADOW deployments are enabled immediately and need the
        account to permit their order mode; AUTO_LIVE starts in
        PENDING_CONSENT (its LIVE gates are checked when consent is
        granted) and never inherits consent from another version.

        Args:
            principal: Caller.
            strategy_version_id: Immutable version id ``<id>:v<n>``.
            account_id: Account of the caller's tenant.
            mode: Deployment mode.
            limits: Deployment limits.
            sizing: Sizing model.

        Returns:
            The new deployment.

        Raises:
            ExecutionError: ``VALIDATION_FAILED`` for a mutable strategy id,
                ``NOT_FOUND`` for a missing or foreign account, or
                ``TRADE_MODE_DISABLED`` when the account does not permit
                the mode.
        """
        if ":v" not in strategy_version_id:
            raise errors.ExecutionError(
                "VALIDATION_FAILED",
                "strategy_version_id must be an immutable version id",
            )
        now = self._clock.now_ms()
        with self._uow() as uow:
            account = uow.accounts.get(account_id)
            if account is None or account.tenant_id != principal.tenant_id:
                raise errors.ExecutionError("NOT_FOUND", "account not found")
            if mode is not enums.DeploymentMode.AUTO_LIVE and (
                mode.order_mode not in account.modes
            ):
                raise errors.ExecutionError(
                    "TRADE_MODE_DISABLED",
                    f"account does not permit {mode.value}",
                )
            deployment = deployments.Deployment(
                deployment_id=self._ids.new_id(),
                tenant_id=principal.tenant_id,
                strategy_version_id=strategy_version_id,
                account_id=account_id,
                mode=mode,
                state=enums.DeploymentState.PENDING_CONSENT
                if mode is enums.DeploymentMode.AUTO_LIVE
                else enums.DeploymentState.ENABLED,
                limits=limits,
                sizing=sizing,
                created_at_ms=now,
                updated_at_ms=now,
            )
            uow.deployments.add(deployment)
            self._audit.record(
                uow,
                tenant_id=principal.tenant_id,
                actor=principal.actor,
                action="deployment.created",
                entity_type="deployment",
                entity_id=deployment.deployment_id,
                account_id=account_id,
                payload=deployment.to_contract(),
            )
        return deployment

    def _owned(
        self,
        uow: repositories.UnitOfWork,
        principal: identity.Principal,
        deployment_id: str,
    ) -> deployments.Deployment:
        """Loads a deployment owned by the caller's tenant.

        Args:
            uow: Unit of work.
            principal: Caller.
            deployment_id: Deployment id.

        Returns:
            The deployment.

        Raises:
            ExecutionError: ``NOT_FOUND`` for a missing or foreign
                deployment.
        """
        deployment = uow.deployments.get(deployment_id)
        if deployment is None or deployment.tenant_id != principal.tenant_id:
            raise errors.ExecutionError("NOT_FOUND", "deployment not found")
        return deployment

    def disable(
        self, principal: identity.Principal, deployment_id: str
    ) -> deployments.Deployment:
        """Disables a deployment, drops any live consent and audits it.

        Args:
            principal: Caller.
            deployment_id: Deployment id.

        Returns:
            Updated deployment.

        Raises:
            ExecutionError: ``NOT_FOUND`` for a missing or foreign
                deployment.
        """
        now = self._clock.now_ms()
        with self._uow() as uow:
            current = self._owned(uow, principal, deployment_id)
            updated = dataclasses.replace(
                current,
                state=enums.DeploymentState.DISABLED,
                live_consent=None,
                updated_at_ms=now,
            )
            uow.deployments.save(updated)
            self._audit.record(
                uow,
                tenant_id=principal.tenant_id,
                actor=principal.actor,
                action="deployment.disabled",
                entity_type="deployment",
                entity_id=deployment_id,
                account_id=updated.account_id,
                payload={"previous_state": current.state.value},
            )
        return updated

    async def grant_live_consent(  # noqa: PLR0912 - the LIVE consent gates stay in one ordered if/elif chain.
        self,
        principal: identity.Principal,
        deployment_id: str,
        step_up_code: str,
        acknowledge_risk: bool,
        duration_ms: int,
    ) -> deployments.Deployment:
        """Grants scoped, expiring AUTO_LIVE consent (fails closed).

        The gates run in this order and the first failure wins: the
        deployment is AUTO_LIVE, is not DISABLED or REVOKED, the risk is
        acknowledged, ``0 < duration_ms <= 7 days``, the limits carry an
        instrument allow-list, the step-up code verifies, the global LIVE
        gate is on, the account permits LIVE, and the venue adapter reports
        trading AVAILABLE with native stop-loss orders. Every refusal is
        audited as ``live_consent.denied`` before it is raised. The consent
        is bound to a hash of account, venue, strategy version, limits and
        expiry, so changing any of them voids it.

        Args:
            principal: Caller.
            deployment_id: AUTO_LIVE deployment.
            step_up_code: Step-up (MFA) code.
            acknowledge_risk: Explicit risk acknowledgement.
            duration_ms: Consent lifetime in milliseconds (max 7 days).

        Returns:
            The deployment, ENABLED with the new consent.

        Raises:
            ExecutionError: ``NOT_FOUND``, ``VALIDATION_FAILED``,
                ``CONFLICT`` (disabled), ``STEP_UP_REQUIRED``,
                ``LIVE_TRADING_DISABLED`` or ``UNSUPPORTED`` (no native
                stop-loss).
        """
        now = self._clock.now_ms()
        with self._uow() as uow:
            current = self._owned(uow, principal, deployment_id)
            account = uow.accounts.get(current.account_id)
        if account is None:
            raise AssertionError("a deployment always has an account")
        problem: errors.ExecutionError | None = None
        if current.mode is not enums.DeploymentMode.AUTO_LIVE:
            problem = errors.ExecutionError(
                "VALIDATION_FAILED", "consent applies to AUTO_LIVE only"
            )
        elif current.state in (
            enums.DeploymentState.DISABLED,
            enums.DeploymentState.REVOKED,
        ):
            problem = errors.ExecutionError(
                "CONFLICT", "deployment is disabled"
            )
        elif not acknowledge_risk:
            problem = errors.ExecutionError(
                "VALIDATION_FAILED", "risk acknowledgement is required"
            )
        elif not (0 < duration_ms <= _MAX_CONSENT_MS):
            problem = errors.ExecutionError(
                "VALIDATION_FAILED", "consent lasts at most 7 days"
            )
        elif not current.limits.allowed_instruments:
            problem = errors.ExecutionError(
                "VALIDATION_FAILED", "AUTO_LIVE needs an instrument allow-list"
            )
        elif not self._step_up.verify(principal.user_id, step_up_code):
            problem = errors.ExecutionError(
                "STEP_UP_REQUIRED", "step-up verification failed"
            )
        elif not self._live_enabled:
            problem = errors.ExecutionError(
                "LIVE_TRADING_DISABLED",
                "live trading is disabled on this deployment",
            )
        elif enums.OrderMode.LIVE not in account.modes:
            problem = errors.ExecutionError(
                "LIVE_TRADING_DISABLED", "account is not enabled for LIVE"
            )
        else:
            adapter = self._resolver.resolve(account, enums.OrderMode.LIVE)
            caps = await adapter.capabilities(account.account_id)
            if caps.status is not enums.TradingStatus.AVAILABLE:
                problem = errors.ExecutionError(
                    "LIVE_TRADING_DISABLED",
                    f"{account.venue} trading is {caps.status.value}",
                )
            elif not caps.native_stop_loss:
                problem = errors.ExecutionError(
                    "UNSUPPORTED", "venue lacks native stop-loss"
                )
        if problem is not None:
            with self._uow() as uow:
                self._audit.record(
                    uow,
                    tenant_id=principal.tenant_id,
                    actor=principal.actor,
                    action="live_consent.denied",
                    entity_type="deployment",
                    entity_id=deployment_id,
                    account_id=account.account_id,
                    payload={"code": problem.code, "reason": problem.message},
                )
            raise problem
        expires = now + duration_ms
        consent = deployments.LiveConsent(
            consented_at_ms=now,
            expires_at_ms=expires,
            scope_hash=deployments.consent_scope_hash(
                current.account_id,
                account.venue,
                current.strategy_version_id,
                current.limits,
                expires,
            ),
            actor=principal.actor,
        )
        updated = dataclasses.replace(
            current,
            state=enums.DeploymentState.ENABLED,
            live_consent=consent,
            updated_at_ms=now,
        )
        with self._uow() as uow:
            uow.deployments.save(updated)
            self._audit.record(
                uow,
                tenant_id=principal.tenant_id,
                actor=principal.actor,
                action="live_consent.granted",
                entity_type="deployment",
                entity_id=deployment_id,
                account_id=account.account_id,
                payload=consent.to_contract(),
            )
        return updated
