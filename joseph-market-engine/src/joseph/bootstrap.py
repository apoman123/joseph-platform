"""Dependency-injection composition root for the market engine.

Wires adapters to ports. This is the only module allowed to import the API
layer and concrete adapters together. Run with::

    uv run uvicorn joseph.bootstrap:create_application --factory --port 8000

Wiring overview:

* market data → ``MarketDataService`` (also the indicator ``CandleSource``);
* indicators → ``IndicatorQueryService`` + ``/v1/indicators``, ``/v1/aois``;
* strategies → ``StrategyVersionService`` (SQL) + ``StrategyRuntime``;
* live path → ``LiveSignalPipeline`` (closed candles → engine → runtime);
* signals → ``AlertDispatcher`` → durable outbox → ``OutboxWorker`` senders
  (UI stream, Telegram, LINE, e-mail, webhook, execution forwarder);
* backtests → ``EngineFrameProvider`` (same engine and evaluator as live).
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
from joseph.alerts.adapters import sql as alerts_sql
from joseph.alerts.app import dispatcher as alert_dispatcher
from joseph.alerts.app import subscriptions as alert_subscriptions
from joseph.alerts.domain import model as alert_model
from joseph.api import app as api_app
from joseph.api import deps
from joseph.api.routers import alerts as alerts_router
from joseph.api.routers import backtests as backtests_router
from joseph.api.routers import indicators as indicators_router
from joseph.api.routers import signals as signals_router
from joseph.api.routers import strategies as strategies_router
from joseph.backtest import runner as backtest_runner
from joseph.backtest import service as backtest_service
from joseph.domain import clock as clock_lib
from joseph.domain import events
from joseph.domain import instruments
from joseph.domain import market
from joseph.indicators import catalog
from joseph.indicators import service as indicator_service
from joseph.indicators.sdk import registry as plugin_registry
from joseph.market_data.adapters import factory
from joseph.market_data.adapters import replay as replay_lib
from joseph.market_data.adapters import ws
from joseph.market_data.app import candle_store
from joseph.market_data.app import feed_router
from joseph.market_data.app import ports
from joseph.market_data.app import registry
from joseph.market_data.app import service as market_service
from joseph.market_data.domain import candles as candle_rules
from joseph.market_data.domain import health
from joseph.pipeline import frames as pipeline_frames
from joseph.pipeline import live as pipeline_live
from joseph.pipeline import market_context
from joseph.pipeline import qualification as pipeline_qualification
from joseph.pipeline import ticks as pipeline_ticks
from joseph.platform import auth
from joseph.platform import bus
from joseph.platform import config
from joseph.platform import db
from joseph.platform import metrics as metrics_lib
from joseph.platform import redaction
from joseph.platform import streaming
from joseph.screening.adapters import coingecko
from joseph.screening.app import service as screening_service
from joseph.signals.adapters import sql as signals_sql
from joseph.signals.app import service as signal_service
from joseph.strategy.adapters import sql as strategy_sql
from joseph.strategy.app import runtime as strategy_runtime
from joseph.strategy.app import version_service


@dataclasses.dataclass
class SignalStack:
    """Indicator, strategy, signal, alert and backtest services.

    Attributes:
        plugins: Indicator plugin registry.
        indicator_queries: Point-in-time indicator query service.
        versions: Strategy version service.
        signals: Signal history service.
        runtime: Strategy runtime.
        gate: Qualification gate shared by runtime and pipeline.
        market_context: Market context recorder.
        frame_provider: Engine-backed frame provider.
        backtests: Backtest service.
        dispatcher: Alert dispatcher (runtime publisher).
        outbox_worker: Alert delivery worker.
        subscriptions: Alert subscription service.
        pipeline: Live signal pipeline.
        http_client: HTTP client used by alert senders.
    """

    plugins: plugin_registry.PluginRegistry
    indicator_queries: indicator_service.IndicatorQueryService
    versions: version_service.StrategyVersionService
    signals: signal_service.SignalService
    runtime: strategy_runtime.StrategyRuntime
    gate: pipeline_qualification.CachedQualificationGate
    market_context: market_context.SnapshotMarketContext
    frame_provider: pipeline_frames.EngineFrameProvider
    backtests: backtest_service.BacktestService
    dispatcher: alert_dispatcher.AlertDispatcher
    outbox_worker: alert_dispatcher.OutboxWorker
    subscriptions: alert_subscriptions.SubscriptionService
    pipeline: pipeline_live.LiveSignalPipeline
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
        market_data: Market-data service (also the indicator CandleSource).
        qualification: Qualification service.
        stream_hub: Resumable stream hub (publish ``aois``/``signals`` here).
        event_bus: In-process domain-event bus.
        engine: SQLAlchemy engine.
        outbox: Transactional outbox.
        inbox: Idempotent-consumer inbox.
        authenticator: Bearer-token authenticator.
        metrics: Metrics registry.
        replay: Optional replay source.
        signal_stack: Indicator/strategy/signal/alert services.
        closers: Extra async close hooks registered by integrators.
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

    async def astart(self) -> None:
        """Starts background workers (pipeline, alert outbox)."""
        stack = self.signal_stack
        if stack is None:
            return
        stack.outbox_worker.start(self.settings.alert_interval_s)
        if self.settings.pipeline_enabled:
            stack.pipeline.start()

    async def aclose(self) -> None:
        """Stops workers and feeds and closes network resources."""
        stack = self.signal_stack
        if stack is not None:
            await stack.pipeline.stop()
            await stack.outbox_worker.stop()
            await stack.http_client.aclose()
        await self.router.close()
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
        http_client: Optional shared HTTP client (tests inject respx).
        connect: WebSocket connector.

    Returns:
        Container.
    """
    settings = settings or config.Settings.from_env()
    clock = clock or clock_lib.SystemClock()
    metrics = metrics_lib.standard_registry()
    adapters = factory.build_adapters(
        settings.active_venues,
        clock,
        timeout_s=settings.http_timeout_s,
        client=http_client,
        connect=connect,
    )
    hub = streaming.StreamHub(clock, retention=settings.stream_retention)
    event_bus = bus.EventBus()
    store = candle_store.CandleStore()
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

        Args:
            session: Producing session.
            candle: Stored candle.
            action: Merge outcome.
        """
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
        """Publishes feed health changes.

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
    engine = db.create_engine(settings.database_url)
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
    )
    if trending is not None:
        container.closers.append(trending.close)
    container.signal_stack = build_signal_stack(container)
    return container


def _channel_policy(
    repository: strategy_sql.SqlStrategyRepository,
) -> Callable[[str, str], frozenset[str]]:
    """Builds the per-version channel policy from stored definitions.

    Args:
        repository: Strategy repository.

    Returns:
        Policy returning the channels a strategy version opted into.
    """

    def policy(tenant_id: str, strategy_version_id: str) -> frozenset[str]:
        """Returns the version's notification channels (UI by default).

        Args:
            tenant_id: Owning tenant.
            strategy_version_id: ``<strategy_id>:v<n>``.

        Returns:
            Channel names.
        """
        strategy_id, _, number = strategy_version_id.rpartition(":v")
        try:
            version = repository.get(tenant_id, strategy_id, int(number))
        except (ValueError, LookupError):
            return frozenset({"UI"})
        if version is None:
            return frozenset({"UI"})
        chosen = version.definition.notification.channels
        names = {str(getattr(c, "value", c)) for c in chosen or ()}
        return frozenset(names or {"UI"})

    return policy


def _senders(
    settings: config.Settings,
    hub: streaming.StreamHub,
    client: httpx.AsyncClient,
) -> Mapping[alert_model.Channel, object]:
    """Builds alert senders for every configured channel.

    Args:
        settings: Settings (channel credentials).
        hub: Stream hub for UI delivery.
        client: Shared HTTP client.

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

    senders: dict[alert_model.Channel, object] = {
        alert_model.Channel.UI: alert_channels.UiSender(ui_sink),
    }
    if settings.telegram_bot_token:
        senders[alert_model.Channel.TELEGRAM] = alert_channels.TelegramSender(
            client, settings.telegram_bot_token
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
    """Wires indicators, strategies, signals, alerts and backtests.

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
    strategy_sql.create_schema(engine)
    signals_sql.create_schema(engine)
    alerts_sql.create_schema(engine)
    plugins = catalog.build_default_registry()
    available = plugins.ids()
    repository = strategy_sql.SqlStrategyRepository(engine)
    versions = version_service.StrategyVersionService(
        repository, clock, available_plugins=available
    )
    signals = signal_service.SignalService(
        signals_sql.SqlSignalRepository(engine)
    )
    http_client = httpx.AsyncClient(timeout=settings.http_timeout_s)
    outbox = alerts_sql.SqlOutbox(engine)
    subscription_repo = alerts_sql.SqlSubscriptionRepository(engine)
    senders = _senders(settings, container.stream_hub, http_client)
    dispatcher = alert_dispatcher.AlertDispatcher(
        outbox,
        subscription_repo,
        clock,
        strategy_channels=_channel_policy(repository),
        ui_enabled=True,
        forward_execution=bool(
            settings.forward_signals and settings.service_token
        ),
    )
    worker = alert_dispatcher.OutboxWorker(
        outbox,
        senders,  # type: ignore[arg-type]
        clock,
    )
    gate = pipeline_qualification.CachedQualificationGate(
        container.qualification, clock
    )
    context = market_context.SnapshotMarketContext()
    runtime = strategy_runtime.StrategyRuntime(
        repository=repository,
        signals=signals,
        checkpoints=strategy_sql.SqlCheckpointRepository(engine),
        clock=clock,
        publisher=dispatcher,
        qualification=gate,
        market=context,
        available_plugins=available,
    )
    tick_sizes = pipeline_ticks.RegistryTickSizes(container.registry, clock)
    provider = pipeline_frames.EngineFrameProvider(
        container.market_data, plugins, tick_sizes=tick_sizes
    )
    pipeline = pipeline_live.LiveSignalPipeline(
        runtime,
        provider,
        container.market_data,
        gate=gate,
        context=context,
        clock=clock,
        interval_s=settings.pipeline_interval_s,
        max_instruments_per_venue=settings.pipeline_max_instruments,
    )
    backtests = backtest_service.BacktestService(
        versions,
        backtest_runner.BacktestRunner(provider, available_plugins=available),
    )
    return SignalStack(
        plugins=plugins,
        indicator_queries=indicator_service.IndicatorQueryService(
            container.market_data, plugins, clock, tick_sizes=tick_sizes
        ),
        versions=versions,
        signals=signals,
        runtime=runtime,
        gate=gate,
        market_context=context,
        frame_provider=provider,
        backtests=backtests,
        dispatcher=dispatcher,
        outbox_worker=worker,
        subscriptions=alert_subscriptions.SubscriptionService(
            subscription_repo, [channel.value for channel in senders]
        ),
        pipeline=pipeline,
        http_client=http_client,
    )


def signal_routers(stack: SignalStack) -> list[fastapi.APIRouter]:
    """Builds the routers of the signal stack.

    Args:
        stack: Signal stack.

    Returns:
        Indicator, strategy, signal and backtest routers.
    """
    return [
        indicators_router.build_router(stack.indicator_queries, stack.plugins),
        strategies_router.build_router(
            versions=stack.versions, get_principal=deps.get_principal
        ),
        signals_router.build_router(
            signals=stack.signals, get_principal=deps.get_principal
        ),
        backtests_router.build_router(
            backtests=stack.backtests, get_principal=deps.get_principal
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

    Args:
        container: Optional pre-built container.
        extra_routers: Additional routers to mount.

    Returns:
        FastAPI application.
    """
    redaction.configure_logging()
    built = container or build_container()
    routers = list(extra_routers)
    if built.signal_stack is not None:
        routers = signal_routers(built.signal_stack) + routers
    return api_app.create_app(built, routers)
