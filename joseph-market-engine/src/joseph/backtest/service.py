"""Backtest application service (tenant scoped)."""

from __future__ import annotations

from joseph.backtest import runner as runner_module
from joseph.domain import identity
from joseph.strategy.app import version_service


class BacktestService:
    """Loads the caller's version and runs a backtest.

    Attributes:
        versions: Strategy version service.
        runner: Backtest runner.
    """

    def __init__(
        self,
        versions: version_service.StrategyVersionService,
        runner: runner_module.BacktestRunner,
    ) -> None:
        """Initializes the service.

        Args:
            versions: Strategy version service.
            runner: Backtest runner.
        """
        self.versions = versions
        self.runner = runner

    async def run(
        self,
        principal: identity.Principal,
        strategy_id: str,
        request: runner_module.BacktestRequest,
    ) -> runner_module.BacktestReport:
        """Runs a backtest of one of the principal's versions.

        Args:
            principal: Caller.
            strategy_id: Strategy slug.
            request: Parameters.

        Returns:
            The report.

        Raises:
            errors.NotFoundError: If the version is not the caller's.
        """
        version = self.versions.get_version(
            principal, strategy_id, request.version
        )
        return await self.runner.run(version, request)
