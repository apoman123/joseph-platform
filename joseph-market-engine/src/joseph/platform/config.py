"""Environment-driven configuration for the market engine.

All settings come from ``JOSEPH_*`` environment variables so the same build
runs in development, CI and production. Secrets (dev tokens, service token)
are never logged; ``Settings.__repr__`` redacts them.
"""

from __future__ import annotations

import dataclasses
import os
from collections.abc import Mapping

from joseph.domain import identity
from joseph.domain import instruments

_DEFAULT_CORS = "http://localhost:3000,http://127.0.0.1:3000"
_ALL_VENUES = ",".join(venue.value for venue in instruments.Venue)


@dataclasses.dataclass(frozen=True, slots=True)
class DevToken:
    """Static bearer token mapped to a principal (development only).

    Attributes:
        token: Opaque bearer token text.
        tenant_id: Tenant the token authenticates as.
        user_id: User the token authenticates as.
    """

    token: str
    tenant_id: str
    user_id: str

    def __repr__(self) -> str:
        """Returns a representation with the token redacted."""
        return (
            f"DevToken(token='***', tenant_id={self.tenant_id!r}, "
            f"user_id={self.user_id!r})"
        )

    def principal(self) -> identity.Principal:
        """Builds the principal this token authenticates.

        Returns:
            Principal with read/write strategy scopes.
        """
        return identity.Principal(
            tenant_id=self.tenant_id,
            user_id=self.user_id,
            scopes=frozenset(
                {"market:read", "strategy:read", "strategy:write"}
            ),
        )


def parse_dev_tokens(raw: str) -> tuple[DevToken, ...]:
    """Parses ``token:tenant:user`` entries separated by commas.

    Args:
        raw: Value of ``JOSEPH_DEV_TOKENS``.

    Returns:
        Parsed tokens; malformed entries raise.

    Raises:
        ValueError: If an entry does not have exactly three parts.
    """
    tokens: list[DevToken] = []
    for entry in (part.strip() for part in raw.split(",")):
        if not entry:
            continue
        pieces = entry.split(":")
        if len(pieces) != 3 or not all(pieces):
            raise ValueError(
                "JOSEPH_DEV_TOKENS entries must be token:tenant:user"
            )
        tokens.append(DevToken(pieces[0], pieces[1], pieces[2]))
    return tuple(tokens)


def _bool(value: str | None, default: bool) -> bool:
    """Parses a boolean environment value.

    Args:
        value: Raw value or ``None``.
        default: Value used when unset.

    Returns:
        Parsed boolean.
    """
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


_ALPACA_FEEDS = frozenset({"iex", "sip", "delayed_sip"})


def _alpaca_feed(value: str) -> str:
    """Validates ``JOSEPH_ALPACA_FEED``.

    Args:
        value: Raw value.

    Returns:
        ``iex``, ``sip`` or ``delayed_sip``.

    Raises:
        ValueError: For another value.
    """
    feed = value.strip().lower() or "iex"
    if feed not in _ALPACA_FEEDS:
        raise ValueError("JOSEPH_ALPACA_FEED must be iex, sip or delayed_sip")
    return feed


@dataclasses.dataclass(frozen=True)
class Settings:
    """Market-engine settings.

    Attributes:
        dev_tokens: Development bearer tokens (empty disables user APIs).
        service_token: Token for service-to-service calls.
        execution_url: Base URL of the execution engine.
        cors_origins: Allowed browser origins.
        database_url: SQLAlchemy URL for durable state.
        enabled_venues: Venues whose adapters are constructed.
        demo_only: When True only the mock venue is enabled.
        replay_dir: Directory of JSONL candle files for the replay adapter.
        http_timeout_s: Venue HTTP timeout in seconds.
        poll_interval_s: REST polling interval for feeds.
        snapshot_stale_after_ms: Age at which snapshots are flagged stale.
        stream_retention: Envelopes kept per stream for resume.
        top_n: Binance qualification preselection size.
        coingecko_enabled: Whether trending heat bonuses are fetched.
        pipeline_enabled: Whether the live signal pipeline runs.
        pipeline_interval_s: Seconds between live pipeline passes.
        pipeline_max_instruments: Instruments per venue for universes
            without explicit instruments.
        forward_signals: Whether entry signals of PAPER/SHADOW/AUTO_LIVE
            strategies are forwarded to the execution engine.
        alert_interval_s: Seconds between alert outbox passes.
        max_anchored_bars: Hard cap of chart bars an anchored study (anchored
            VWAP, anchored regression) computes from its anchor to now.
        telegram_bot_token: Telegram bot token (secret).
        line_channel_token: LINE Messaging API channel token (secret).
        webhook_secret: HMAC secret for outbound webhooks (secret).
        smtp_host: SMTP host for e-mail alerts.
        smtp_port: SMTP port.
        smtp_from: Sender address.
        smtp_username: SMTP username.
        smtp_password: SMTP password (secret).
        alpaca_key_id: Alpaca API key id for Nasdaq/NYSE data (secret).
        alpaca_secret_key: Alpaca API secret (secret).
        alpaca_feed: Alpaca stock feed: ``iex``, ``sip`` or
            ``delayed_sip``.
        alpaca_seconds_history_ms: Longest seconds-bar history built on
            demand from Alpaca trades and quotes.
        trade_recording: Whether public trades are recorded (seconds
            history across restarts, true delta on covered bars).
        trade_bar_retention_days: Days of recorded trades kept.
        trade_record_instruments: Instruments recorded permanently.
        trade_record_max_instruments: Instruments recorded live at once.
        trade_backfill_max_calls: REST trade-history calls per backfill.
        trade_archive_url: SQLAlchemy URL of the trade archive (defaults to
            ``database_url``).
        binance_archive: Whether Binance seconds history older than the
            REST window is imported from data.binance.vision.
        binance_archive_dir: Cache directory of verified archive files.
        bitget_archive: Whether Bitget seconds history is imported from
            its public trade archive (verified against 1D klines).
        bitget_archive_dir: Cache directory of downloaded Bitget parts.
    """

    dev_tokens: tuple[DevToken, ...] = ()
    service_token: str | None = None
    execution_url: str = "http://localhost:8100"
    cors_origins: tuple[str, ...] = tuple(_DEFAULT_CORS.split(","))
    database_url: str = "sqlite:///./var/market.db"
    enabled_venues: tuple[instruments.Venue, ...] = tuple(instruments.Venue)
    demo_only: bool = False
    replay_dir: str | None = None
    http_timeout_s: float = 10.0
    poll_interval_s: float = 5.0
    snapshot_stale_after_ms: int = 30_000
    stream_retention: int = 2_000
    top_n: int = 50
    coingecko_enabled: bool = False
    pipeline_enabled: bool = True
    pipeline_interval_s: float = 10.0
    pipeline_max_instruments: int = 10
    forward_signals: bool = True
    alert_interval_s: float = 2.0
    max_anchored_bars: int = 50_000
    telegram_bot_token: str | None = None
    line_channel_token: str | None = None
    webhook_secret: str | None = None
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_from: str | None = None
    smtp_username: str | None = None
    smtp_password: str | None = None
    alpaca_key_id: str | None = None
    alpaca_secret_key: str | None = None
    alpaca_feed: str = "iex"
    alpaca_seconds_history_ms: int = 1_800_000
    trade_recording: bool = True
    trade_bar_retention_days: float = 30.0
    trade_record_instruments: tuple[str, ...] = ()
    trade_record_max_instruments: int = 16
    trade_backfill_max_calls: int = 20
    trade_archive_url: str | None = None
    binance_archive: bool = True
    binance_archive_dir: str = "./var/binance-vision"
    bitget_archive: bool = True
    bitget_archive_dir: str = "./var/bitget-archive"

    def __repr__(self) -> str:
        """Returns a representation with secrets redacted."""
        return (
            f"Settings(dev_tokens=<{len(self.dev_tokens)} redacted>, "
            f"service_token={"'***'" if self.service_token else None}, "
            f"execution_url={self.execution_url!r}, "
            f"cors_origins={self.cors_origins!r}, "
            f"database_url={self.database_url!r}, "
            f"enabled_venues={[v.value for v in self.active_venues]!r}, "
            f"demo_only={self.demo_only!r})"
        )

    @property
    def active_venues(self) -> tuple[instruments.Venue, ...]:
        """Returns the venues to construct, honoring ``demo_only``."""
        if self.demo_only:
            return (instruments.Venue.MOCK,)
        return self.enabled_venues

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        """Builds settings from environment variables.

        Args:
            env: Mapping to read; defaults to ``os.environ``.

        Returns:
            Parsed settings.

        Raises:
            ValueError: If a value is malformed.
        """
        source = os.environ if env is None else env
        venues = tuple(
            instruments.Venue(name.strip().lower())
            for name in source.get("JOSEPH_ENABLED_VENUES", _ALL_VENUES).split(
                ","
            )
            if name.strip()
        )
        return cls(
            dev_tokens=parse_dev_tokens(source.get("JOSEPH_DEV_TOKENS", "")),
            service_token=source.get("JOSEPH_SERVICE_TOKEN") or None,
            execution_url=source.get(
                "JOSEPH_EXECUTION_URL", "http://localhost:8100"
            ).rstrip("/"),
            cors_origins=tuple(
                origin.strip()
                for origin in source.get(
                    "JOSEPH_CORS_ORIGINS", _DEFAULT_CORS
                ).split(",")
                if origin.strip()
            ),
            database_url=source.get(
                "JOSEPH_DATABASE_URL", "sqlite:///./var/market.db"
            ),
            enabled_venues=venues,
            demo_only=_bool(source.get("JOSEPH_DEMO_ONLY"), False),
            replay_dir=source.get("JOSEPH_REPLAY_DIR") or None,
            http_timeout_s=float(source.get("JOSEPH_HTTP_TIMEOUT_S", "10")),
            poll_interval_s=max(
                1.0, float(source.get("JOSEPH_POLL_INTERVAL_S", "5"))
            ),
            snapshot_stale_after_ms=int(
                source.get("JOSEPH_SNAPSHOT_STALE_AFTER_MS", "30000")
            ),
            stream_retention=int(source.get("JOSEPH_STREAM_RETENTION", "2000")),
            top_n=int(source.get("JOSEPH_QUALIFICATION_TOP_N", "50")),
            coingecko_enabled=_bool(
                source.get("JOSEPH_COINGECKO_ENABLED"), False
            ),
            pipeline_enabled=_bool(source.get("JOSEPH_PIPELINE_ENABLED"), True),
            pipeline_interval_s=max(
                1.0, float(source.get("JOSEPH_PIPELINE_INTERVAL_S", "10"))
            ),
            pipeline_max_instruments=int(
                source.get("JOSEPH_PIPELINE_MAX_INSTRUMENTS", "10")
            ),
            forward_signals=_bool(source.get("JOSEPH_FORWARD_SIGNALS"), True),
            alert_interval_s=max(
                0.5, float(source.get("JOSEPH_ALERT_INTERVAL_S", "2"))
            ),
            max_anchored_bars=max(
                2_000, int(source.get("JOSEPH_MAX_ANCHORED_BARS", "50000"))
            ),
            telegram_bot_token=source.get("JOSEPH_TELEGRAM_BOT_TOKEN") or None,
            line_channel_token=source.get("JOSEPH_LINE_CHANNEL_TOKEN") or None,
            webhook_secret=source.get("JOSEPH_WEBHOOK_SECRET") or None,
            smtp_host=source.get("JOSEPH_SMTP_HOST") or None,
            smtp_port=int(source.get("JOSEPH_SMTP_PORT", "587")),
            smtp_from=source.get("JOSEPH_SMTP_FROM") or None,
            smtp_username=source.get("JOSEPH_SMTP_USERNAME") or None,
            smtp_password=source.get("JOSEPH_SMTP_PASSWORD") or None,
            alpaca_key_id=source.get("JOSEPH_ALPACA_KEY_ID") or None,
            alpaca_secret_key=source.get("JOSEPH_ALPACA_SECRET_KEY") or None,
            alpaca_feed=_alpaca_feed(source.get("JOSEPH_ALPACA_FEED", "iex")),
            alpaca_seconds_history_ms=int(
                source.get("JOSEPH_ALPACA_SECONDS_HISTORY_MS", "1800000")
            ),
            trade_recording=_bool(source.get("JOSEPH_TRADE_RECORDING"), True),
            trade_bar_retention_days=max(
                1.0,
                float(source.get("JOSEPH_TRADE_BAR_RETENTION_DAYS", "30")),
            ),
            trade_record_instruments=tuple(
                value.strip()
                for value in source.get(
                    "JOSEPH_TRADE_RECORD_INSTRUMENTS", ""
                ).split(",")
                if value.strip()
            ),
            trade_record_max_instruments=max(
                0, int(source.get("JOSEPH_TRADE_RECORD_MAX_INSTRUMENTS", "16"))
            ),
            trade_backfill_max_calls=max(
                0, int(source.get("JOSEPH_TRADE_BACKFILL_MAX_CALLS", "20"))
            ),
            trade_archive_url=source.get("JOSEPH_TRADE_ARCHIVE_URL") or None,
            binance_archive=_bool(source.get("JOSEPH_BINANCE_ARCHIVE"), True),
            binance_archive_dir=source.get(
                "JOSEPH_BINANCE_ARCHIVE_DIR", "./var/binance-vision"
            ),
            bitget_archive=_bool(source.get("JOSEPH_BITGET_ARCHIVE"), True),
            bitget_archive_dir=source.get(
                "JOSEPH_BITGET_ARCHIVE_DIR", "./var/bitget-archive"
            ),
        )
