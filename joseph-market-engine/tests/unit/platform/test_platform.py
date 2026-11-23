"""Unit tests for configuration, auth, redaction, metrics, bus and storage."""

from __future__ import annotations

import logging

import pytest

from joseph.domain import clock as clock_lib
from joseph.domain import errors
from joseph.domain import events
from joseph.domain import instruments
from joseph.platform import auth
from joseph.platform import bus
from joseph.platform import config
from joseph.platform import db
from joseph.platform import metrics
from joseph.platform import redaction


def test_settings_from_env_parses_every_field() -> None:
    """All JOSEPH_* variables are honored."""
    settings = config.Settings.from_env(
        {
            "JOSEPH_DEV_TOKENS": "tok-a:tenant-01:user-01, tok-b:tenant-02:u2",
            "JOSEPH_SERVICE_TOKEN": "svc",
            "JOSEPH_EXECUTION_URL": "http://exec:8100/",
            "JOSEPH_CORS_ORIGINS": "http://a, http://b",
            "JOSEPH_DATABASE_URL": "sqlite://",
            "JOSEPH_ENABLED_VENUES": "binance, MEXC",
            "JOSEPH_POLL_INTERVAL_S": "0.1",
        }
    )
    assert [t.tenant_id for t in settings.dev_tokens] == [
        "tenant-01",
        "tenant-02",
    ]
    assert settings.execution_url == "http://exec:8100"
    assert settings.cors_origins == ("http://a", "http://b")
    assert settings.active_venues == (
        instruments.Venue.BINANCE,
        instruments.Venue.MEXC,
    )
    assert settings.poll_interval_s == 1.0


def test_trade_recording_settings_parse_with_bounds() -> None:
    """Trade recording, retention and archive variables are honored."""
    defaults = config.Settings.from_env({})
    assert defaults.trade_recording is True
    assert defaults.trade_bar_retention_days == 30.0
    assert defaults.binance_archive is True
    assert defaults.trade_archive_url is None
    settings = config.Settings.from_env(
        {
            "JOSEPH_TRADE_RECORDING": "false",
            "JOSEPH_TRADE_BAR_RETENTION_DAYS": "0.2",
            "JOSEPH_TRADE_RECORD_INSTRUMENTS": "bitget:perp:BTCUSDT, ",
            "JOSEPH_TRADE_RECORD_MAX_INSTRUMENTS": "-3",
            "JOSEPH_TRADE_BACKFILL_MAX_CALLS": "7",
            "JOSEPH_TRADE_ARCHIVE_URL": "sqlite:///./var/trades.db",
            "JOSEPH_BINANCE_ARCHIVE": "0",
            "JOSEPH_BINANCE_ARCHIVE_DIR": "/data/vision",
        }
    )
    assert settings.trade_recording is False
    assert settings.trade_bar_retention_days == 1.0
    assert settings.trade_record_instruments == ("bitget:perp:BTCUSDT",)
    assert settings.trade_record_max_instruments == 0
    assert settings.trade_backfill_max_calls == 7
    assert settings.trade_archive_url == "sqlite:///./var/trades.db"
    assert settings.binance_archive is False
    assert settings.binance_archive_dir == "/data/vision"


def test_demo_only_restricts_to_mock() -> None:
    """DEMO deployments never construct real venue adapters."""
    settings = config.Settings.from_env({"JOSEPH_DEMO_ONLY": "true"})
    assert settings.active_venues == (instruments.Venue.MOCK,)


def test_settings_repr_redacts_secrets() -> None:
    """Tokens never appear in representations."""
    settings = config.Settings(
        dev_tokens=config.parse_dev_tokens("supersecret:t:u"),
        service_token="servicesecret",
    )
    text = repr(settings) + repr(settings.dev_tokens)
    assert "supersecret" not in text
    assert "servicesecret" not in text


def test_malformed_dev_tokens_are_rejected() -> None:
    """Entries must be token:tenant:user."""
    with pytest.raises(ValueError):
        config.parse_dev_tokens("only:two")


def test_authenticator_accepts_known_and_rejects_unknown() -> None:
    """Bearer tokens map to principals; others are 401."""
    authenticator = auth.TokenAuthenticator(
        config.parse_dev_tokens("tok:tenant-01:user-01")
    )
    principal = authenticator.authenticate_header("Bearer tok")
    assert principal.tenant_id == "tenant-01"
    assert principal.has_scope("strategy:write")
    for header in (None, "", "Basic tok", "Bearer ", "Bearer nope"):
        with pytest.raises(errors.UnauthenticatedError):
            authenticator.authenticate_header(header)


def test_redaction_scrubs_common_secret_shapes() -> None:
    """Tokens, keys and long hex blobs are removed."""
    text = redaction.redact(
        "Authorization: Bearer abc.def api_key=XYZ secret: s3cr3t "
        "sig=" + "ab" * 40
    )
    assert "abc.def" not in text
    assert "XYZ" not in text
    assert "s3cr3t" not in text
    assert "ab" * 40 not in text


def test_redacting_filter_applies_to_formatted_records(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A careless log call cannot leak a token."""
    logger = logging.getLogger("joseph.test.redaction")
    handler = logging.Handler()
    records: list[logging.LogRecord] = []
    handler.emit = records.append  # type: ignore[method-assign,assignment]
    handler.addFilter(redaction.RedactingFilter())
    logger.addHandler(handler)
    try:
        logger.warning("headers %s", {"authorization": "Bearer leaked"})
    finally:
        logger.removeHandler(handler)
    assert "leaked" not in records[0].getMessage()


def test_metrics_render_prometheus_text() -> None:
    """Counters, gauges and histograms render in exposition format."""
    registry = metrics.MetricsRegistry()
    counter = registry.counter("joseph_x_total", "X.")
    counter.inc(venue="binance")
    counter.inc(2, venue="binance")
    registry.gauge("joseph_lag_ms", "Lag.").set(12.5, venue="mexc")
    registry.histogram("joseph_eval_seconds", "Eval.").observe(0.03)
    text = registry.render()
    assert "# TYPE joseph_x_total counter" in text
    assert 'joseph_x_total{venue="binance"} 3.0' in text
    assert 'joseph_lag_ms{venue="mexc"} 12.5' in text
    assert 'joseph_eval_seconds_bucket{le="+Inf"} 1' in text
    assert registry.counter("joseph_x_total", "X.") is counter
    with pytest.raises(TypeError):
        registry.gauge("joseph_x_total", "X.")
    with pytest.raises(ValueError):
        counter.inc(-1)


async def test_event_bus_isolates_failing_handlers() -> None:
    """One failing handler does not block the others."""
    event_bus = bus.EventBus()
    seen: list[str] = []

    async def good(event: events.DomainEvent) -> None:
        """Records the event.

        Args:
            event: Event.
        """
        seen.append(event.event_type)

    async def bad(event: events.DomainEvent) -> None:
        """Always fails.

        Args:
            event: Event.

        Raises:
            RuntimeError: Always.
        """
        raise RuntimeError(event.event_type)

    event_bus.subscribe("x.happened", bad)
    unsubscribe = event_bus.subscribe("*", good)
    event = events.DomainEvent("x.happened", {}, "k1", 1)
    await event_bus.publish(event)
    assert seen == ["x.happened"]
    assert event_bus.failures == 1
    unsubscribe()
    await event_bus.publish(event)
    assert seen == ["x.happened"]


def test_outbox_is_idempotent_and_dead_letters() -> None:
    """Duplicate keys are ignored and exhausted rows go DEAD."""
    engine = db.create_engine("sqlite://")
    outbox = db.OutboxRepository(engine, max_attempts=2)
    event = events.DomainEvent("signal.created", {"a": 1}, "k", 5)
    with engine.begin() as connection:
        assert outbox.append(connection, "agg-1", "key-1", event)
        assert not outbox.append(connection, "agg-1", "key-1", event)
    pending = outbox.pending()
    assert len(pending) == 1
    assert pending[0].event.payload == {"a": 1}
    assert outbox.mark_failed(pending[0].seq, "timeout") == "PENDING"
    assert outbox.mark_failed(pending[0].seq, "timeout") == "DEAD"
    assert outbox.pending() == []


def test_outbox_rolls_back_with_business_transaction() -> None:
    """An aborted transaction leaves no outbox row."""
    engine = db.create_engine("sqlite://")
    outbox = db.OutboxRepository(engine)
    event = events.DomainEvent("x", {}, "k", 1)
    with pytest.raises(RuntimeError), engine.begin() as connection:
        outbox.append(connection, "agg", "key", event)
        raise RuntimeError("business failure")
    assert outbox.pending() == []


def test_outbox_delivery_marks_row() -> None:
    """Delivered rows leave the pending set."""
    engine = db.create_engine("sqlite://")
    outbox = db.OutboxRepository(engine)
    with engine.begin() as connection:
        outbox.append(
            connection, "agg", "key", events.DomainEvent("x", {}, "k", 1)
        )
    outbox.mark_delivered(outbox.pending()[0].seq)
    assert outbox.pending() == []


def test_inbox_deduplicates_per_consumer() -> None:
    """Each consumer processes a key once."""
    inbox = db.InboxRepository(db.create_engine("sqlite://"))
    assert inbox.try_mark("alerts", "k1", 1)
    assert not inbox.try_mark("alerts", "k1", 2)
    assert inbox.try_mark("forwarder", "k1", 3)
    assert inbox.processed("alerts") == ["k1"]


def test_file_database_creates_parent_directory(tmp_path: object) -> None:
    """File-based SQLite URLs create their directory."""
    path = f"{tmp_path}/nested/market.db"
    engine = db.create_engine(f"sqlite:///{path}")
    assert engine.url.database == path


def test_manual_clock_never_goes_backwards() -> None:
    """The manual clock rejects negative steps."""
    with pytest.raises(ValueError):
        clock_lib.ManualClock(5).advance(-1)
