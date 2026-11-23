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
import time
from collections.abc import Callable

import fastapi

from joseph_execution.accounts import service as account_service
from joseph_execution.adapters import market_http
from joseph_execution.adapters import market_static
from joseph_execution.adapters import paper
from joseph_execution.adapters import resolver
from joseph_execution.adapters import shadow
from joseph_execution.adapters import step_up
from joseph_execution.adapters import vault
from joseph_execution.adapters.brokers import base as broker_base
from joseph_execution.adapters.brokers import catalog as broker_catalog
from joseph_execution.adapters.brokers import ibkr
from joseph_execution.adapters.brokers import sinopac
from joseph_execution.adapters.persistence import sql
from joseph_execution.adapters.venues import catalog
from joseph_execution.adapters.venues import credentials as venue_credentials
from joseph_execution.adapters.venues import directory as venue_directory
from joseph_execution.adapters.venues import live as venue_live
from joseph_execution.api import app as api_app
from joseph_execution.api import services as api_services
from joseph_execution.audit import service as audit_service
from joseph_execution.domain import enums
from joseph_execution.domain import errors
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
from joseph_execution.ports import trading as trading_port
from joseph_execution.reconciliation import live_streams
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
        brokers: Securities broker adapters by broker code.
        live_streams: Private stream supervisor of crypto venue accounts.
    """

    settings: config.Settings
    clock: service_ports.Clock
    market: market_port.MarketDataGateway
    paper: paper.PaperTradingAdapter
    shadow: shadow.ShadowAdapter
    registry: resolver.AdapterRegistry
    services: api_services.ApiServices
    uow_factory: sql.SqlUnitOfWorkFactory
    brokers: dict[str, broker_base.BrokerAdapter] = dataclasses.field(
        default_factory=dict
    )
    live_streams: live_streams.LiveStreamSupervisor | None = None


# ---------------------------------------------------------------------------
# Securities brokers
# ---------------------------------------------------------------------------


def broker_account_lookup(
    uow_factory: sql.SqlUnitOfWorkFactory, credential_vault: vault.AesGcmVault
) -> Callable[[str], str | None]:
    """Builds the Joseph account -> broker account number lookup.

    Args:
        uow_factory: Unit of work factory.
        credential_vault: Vault holding the sealed connection fields.

    Returns:
        Lookup returning ``None`` for unknown or revoked accounts.
    """

    def lookup(account_id: str) -> str | None:
        """Reads the sealed broker account number of an account.

        Args:
            account_id: Joseph account.

        Returns:
            Broker account number or ``None``.
        """
        with uow_factory() as uow:
            account = uow.accounts.get(account_id)
            if account is None or account.credential_id is None:
                return None
            envelope = uow.credentials.get(account.credential_id)
        if envelope is None:
            return None
        try:
            fields = credential_vault.open(envelope)
        except errors.ExecutionError:
            return None
        number = fields.get(account_service.BROKER_ACCOUNT_FIELD, "").strip()
        return number or None

    return lookup


def build_brokers(
    settings: config.Settings,
    market: market_port.MarketDataGateway,
    clock: service_ports.Clock,
    lookup: Callable[[str], str | None],
) -> dict[str, broker_base.BrokerAdapter]:
    """Creates the broker adapters with the deployment's gates.

    Args:
        settings: Settings.
        market: Market gateway.
        clock: Clock.
        lookup: Joseph account -> broker account number.

    Returns:
        Adapters by broker code.
    """
    sec = settings.securities
    sinopac_config = None
    if sec.sinopac_configured:
        assert sec.sinopac_api_key is not None
        assert sec.sinopac_secret_key is not None
        sinopac_config = sinopac.SinopacConfig(
            api_key=sec.sinopac_api_key,
            secret_key=sec.sinopac_secret_key,
            ca_path=sec.sinopac_ca_path,
            ca_password=sec.sinopac_ca_password,
            person_id=sec.sinopac_person_id,
            simulation=sec.sinopac_simulation,
        )
    return broker_catalog.build(
        live_trading_enabled=settings.live_trading_enabled,
        ibkr_config=ibkr.IbkrConfig(
            host=sec.ibkr_host,
            port=sec.ibkr_port,
            client_id=sec.ibkr_client_id,
            simulation=sec.ibkr_paper,
            short_selling=sec.ibkr_short_selling,
            readonly=sec.ibkr_readonly,
        ),
        ibkr_enabled=sec.ibkr_enabled,
        sinopac_config=sinopac_config,
        sinopac_enabled=sec.sinopac_enabled,
        market_data=market,
        clock=clock,
        account_lookup=lookup,
    )


_LIVE_OPEN = (
    enums.OrderState.SUBMITTING,
    enums.OrderState.ACKNOWLEDGED,
    enums.OrderState.PARTIALLY_FILLED,
    enums.OrderState.CANCEL_PENDING,
    enums.OrderState.UNKNOWN_RECONCILING,
)


async def pump_brokers(container: Container) -> int:
    """Binds accounts with working LIVE orders and applies broker events.

    Args:
        container: Container.

    Returns:
        Events that changed OMS state.
    """
    brokers = container.brokers
    if not brokers:
        return 0
    with container.uow_factory() as uow:
        working = uow.orders.list(states=_LIVE_OPEN, limit=2000)
        ids = {o.account_id for o in working if o.mode is enums.OrderMode.LIVE}
        found = [uow.accounts.get(account_id) for account_id in sorted(ids)]
    for account in found:
        if account is not None and account.venue in brokers:
            brokers[account.venue].bind(account.account_id)
    applied = 0
    for adapter in brokers.values():
        for account_id in adapter.event_accounts():
            events = adapter.drain_events(account_id)
            if events:
                applied += await container.services.oms.apply_venue_events(
                    account_id, events
                )
    return applied


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
    twd_cash = settings.securities.paper_twd_starting_cash
    paper_adapter = paper.PaperTradingAdapter(
        gateway,
        the_clock,
        starting_equity=settings.paper_starting_equity,
        starting_cash={} if twd_cash is None else {"TWD": twd_cash},
    )
    shadow_adapter = shadow.ShadowAdapter(gateway, the_clock)
    credential_vault = vault.AesGcmVault(
        settings.vault_master_key, on_secret=log_lib.SECRETS.add
    )
    brokers = build_brokers(
        settings,
        gateway,
        the_clock,
        broker_account_lookup(uow_factory, credential_vault),
    )
    crypto = _live_adapters(settings, the_clock, uow_factory, credential_vault)
    registry = resolver.AdapterRegistry(
        paper_adapter, shadow_adapter, {**crypto, **brokers}
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
            queue_closed_market_orders=(
                settings.securities.closed_market_orders == "queue"
            ),
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
            credential_vault,
            audit_log,
            the_clock,
            id_gen,
            settings.paper_starting_equity,
            paper_twd_starting_cash=twd_cash,
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
        brokers=brokers,
        live_streams=live_streams.LiveStreamSupervisor(
            uow_factory, registry, oms, metrics, venues=tuple(crypto)
        ),
    )


def _live_adapters(
    settings: config.Settings,
    clock: service_ports.Clock,
    uow_factory: sql.SqlUnitOfWorkFactory,
    credential_vault: service_ports.CredentialVault,
) -> dict[str, trading_port.TradingAdapter]:
    """Builds the credentialed crypto venue adapters.

    Credentials are read from the vault per call; the global LIVE gate is
    passed down as a defense-in-depth check in front of every venue call.

    Args:
        settings: Settings.
        clock: Clock.
        uow_factory: Unit of work factory.
        credential_vault: Credential vault.

    Returns:
        Adapters by venue.
    """
    venues = settings.venues
    return catalog.build_live_adapters(
        credentials=venue_credentials.VaultCredentialSource(
            uow_factory, credential_vault
        ),
        clock=clock,
        options=venue_live.VenueOptions(
            live_trading_enabled=settings.live_trading_enabled,
            timeout_s=venues.timeout_s,
            recv_window_ms=venues.recv_window_ms,
            market_slippage_bps=venues.market_slippage_bps,
        ),
        sandbox_venues=venues.sandbox_venues,
        directory=venue_directory.RepositoryOrderDirectory(uow_factory),
        urls=venues.urls,
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

    async def broker_events() -> None:
        """Applies broker events; resyncs brokers periodically."""
        interval = settings.securities.broker_resync_interval_ms / 1000
        last = time.monotonic()
        while True:
            await asyncio.sleep(settings.tick_interval_ms / 1000)
            try:
                if time.monotonic() - last >= interval:
                    last = time.monotonic()
                    for adapter in container.brokers.values():
                        await adapter.resync()
                await pump_brokers(container)
            except Exception:
                _LOG.exception("broker event pump failed")

    jobs: list[api_app.BackgroundTask] = [
        ticker,
        outbox,
        reconcile,
        broker_events,
    ]
    supervisor = container.live_streams
    if (
        supervisor is not None
        and settings.live_trading_enabled
        and settings.venues.streams
    ):
        jobs.append(supervisor.run_forever)
    return jobs


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
