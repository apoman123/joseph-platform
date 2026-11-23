"""Strategy authoring: immutable versions, validation and activation.

Every saved change creates a new immutable version; activation never
carries over to it. The market engine can activate ALERT_ONLY, PAPER or
SHADOW only. AUTO_LIVE is refused here: it exists only as an
execution-engine deployment with explicit scoped consent.
"""

from __future__ import annotations

from collections.abc import Callable
from collections.abc import Collection
from collections.abc import Sequence

from joseph.domain import clock as clock_port
from joseph.domain import errors
from joseph.domain import identity
from joseph.strategy.app import ports
from joseph.strategy.domain import ast
from joseph.strategy.domain import compiler
from joseph.strategy.domain import errors as strategy_errors
from joseph.strategy.domain import evaluator
from joseph.strategy.domain import versions

ACTIVATABLE_MODES = frozenset({"ALERT_ONLY", "PAPER", "SHADOW"})
"""Modes the market engine may activate."""

WRITE_SCOPE = "strategy:write"
"""Scope required for writes when the principal carries scopes."""

_ACTIVATABLE_STATES = frozenset(
    {
        versions.StrategyState.VALIDATED,
        versions.StrategyState.PAUSED,
        versions.StrategyState.ACTIVE,
    }
)


class StrategyVersionService:
    """Creates, validates, activates and pauses strategy versions.

    Attributes:
        repository: Version store.
        clock: Time source.
    """

    def __init__(
        self,
        repository: ports.StrategyRepository,
        clock: clock_port.Clock,
        *,
        available_plugins: Collection[str] | None = None,
        unsupported_ops: Collection[str] = (),
        on_change: Callable[[], None] | None = None,
    ) -> None:
        """Initializes the service.

        Args:
            repository: Version store.
            clock: Time source.
            available_plugins: Registered indicator plugins (defaults to the
                seven first-party plugins).
            unsupported_ops: Rule operators removed from this build.
            on_change: Called after activation changes (runtime refresh).
        """
        self.repository = repository
        self.clock = clock
        self.available_plugins = (
            None if available_plugins is None else frozenset(available_plugins)
        )
        self.unsupported_ops = frozenset(unsupported_ops)
        self._on_change = on_change

    def compile(
        self, definition: ast.StrategyDefinition
    ) -> compiler.CompileResult:
        """Compiles a definition against the current plugin catalog.

        Args:
            definition: Definition.

        Returns:
            Compile result.
        """
        return compiler.compile_strategy(
            definition,
            available_plugins=self.available_plugins,
            unsupported_ops=self.unsupported_ops,
        )

    def validate(
        self, definition: ast.StrategyDefinition
    ) -> compiler.ValidationResult:
        """Validates a definition without storing it.

        Args:
            definition: Definition.

        Returns:
            Validation result.
        """
        return self.compile(definition).validation

    @staticmethod
    def _require_write(principal: identity.Principal) -> None:
        """Checks write permission.

        Args:
            principal: Caller.

        Raises:
            errors.ForbiddenError: If scopes are present but lack writes.
        """
        if principal.scopes and not principal.has_scope(WRITE_SCOPE):
            raise errors.ForbiddenError("strategy:write scope required")

    def _store(
        self,
        principal: identity.Principal,
        definition: ast.StrategyDefinition,
        version: int,
    ) -> versions.StrategyVersion:
        """Validates and stores a new version.

        Args:
            principal: Caller.
            definition: Definition.
            version: Version number to create.

        Returns:
            Stored version (DRAFT when semantically invalid).
        """
        result = self.compile(definition)
        stored = versions.StrategyVersion(
            strategy_id=definition.strategy_id,
            version=version,
            tenant_id=principal.tenant_id,
            author_user_id=principal.user_id,
            created_at_ms=self.clock.now_ms(),
            definition=definition,
            definition_hash=definition.definition_hash(),
            compiler_version=compiler.COMPILER_VERSION,
            evaluator_version=evaluator.EVALUATOR_VERSION,
            state=(
                versions.StrategyState.VALIDATED
                if result.validation.valid
                else versions.StrategyState.DRAFT
            ),
        )
        self.repository.add(stored)
        return stored

    def create(
        self, principal: identity.Principal, definition: ast.StrategyDefinition
    ) -> versions.StrategyVersion:
        """Creates a new strategy as version 1.

        Args:
            principal: Caller.
            definition: Definition.

        Returns:
            Version 1.

        Raises:
            errors.ConflictError: If the strategy id already exists.
            errors.ForbiddenError: If writes are not permitted.
        """
        self._require_write(principal)
        if self.repository.list_versions(
            principal.tenant_id, definition.strategy_id
        ):
            raise errors.ConflictError(
                f"strategy {definition.strategy_id} already exists; create "
                "a new version instead"
            )
        return self._store(principal, definition, 1)

    def create_version(
        self,
        principal: identity.Principal,
        strategy_id: str,
        definition: ast.StrategyDefinition,
    ) -> versions.StrategyVersion:
        """Creates a new immutable version.

        Submitting a definition identical to the latest version returns the
        latest version instead of creating a duplicate.

        Args:
            principal: Caller.
            strategy_id: Strategy slug from the path.
            definition: Edited definition.

        Returns:
            The new (or identical latest) version.

        Raises:
            errors.ValidationFailedError: If ids disagree.
            errors.NotFoundError: If the strategy does not exist.
        """
        self._require_write(principal)
        if definition.strategy_id != strategy_id:
            raise errors.ValidationFailedError(
                "definition.strategy_id must match the strategy in the path"
            )
        existing = self.get_versions(principal, strategy_id)
        latest = existing[0]
        if latest.definition_hash == definition.definition_hash():
            return latest
        return self._store(principal, definition, latest.version + 1)

    def list_latest(
        self, principal: identity.Principal
    ) -> Sequence[versions.StrategyVersion]:
        """Lists the latest version of each strategy.

        Args:
            principal: Caller.

        Returns:
            Versions.
        """
        return self.repository.list_latest(principal.tenant_id)

    def get_versions(
        self, principal: identity.Principal, strategy_id: str
    ) -> Sequence[versions.StrategyVersion]:
        """Lists every version of a strategy, newest first.

        Args:
            principal: Caller.
            strategy_id: Strategy slug.

        Returns:
            Versions.

        Raises:
            errors.NotFoundError: If absent for this tenant.
        """
        found = self.repository.list_versions(principal.tenant_id, strategy_id)
        if not found:
            raise errors.NotFoundError(f"strategy {strategy_id} not found")
        return found

    def get_version(
        self,
        principal: identity.Principal,
        strategy_id: str,
        version: int | None = None,
    ) -> versions.StrategyVersion:
        """Fetches one version (latest when ``version`` is None).

        Args:
            principal: Caller.
            strategy_id: Strategy slug.
            version: Version number.

        Returns:
            The version.

        Raises:
            errors.NotFoundError: If absent for this tenant.
        """
        if version is None:
            return self.get_versions(principal, strategy_id)[0]
        found = self.repository.get(principal.tenant_id, strategy_id, version)
        if found is None:
            raise errors.NotFoundError(
                f"strategy {strategy_id} version {version} not found"
            )
        return found

    def revalidate(
        self,
        principal: identity.Principal,
        strategy_id: str,
        version: int | None = None,
    ) -> compiler.ValidationResult:
        """Re-validates a stored version against the current catalog.

        A previously valid version that now fails (for example because an
        indicator plugin or rule operator was removed) becomes UNSUPPORTED
        and is deactivated; its conditions are never silently dropped.

        Args:
            principal: Caller.
            strategy_id: Strategy slug.
            version: Version number (latest when None).

        Returns:
            Validation result.
        """
        stored = self.get_version(principal, strategy_id, version)
        result = self.compile(stored.definition).validation
        if not result.valid and stored.state in _ACTIVATABLE_STATES:
            self.repository.update_lifecycle(
                stored.tenant_id,
                stored.strategy_id,
                stored.version,
                versions.StrategyState.UNSUPPORTED,
                None,
            )
            self._changed()
        elif result.valid and stored.state in (
            versions.StrategyState.DRAFT,
            versions.StrategyState.UNSUPPORTED,
        ):
            self.repository.update_lifecycle(
                stored.tenant_id,
                stored.strategy_id,
                stored.version,
                versions.StrategyState.VALIDATED,
                None,
            )
        return result

    def activate(
        self,
        principal: identity.Principal,
        strategy_id: str,
        version: int,
        mode: str,
        instruments: Sequence[str] = (),
    ) -> versions.StrategyVersion:
        """Activates a version for signal generation.

        Args:
            principal: Caller.
            strategy_id: Strategy slug.
            version: Version to activate.
            mode: ``ALERT_ONLY``, ``PAPER`` or ``SHADOW``.
            instruments: Optional explicit instrument list.

        Returns:
            The activated version.

        Raises:
            strategy_errors.TradeModeDisabledError: For AUTO_LIVE.
            errors.ValidationFailedError: For unknown modes or instruments.
            strategy_errors.StrategyValidationError: If the version is
                invalid against the current catalog.
        """
        self._require_write(principal)
        if mode == "AUTO_LIVE":
            raise strategy_errors.TradeModeDisabledError(
                "AUTO_LIVE cannot be activated from the strategy service. "
                "Create an execution-engine deployment for one account with "
                "limits, then give explicit scoped consent; live trading is "
                "disabled by default."
            )
        if mode not in ACTIVATABLE_MODES:
            raise errors.ValidationFailedError(f"unsupported mode {mode}")
        stored = self.get_version(principal, strategy_id, version)
        result = self.compile(stored.definition).validation
        if not result.valid:
            if stored.state in _ACTIVATABLE_STATES:
                self.repository.update_lifecycle(
                    stored.tenant_id,
                    stored.strategy_id,
                    stored.version,
                    versions.StrategyState.UNSUPPORTED,
                    None,
                )
            raise strategy_errors.StrategyValidationError(
                "version is not valid against the current catalog",
                [issue.to_contract() for issue in result.errors],
            )
        venues = {venue.value for venue in stored.definition.universe.venues}
        for instrument in instruments:
            if instrument.split(":", 1)[0] not in venues:
                raise errors.ValidationFailedError(
                    f"{instrument} is outside the strategy universe"
                )
            reason = compiler.untradable_reason(instrument)
            if reason is not None:
                raise errors.ValidationFailedError(reason)
        for other in self.repository.list_versions(
            principal.tenant_id, strategy_id
        ):
            if (
                other.version != version
                and other.state is versions.StrategyState.ACTIVE
            ):
                self.repository.update_lifecycle(
                    other.tenant_id,
                    other.strategy_id,
                    other.version,
                    versions.StrategyState.VALIDATED,
                    None,
                )
        activated = self.repository.update_lifecycle(
            stored.tenant_id,
            stored.strategy_id,
            stored.version,
            versions.StrategyState.ACTIVE,
            versions.Activation(
                mode=mode,
                activated_at_ms=self.clock.now_ms(),
                instruments=tuple(instruments),
            ),
        )
        self._changed()
        return activated

    def pause(
        self, principal: identity.Principal, strategy_id: str
    ) -> versions.StrategyVersion:
        """Pauses the active version of a strategy.

        Args:
            principal: Caller.
            strategy_id: Strategy slug.

        Returns:
            The paused version.

        Raises:
            errors.ConflictError: If no version is active.
        """
        self._require_write(principal)
        for stored in self.get_versions(principal, strategy_id):
            if stored.state is versions.StrategyState.ACTIVE:
                paused = self.repository.update_lifecycle(
                    stored.tenant_id,
                    stored.strategy_id,
                    stored.version,
                    versions.StrategyState.PAUSED,
                    stored.activation,
                )
                self._changed()
                return paused
        raise errors.ConflictError(f"strategy {strategy_id} is not active")

    def _changed(self) -> None:
        """Notifies the runtime that activations changed."""
        if self._on_change is not None:
            self._on_change()
