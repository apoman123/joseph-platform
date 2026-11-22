"""Composition root: wires adapters to ports and builds the application.

This is the only module that imports adapters and the API together.
Run with ``uvicorn joseph_execution.bootstrap:create_application --factory
--port 8100``.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import logging

import fastapi

from joseph_execution.accounts import service as account_service
from joseph_execution.adapters import market_http
from joseph_execution.adapters import market_static
from joseph_execution.adapters import paper
from joseph_execution.adapters import resolver
from joseph_execution.adapters import shadow
from joseph_execution.adapters import step_up
from joseph_execution.adapters import vault
from joseph_execution.adapters.persistence import sql
from joseph_execution.adapters.venues import catalog
from joseph_execution.api import app as api_app
from joseph_execution.api import services as api_services
from joseph_execution.audit import service as audit_service
from joseph_execution.oms import deployments
from joseph_execution.oms import kill_switch
from joseph_execution.oms import preview
from joseph_execution.oms import service as oms_service
from joseph_execution.oms import signal_intake
from joseph_execution.platform import clock as clock_lib
from joseph_execution.platform import config
from joseph_execution.platform import logging as log_lib
from joseph_execution.platform import metrics as metrics_lib
from joseph_execution.platform import streams
from joseph_execution.ports import market as market_port
from joseph_execution.ports import services as service_ports
from joseph_execution.reconciliation import service as reconcile_service
from joseph_execution.risk import gateway as risk_gateway

_LOG = logging.getLogger(__name__)


@dataclasses.dataclass(slots=True)
class Container:
    """Every wired component (tests reach adapters through it).

    Attributes:
        settings: Settings.
        clock: Clock.
        market: Market gateway.
        paper: Paper simulator.
        shadow: Shadow recorder.
        registry: Adapter registry.
        services: API services.
        uow_factory: Unit of work factory.
    """

    settings: config.Settings
    clock: service_ports.Clock
    market: market_port.MarketDataGateway
    paper: paper.PaperTradingAdapter
    shadow: shadow.ShadowAdapter
    registry: resolver.AdapterRegistry
    services: api_services.ApiServices
    uow_factory: sql.SqlUnitOfWorkFactory


def build_container(
    settings: config.Settings,
    clock: service_ports.Clock | None = None,
    market: market_port.MarketDataGateway | None = None,
) -> Container:
    """Wires the execution engine.

    Args:
        settings: Settings.
        clock: Optional clock override (tests).
        market: Optional market gateway override (tests).

    Returns:
        Container.
    """
    log_lib.install()
    the_clock = clock or clock_lib.SystemClock()
    id_gen = clock_lib.Uuid7Generator()
    engine = sql.create_engine(settings.database_url)
    uow_factory = sql.SqlUnitOfWorkFactory(engine)
    metrics = metrics_lib.MetricsRegistry()
    hub = streams.UserStreamHub(the_clock)
    if market is not None:
        gateway = market
    elif settings.market_gateway == "http":
        gateway = market_http.HttpMarketDataGateway(settings.market_url)
    else:
        gateway = market_static.StaticMarketDataGateway(the_clock)
    paper_adapter = paper.PaperTradingAdapter(
        gateway, the_clock, starting_equity=settings.paper_starting_equity
    )
    shadow_adapter = shadow.ShadowAdapter(gateway, the_clock)
    registry = resolver.AdapterRegistry(
        paper_adapter, shadow_adapter, catalog.build_live_adapters()
    )
    audit_log = audit_service.AuditLog(the_clock, id_gen)
    oms = oms_service.OmsService(
        uow_factory,
        risk_gateway.RiskGateway(),
        registry,
        gateway,
        audit_log,
        the_clock,
        id_gen,
        hub,
        metrics,
        preview.PreviewSigner(settings.preview_secret),
        oms_service.OmsSettings(
            live_trading_enabled=settings.live_trading_enabled,
            emergency_halt=settings.emergency_halt,
        ),
    )
    svc = api_services.ApiServices(
        settings=settings,
        clock=the_clock,
        uow_factory=uow_factory,
        oms=oms,
        accounts=account_service.AccountService(
            uow_factory,
            registry,
            gateway,
            vault.AesGcmVault(
                settings.vault_master_key, on_secret=log_lib.SECRETS.add
            ),
            audit_log,
            the_clock,
            id_gen,
            settings.paper_starting_equity,
        ),
        kill_switches=kill_switch.KillSwitchService(
            uow_factory, oms, audit_log, the_clock, hub
        ),
        deployments=deployments.DeploymentService(
            uow_factory,
            registry,
            audit_log,
            the_clock,
            id_gen,
            step_up.TotpStepUpVerifier(settings.step_up_totp_secret, the_clock),
            settings.live_trading_enabled,
        ),
        intake=signal_intake.SignalIntakeService(
            uow_factory, oms, gateway, audit_log, the_clock
        ),
        reconciler=reconcile_service.Reconciler(
            uow_factory, registry, oms, audit_log, the_clock, metrics
        ),
        audit=audit_log,
        hub=hub,
        metrics=metrics,
        resolver=registry,
    )
    return Container(
        settings,
        the_clock,
        gateway,
        paper_adapter,
        shadow_adapter,
        registry,
        svc,
        uow_factory,
    )


async def pump_simulators(container: Container) -> int:
    """Runs one simulator tick and applies resulting events.

    Args:
        container: Container.

    Returns:
        Events applied.
    """
    applied = 0
    for adapter in (container.paper, container.shadow):
        await adapter.tick()
        for account_id in adapter.account_ids():
            events = adapter.drain_events(account_id)
            if events:
                applied += await container.services.oms.apply_venue_events(
                    account_id, events
                )
    return applied


def background_jobs(container: Container) -> list[api_app.BackgroundTask]:
    """Builds the long-running loops.

    Args:
        container: Container.

    Returns:
        Coroutine factories.
    """
    settings = container.settings

    async def ticker() -> None:
        """Animates DEMO marks and matches simulated orders."""
        while True:
            with contextlib.suppress(Exception):
                if isinstance(
                    container.market, market_static.StaticMarketDataGateway
                ):
                    container.market.walk(settings.demo_walk_bps)
                await pump_simulators(container)
            await asyncio.sleep(settings.tick_interval_ms / 1000)

    async def outbox() -> None:
        """Processes the outbox."""
        while True:
            with contextlib.suppress(Exception):
                await container.services.oms.dispatch_pending()
            await asyncio.sleep(0.5)

    async def reconcile() -> None:
        """Runs reconciliation passes."""
        while True:
            await asyncio.sleep(settings.reconcile_interval_ms / 1000)
            try:
                await container.services.reconciler.run_once()
            except Exception:
                _LOG.exception("reconciliation pass failed")

    return [ticker, outbox, reconcile]


def build_app(container: Container) -> fastapi.FastAPI:
    """Builds the application for a container.

    Args:
        container: Container.

    Returns:
        Application.
    """
    jobs = (
        background_jobs(container)
        if container.settings.background_tasks
        else []
    )
    return api_app.create_app(container.services, jobs)


def create_application() -> fastapi.FastAPI:
    """Uvicorn factory reading settings from the environment.

    Returns:
        Application.
    """
    settings = config.from_env()
    _LOG.info("starting execution engine: %r", settings)
    return build_app(build_container(settings))
