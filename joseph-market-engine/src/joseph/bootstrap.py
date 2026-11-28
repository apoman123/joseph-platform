# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Dependency-injection composition root for the market engine.

Wires adapters to ports. This is the only module allowed to import the API
layer and concrete adapters together. Run with::

    uv run uvicorn joseph.bootstrap:create_application --factory --port 8000

Wiring overview:

* market data → ``MarketDataService``;
* Python scripts → ``ScriptService`` (built-in and plugin indicators,
  strategies, filters and alerts in process, tenant source in the isolated
  runner); strategy backtests are script runs;
* strategies → ``StrategyActivations`` (closed bars → Python strategy →
  signals);
* signals → alert routing → durable outbox → ``OutboxWorker`` senders
  (UI stream, Telegram, LINE, e-mail, webhook, execution forwarder).
"""

from __future__ import annotations

import dataclasses
import smtplib
from collections.abc import Callable
from collections.abc import Mapping
from collections.abc import Sequence

import fastapi
import httpx
import sqlalchemy as sa

from joseph.alerts.adapters import channels as alert_channels
from joseph.alerts.adapters import mobile as mobile_alerts
from joseph.alerts.adapters import sql as alerts_sql
from joseph.alerts.app import dispatcher as alert_dispatcher
from joseph.alerts.app import ports as alert_ports
from joseph.alerts.app import subscriptions as alert_subscriptions
from joseph.alerts.domain import model as alert_model
from joseph.api import app as api_app
from joseph.api import deps
from joseph.api.routers import alerts as alerts_router
from joseph.api.routers import heatmap as heatmap_router
from joseph.api.routers import script_jobs as script_jobs_router
from joseph.api.routers import scripts as scripts_router
from joseph.api.routers import signals as signals_router
from joseph.api.routers import strategy_activations as activations_router
from joseph.domain import clock as clock_lib
from joseph.domain import events
from joseph.domain import instruments
from joseph.domain import market
from joseph.market_data.adapters import alpaca
from joseph.market_data.adapters import bar_archive_sql
from joseph.market_data.adapters import binance_vision
from joseph.market_data.adapters import bitget_archive
from joseph.market_data.adapters import coingecko as coingecko_markets
from joseph.market_data.adapters import factory
from joseph.market_data.adapters import http as venue_http
from joseph.market_data.adapters import lighter_archive
from joseph.market_data.adapters import mexc
from joseph.market_data.adapters import replay as replay_lib
from joseph.market_data.adapters import taiwan_open_data
from joseph.market_data.adapters import trade_archive_sql
from joseph.market_data.adapters import us_equities
from joseph.market_data.adapters import ws
from joseph.market_data.app import candle_store
from joseph.market_data.app import derived_bars
from joseph.market_data.app import feed_router
from joseph.market_data.app import heatmap as heatmap_service
from joseph.market_data.app import ports
from joseph.market_data.app import registry
from joseph.market_data.app import service as market_service
from joseph.market_data.app import trade_recorder
from joseph.market_data.app import universe as market_universe
from joseph.market_data.domain import candles as candle_rules
from joseph.market_data.domain import health
from joseph.mcp import application as mcp_application
from joseph.mcp import terminal as terminal_control
from joseph.platform import auth
from joseph.platform import bus
from joseph.platform import config
from joseph.platform import db
from joseph.platform import metrics as metrics_lib
from joseph.platform import redaction
from joseph.platform import streaming
from joseph.screening.adapters import coingecko
from joseph.screening.app import service as screening_service
from joseph.scripts import activations as strategy_activations
from joseph.scripts import catalog as script_catalog
from joseph.scripts import jobs as script_jobs
from joseph.scripts import repository as script_repository
from joseph.scripts import runner as script_runner
from joseph.scripts import service as script_service
from joseph.signals.adapters import sql as signals_sql
from joseph.signals.app import service as signal_service


@dataclasses.dataclass
class SignalStack:
    """Signal and alert services.

    Attributes:
        signals: Signal history service.
        dispatcher: Alert dispatcher (signal publisher).
        outbox_worker: Alert delivery worker.
        subscriptions: Alert subscription service.
        http_client: HTTP client used by alert senders.
    """

    signals: signal_service.SignalService
    dispatcher: alert_dispatcher.AlertDispatcher
    outbox_worker: alert_dispatcher.OutboxWorker
    subscriptions: alert_subscriptions.SubscriptionService
    http_client: httpx.AsyncClient


@dataclasses.dataclass
class Container:
    """Every wired service of the market engine.

    Attributes:
        settings: Settings.
        clock: Time source.
        adapters: Market-data adapter per enabled venue.
        registry: Instrument registry.
        store: Candle store.
        router: Feed router.
        market_data: Market-data service.
        qualification: Qualification service.
        stream_hub: Resumable stream hub (publish ``signals`` here).
        event_bus: In-process domain-event bus.
        engine: SQLAlchemy engine.
        outbox: Transactional outbox.
        inbox: Idempotent-consumer inbox.
        authenticator: Bearer-token authenticator.
        metrics: Metrics registry.
        replay: Optional replay source.
        signal_stack: Signal and alert services.
        closers: Close hooks that ``aclose`` runs last, before disposing of
            the database engine; a hook may return an awaitable, which is
            awaited.
        recorder: Optional public-trade recorder.
        starters: Background producer startup hooks, after the delivery worker.
        stoppers: Producer shutdown hooks, before delivery and data sources
            stop.
        heatmap_sources: Keyless public heatmap sources, None in DEMO-only
            mode (tests may inject fixture-backed ones).
    """

    settings: config.Settings
    clock: clock_lib.Clock
    adapters: dict[instruments.Venue, ports.MarketDataPort]
    registry: registry.InstrumentRegistry
    store: candle_store.CandleStore
    router: feed_router.FeedRouter
    market_data: market_service.MarketDataService
    qualification: screening_service.QualificationService
    stream_hub: streaming.StreamHub
    event_bus: bus.EventBus
    engine: sa.Engine
    outbox: db.OutboxRepository
    inbox: db.InboxRepository
    authenticator: auth.TokenAuthenticator
    metrics: metrics_lib.MetricsRegistry
    replay: replay_lib.ReplaySource | None = None
    signal_stack: SignalStack | None = None
    closers: list[Callable[[], object]] = dataclasses.field(
        default_factory=list
    )
    recorder: trade_recorder.TradeRecorder | None = None
    starters: list[Callable[[], None]] = dataclasses.field(default_factory=list)
    stoppers: list[Callable[[], object]] = dataclasses.field(
        default_factory=list
    )
    heatmap_sources: heatmap_service.HeatmapSources | None = None

    async def astart(self) -> None:
        """Starts the background workers.

        Starts provider metadata warmup, the trade recorder, the alert
        outbox worker and the registered producers (such as the strategy
        activation loop and Python alert jobs). Each creates asyncio
        tasks, so this runs inside the app's event loop (the FastAPI
        lifespan). Feed sessions are not started here: the feed
        router opens them on demand (``FeedRouter.acquire``).
        """
        self.registry.start_warmup()
        if self.recorder is not None:
            self.recorder.start()
        stack = self.signal_stack
        if stack is None:
            return
        stack.outbox_worker.start(self.settings.alert_interval_s)
        for starter in self.starters:
            starter()

    async def aclose(self) -> None:
        """Stops workers and feeds and closes network resources.

        Producers stop before what they use: the registered producers
        (which queue alerts) before the outbox worker that delivers them,
        the worker before the HTTP client of its senders, the feed sessions
        and the trade recorder before the adapters they read from, and the
        database engine last.
        """
        for stopper in self.stoppers:
            result = stopper()
            if hasattr(result, "__await__"):
                await result
        stack = self.signal_stack
        if stack is not None:
            await stack.outbox_worker.stop()
            await stack.http_client.aclose()
        await self.router.close()
        await self.registry.close()
        if self.recorder is not None:
            await self.recorder.stop()
        for adapter in self.adapters.values():
            await adapter.close()
        for closer in self.closers:
            result = closer()
            if hasattr(result, "__await__"):
                await result
        self.engine.dispose()


def build_container(
    settings: config.Settings | None = None,
    *,
    clock: clock_lib.Clock | None = None,
    http_client: httpx.AsyncClient | None = None,
    connect: ws.ConnectFn = ws.default_connect,
) -> Container:
    """Builds and wires every market-engine service.

    Args:
        settings: Settings (read from the environment when omitted).
        clock: Time source (system clock when omitted).
        http_client: Optional HTTP client shared by the venue adapters
            (tests inject respx-backed ones); alert senders use their own.
        connect: WebSocket connector.

    Returns:
        Container.

    Raises:
        ValueError: If the Taiwan source setting is unsupported.
    """
    settings = settings or config.Settings.from_env()
    if settings.tw_data_source not in config.TW_DATA_SOURCES:
        raise ValueError("JOSEPH_TW_DATA_SOURCE must be twse or fugle")
    clock = clock or clock_lib.SystemClock()
    metrics = metrics_lib.standard_registry()
    engine = db.create_engine(settings.database_url)
    archive_engine = (
        db.create_engine(settings.trade_archive_url)
        if settings.trade_archive_url
        else None
    )
    # Only the TWSE and TPEx adapters keep recorded minutes in a bar archive.
    exchange = {instruments.Venue.TWSE, instruments.Venue.TPEX}
    raw_adapters = factory.build_adapters(
        settings.active_venues,
        clock,
        crypto_source="native",
        timeout_s=settings.http_timeout_s,
        client=http_client,
        connect=connect,
        alpaca_config=alpaca.AlpacaConfig(
            key_id=settings.alpaca_key_id,
            secret_key=settings.alpaca_secret_key,
            feed=alpaca.Feed(settings.alpaca_feed),
            seconds_history_ms=settings.alpaca_seconds_history_ms,
        ),
        mexc_ws_format=mexc.WsFormat(settings.mexc_ws_format),
        fugle_api_key=(
            settings.fugle_api_key
            if settings.tw_data_source == "fugle"
            else None
        ),
        fugle_adjusted_daily=settings.fugle_adjusted_daily,
        fred_api_key=settings.fred_api_key,
        bar_archive=(
            bar_archive_sql.SqlBarArchive(archive_engine or engine)
            if exchange & set(settings.active_venues)
            else None
        ),
    )
    recorder = _trade_recorder(
        settings, raw_adapters, clock, archive_engine or engine
    )
    adapters = derived_bars.wrap(raw_adapters, clock, recorder=recorder)
    hub = streaming.StreamHub(clock, retention=settings.stream_retention)
    event_bus = bus.EventBus()
    # A series must hold an anchored study's whole window (anchor to now).
    store = candle_store.CandleStore(
        max_bars_per_series=max(20_000, settings.max_anchored_bars + 1_000)
    )
    replay = (
        replay_lib.ReplaySource(settings.replay_dir)
        if settings.replay_dir
        else None
    )
    reconnects = metrics.gauge(
        "joseph_feed_reconnects", "Reconnects of the venue's worst feed."
    )
    lag = metrics.gauge("joseph_feed_lag_ms", "Last message age per feed.")
    revisions = metrics.counter(
        "joseph_candle_revisions_total", "Final candles revised by the venue."
    )

    async def on_candle(
        session: feed_router.FeedSession,
        candle: market.Candle,
        action: candle_rules.MergeAction,
    ) -> None:
        """Publishes candle updates and audits revisions.

        Also marks the instrument as in use for the trade recorder. A
        revised final candle increments the revision counter and publishes
        a ``candle.revised`` domain event.

        Args:
            session: Producing session.
            candle: Stored candle.
            action: Merge outcome.
        """
        if recorder is not None:
            recorder.touch(candle.instrument_id)
        hub.publish(
            streaming.candle_channel(
                str(candle.instrument_id),
                candle.timeframe.value,
                session.mode.value,
            ),
            "candle.updated",
            candle.to_contract(),
        )
        if action is candle_rules.MergeAction.REVISED:
            revisions.inc(venue=candle.instrument_id.venue.value)
            await event_bus.publish(
                events.DomainEvent(
                    event_type="candle.revised",
                    payload=candle.to_contract(),
                    idempotency_key=(
                        f"{candle.instrument_id}|{candle.timeframe.value}|"
                        f"{candle.start_ms}|{candle.revision}"
                    ),
                    ts_ms=clock.now_ms(),
                )
            )

    async def on_health(feed: health.FeedHealth) -> None:
        """Publishes feed health changes and updates the feed gauges.

        Args:
            feed: Health snapshot.
        """
        reconnects.set(feed.reconnects, venue=feed.venue.value)
        if feed.lag_ms is not None:
            lag.set(feed.lag_ms, venue=feed.venue.value)
        hub.publish("feed_health", "feed.health", feed.to_contract())

    router = feed_router.FeedRouter(
        adapters,
        store,
        clock,
        replay=replay,
        on_candle=on_candle,
        on_health=on_health,
        poll_interval_s=settings.poll_interval_s,
        stale_after_ms=settings.snapshot_stale_after_ms,
    )
    instrument_registry = registry.InstrumentRegistry(adapters)
    market_data = market_service.MarketDataService(
        adapters,
        instrument_registry,
        store,
        router,
        clock,
        replay=replay,
        stale_after_ms=settings.snapshot_stale_after_ms,
        max_refresh_bars=settings.max_anchored_bars + 10_000,
        recorded=recorder,
    )
    trending = (
        coingecko.CoinGeckoTrending() if settings.coingecko_enabled else None
    )
    qualification = screening_service.QualificationService(
        adapters,
        clock,
        top_n=settings.top_n,
        trending=trending,
    )
    container = Container(
        settings=settings,
        clock=clock,
        adapters=adapters,
        registry=instrument_registry,
        store=store,
        router=router,
        market_data=market_data,
        qualification=qualification,
        stream_hub=hub,
        event_bus=event_bus,
        engine=engine,
        outbox=db.OutboxRepository(engine),
        inbox=db.InboxRepository(engine),
        authenticator=auth.TokenAuthenticator(settings.dev_tokens),
        metrics=metrics,
        replay=replay,
        recorder=recorder,
    )
    if archive_engine is not None:
        container.closers.append(archive_engine.dispose)
    if trending is not None:
        container.closers.append(trending.close)
    if not settings.demo_only:
        container.heatmap_sources = heatmap_sources(
            clock, http_client, settings.http_timeout_s
        )
    container.signal_stack = build_signal_stack(container)
    return container


def heatmap_sources(
    clock: clock_lib.Clock,
    client: httpx.AsyncClient | None,
    timeout_s: float,
) -> heatmap_service.HeatmapSources:
    """Builds the keyless public heatmap sources.

    Args:
        clock: Time source of the CoinGecko back-off.
        client: Optional shared HTTP client (tests inject fixture-backed
            ones); each source otherwise owns a client.
        timeout_s: Request timeout of owned clients.

    Returns:
        Nasdaq.com/Wikipedia, TWSE/TPEx open data and CoinGecko sources.
    """

    def client_for(
        venue: instruments.Venue, base_url: str, name: str, rate: float
    ) -> venue_http.VenueHttp:
        """Creates one rate-limited client.

        Args:
            venue: Venue tag of the client (messages use ``name``).
            base_url: Fixed public origin.
            name: Source name in error messages.
            rate: Requests per second.

        Returns:
            Client.
        """
        return venue_http.VenueHttp(
            venue,
            base_url,
            client=client,
            timeout_s=timeout_s,
            rate_per_s=rate,
            burst=3,
            max_concurrency=2,
            name=name,
        )

    return heatmap_service.HeatmapSources(
        us=us_equities.UsEquitySource(
            client_for(
                instruments.Venue.NASDAQ,
                us_equities.NASDAQ_API,
                "nasdaq.com",
                1.0,
            ),
            client_for(
                instruments.Venue.NASDAQ,
                us_equities.WIKIPEDIA_API,
                "wikipedia",
                1.0,
            ),
        ),
        taiwan=taiwan_open_data.TaiwanOpenData(
            client_for(
                instruments.Venue.TWSE,
                taiwan_open_data.TWSE_OPENAPI,
                "twse open data",
                1.0,
            ),
            client_for(
                instruments.Venue.TPEX,
                taiwan_open_data.TPEX_OPENAPI,
                "tpex open data",
                1.0,
            ),
        ),
        coins=coingecko_markets.CoinGeckoSource(
            # The venue only tags the client; messages say ``coingecko``.
            client_for(
                instruments.Venue.BINANCE,
                coingecko_markets.API_URL,
                "coingecko",
                0.2,
            ),
            clock,
        ),
    )


def _trade_recorder(
    settings: config.Settings,
    adapters: Mapping[instruments.Venue, ports.MarketDataPort],
    clock: clock_lib.Clock,
    archive_engine: sa.Engine,
) -> trade_recorder.TradeRecorder | None:
    """Builds the public-trade recorder and its archive.

    Args:
        settings: Settings.
        adapters: Raw venue adapters.
        clock: Time source.
        archive_engine: Engine of ``trade_archive_url`` (else of
            ``database_url``).

    Returns:
        Recorder, or ``None`` when recording is disabled or no real venue
        is enabled.
    """
    real = [v for v in adapters if v is not instruments.Venue.MOCK]
    if not settings.trade_recording or not real:
        return None
    day_archives: list[trade_recorder.DayArchive] = []
    if settings.binance_archive and instruments.Venue.BINANCE in adapters:
        day_archives.append(
            binance_vision.BinanceVision(settings.binance_archive_dir)
        )
    if settings.bitget_archive and instruments.Venue.BITGET in adapters:
        day_archives.append(
            bitget_archive.BitgetArchive(settings.bitget_archive_dir)
        )
    if settings.lighter_archive and instruments.Venue.LIGHTER in adapters:
        day_archives.append(lighter_archive.LighterArchive())
    return trade_recorder.TradeRecorder(
        adapters,
        trade_archive_sql.SqlTradeArchive(archive_engine),
        clock,
        trade_recorder.RecorderSettings(
            retention_ms=int(settings.trade_bar_retention_days * 86_400_000),
            max_instruments=settings.trade_record_max_instruments,
            backfill_max_calls=settings.trade_backfill_max_calls,
            always=tuple(
                instruments.InstrumentId.parse(value)
                for value in settings.trade_record_instruments
            ),
        ),
        day_archives=day_archives,
    )


def _senders(
    settings: config.Settings,
    hub: streaming.StreamHub,
    client: httpx.AsyncClient,
    subscriptions: alert_ports.SubscriptionRepository | None = None,
) -> Mapping[alert_model.Channel, alert_ports.ChannelSender]:
    """Builds alert senders for every configured channel.

    Args:
        settings: Settings (channel credentials).
        hub: Stream hub for UI delivery.
        client: Shared HTTP client.
        subscriptions: Optional tenant-scoped private browser device store.

    Returns:
        Sender per channel; unconfigured channels are absent and their
        deliveries dead-letter with a clear reason.
    """

    async def ui_sink(tenant_id: str, payload: Mapping[str, object]) -> None:
        """Publishes a signal on the tenant-scoped ``signals`` channel.

        Args:
            tenant_id: Tenant.
            payload: Contract ``Signal``.
        """
        hub.publish(
            "signals", "signal.created", dict(payload), tenant_id=tenant_id
        )

    senders: dict[alert_model.Channel, alert_ports.ChannelSender] = {
        alert_model.Channel.UI: alert_channels.UiSender(ui_sink),
    }
    if settings.telegram_bot_token:
        senders[alert_model.Channel.TELEGRAM] = alert_channels.TelegramSender(
            client, settings.telegram_bot_token
        )
    if (
        settings.whatsapp_access_token
        and settings.whatsapp_phone_number_id
        and settings.whatsapp_template
    ):
        senders[alert_model.Channel.WHATSAPP] = mobile_alerts.WhatsAppSender(
            client,
            settings.whatsapp_access_token,
            settings.whatsapp_phone_number_id,
            settings.whatsapp_template,
            language=settings.whatsapp_language,
            version=settings.whatsapp_api_version,
        )
    if (
        settings.web_push_private_key
        and settings.web_push_public_key
        and settings.web_push_contact
        and subscriptions is not None
    ):
        senders[alert_model.Channel.WEB_PUSH] = mobile_alerts.WebPushSender(
            subscriptions,
            settings.web_push_private_key,
            settings.web_push_public_key,
            settings.web_push_contact,
        )
    if settings.line_channel_token:
        senders[alert_model.Channel.LINE] = alert_channels.LineSender(
            client, settings.line_channel_token
        )
    if settings.webhook_secret:
        senders[alert_model.Channel.WEBHOOK] = alert_channels.WebhookSender(
            client, settings.webhook_secret
        )
    if settings.smtp_host and settings.smtp_from:
        host, port = settings.smtp_host, settings.smtp_port

        def smtp_factory() -> smtplib.SMTP:
            """Opens an SMTP connection.

            Returns:
                Connected SMTP client.
            """
            return smtplib.SMTP(host, port, timeout=10)

        senders[alert_model.Channel.EMAIL] = alert_channels.EmailSender(
            smtp_factory,
            settings.smtp_from,
            username=settings.smtp_username,
            password=settings.smtp_password,
        )
    if settings.service_token:
        senders[alert_model.Channel.EXECUTION] = (
            alert_channels.ExecutionForwarder(
                client, settings.execution_url, settings.service_token
            )
        )
    return senders


def build_signal_stack(container: Container) -> SignalStack:
    """Wires signals and alerts.

    Creates the signal and alert tables when they are missing, and
    an HTTP client for the alert senders that ``Container.aclose`` closes.

    Args:
        container: Market-data container.

    Returns:
        Signal stack.
    """
    settings, clock, engine = (
        container.settings,
        container.clock,
        container.engine,
    )
    signals_sql.create_schema(engine)
    alerts_sql.create_schema(engine)
    signals = signal_service.SignalService(
        signals_sql.SqlSignalRepository(engine)
    )
    http_client = httpx.AsyncClient(timeout=settings.http_timeout_s)
    outbox = alerts_sql.SqlOutbox(engine)
    subscription_repo = alerts_sql.SqlSubscriptionRepository(engine)
    senders = _senders(
        settings, container.stream_hub, http_client, subscription_repo
    )
    dispatcher = alert_dispatcher.AlertDispatcher(
        outbox,
        subscription_repo,
        clock,
        ui_enabled=True,
        forward_execution=bool(
            settings.forward_signals and settings.service_token
        ),
    )
    worker = alert_dispatcher.OutboxWorker(
        outbox,
        senders,
        clock,
    )
    return SignalStack(
        signals=signals,
        dispatcher=dispatcher,
        outbox_worker=worker,
        subscriptions=alert_subscriptions.SubscriptionService(
            subscription_repo,
            [channel.value for channel in senders],
            outbox=outbox,
            web_push_public_key=settings.web_push_public_key
            if alert_model.Channel.WEB_PUSH in senders
            else None,
        ),
        http_client=http_client,
    )


def signal_routers(stack: SignalStack) -> list[fastapi.APIRouter]:
    """Builds the routers of the signal stack.

    Args:
        stack: Signal stack.

    Returns:
        Signal and alert-subscription routers.
    """
    return [
        signals_router.build_router(
            signals=stack.signals, get_principal=deps.get_principal
        ),
        alerts_router.build_router(
            service=stack.subscriptions, get_principal=deps.get_principal
        ),
    ]


def create_application(
    container: Container | None = None,
    extra_routers: Sequence[fastapi.APIRouter] = (),
) -> fastapi.FastAPI:
    """Builds the ASGI app (uvicorn ``--factory`` entry point).

    Configures secret-redacting logging first. The signal-stack routers are
    mounted before ``extra_routers``.

    Args:
        container: Optional pre-built container (built from the environment
            when omitted).
        extra_routers: Additional routers to mount.

    Returns:
        FastAPI application.
    """
    redaction.configure_logging()
    built = container or build_container()
    routers = list(extra_routers)
    scripts = script_service.ScriptService(
        script_repository.ScriptRepository(built.engine),
        script_runner.DockerRunner(
            enabled=built.settings.script_runtime == "docker",
            image=built.settings.script_runner_image,
            timeout_s=built.settings.script_timeout_s,
        ),
        built.market_data,
        script_catalog.build(
            script_catalog.CatalogConfig(
                packages=built.settings.script_plugin_packages,
                disabled=built.settings.script_plugins_disabled,
                required=built.settings.script_plugins_required,
            )
        ),
    )
    routers.append(scripts_router.build_router(scripts, deps.get_principal))
    universe = market_universe.UniverseService(built.market_data)
    heatmaps = heatmap_service.HeatmapService(
        universe, built.heatmap_sources, clock=built.clock
    )
    built.closers.append(heatmaps.close)
    routers.append(heatmap_router.build_router(heatmaps, deps.get_principal))
    if built.signal_stack is not None:
        jobs = script_jobs.ScriptJobs(
            scripts,
            universe,
            built.engine,
            history=built.signal_stack.signals,
            subscriptions=alerts_sql.SqlSubscriptionRepository(built.engine),
            outbox=alerts_sql.SqlOutbox(built.engine),
        )
        built.starters.append(jobs.start)
        built.stoppers.append(jobs.stop)
        routers.append(
            script_jobs_router.build_router(jobs, deps.get_principal)
        )
        activations = strategy_activations.StrategyActivations(
            scripts,
            built.engine,
            history=built.signal_stack.signals,
            subscriptions=alerts_sql.SqlSubscriptionRepository(built.engine),
            outbox=alerts_sql.SqlOutbox(built.engine),
            forward_execution=bool(
                built.settings.forward_signals and built.settings.service_token
            ),
            interval_s=built.settings.strategy_interval_s,
        )
        if built.settings.strategy_activations_enabled:
            built.starters.append(activations.start)
        built.stoppers.append(activations.stop)
        routers.append(
            activations_router.build_router(activations, deps.get_principal)
        )
        routers = signal_routers(built.signal_stack) + routers
    terminal = terminal_control.TerminalHub(
        built.authenticator, built.settings.cors_origins
    )
    routers.append(terminal_control.build_router(terminal, deps.get_principal))
    app = api_app.create_app(built, routers)
    app.state.python_scripts = scripts
    app.state.terminal_hub = terminal
    if built.signal_stack is not None:
        app.state.script_jobs = jobs
        app.state.strategy_activations = activations
    mcp_application.install(
        app,
        engine=built.engine,
        settings=built.settings,
        get_principal=deps.get_principal,
        closers=built.closers,
    )
    return app
