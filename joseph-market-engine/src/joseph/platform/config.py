# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Environment-driven configuration for the market engine.

All settings come from ``JOSEPH_*`` environment variables so the same build
runs in development, CI and production; ``Settings`` lists each variable
with its default and unit. Secrets never appear in ``Settings.__repr__``,
which masks the tokens and keys it shows and leaves the others out. API
keys may also come from secret files (``NAME_FILE``, see
``secret_value``) and are registered for log redaction.
"""

from __future__ import annotations

import dataclasses
import os
import pathlib
import re
from collections.abc import Mapping

import dotenv

from joseph.domain import identity
from joseph.domain import instruments
from joseph.platform import redaction

_DEFAULT_CORS = "http://localhost:3000,http://127.0.0.1:3000"
_DEFAULT_VENUES = tuple(
    venue
    for venue in instruments.Venue
    if venue
    not in {
        instruments.Venue.MOCK,
        instruments.Venue.HYPERLIQUID,
        instruments.Venue.LIGHTER,
        instruments.Venue.BITGET,
        instruments.Venue.MEXC,
    }
)
_DEFAULT_VENUE_NAMES = ",".join(venue.value for venue in _DEFAULT_VENUES)
_DATA_FEED_ENV_NAMES = frozenset(
    {
        "JOSEPH_ENABLED_VENUES",
        "JOSEPH_DEMO_ONLY",
        "JOSEPH_ALPACA_KEY_ID",
        "JOSEPH_ALPACA_SECRET_KEY",
        "JOSEPH_ALPACA_FEED",
        "JOSEPH_ALPACA_SECONDS_HISTORY_MS",
        "JOSEPH_TW_DATA_SOURCE",
        "JOSEPH_FUGLE_API_KEY",
        "JOSEPH_FUGLE_ADJUSTED_DAILY",
        "JOSEPH_FRED_API_KEY",
    }
)
_ALERT_ENV_NAMES = frozenset(
    {
        "JOSEPH_TELEGRAM_BOT_TOKEN",
        "JOSEPH_LINE_CHANNEL_TOKEN",
        "JOSEPH_WEBHOOK_SECRET",
        "JOSEPH_SMTP_HOST",
        "JOSEPH_SMTP_PORT",
        "JOSEPH_SMTP_FROM",
        "JOSEPH_SMTP_USERNAME",
        "JOSEPH_SMTP_PASSWORD",
        "JOSEPH_WHATSAPP_ACCESS_TOKEN",
        "JOSEPH_WHATSAPP_PHONE_NUMBER_ID",
        "JOSEPH_WHATSAPP_TEMPLATE",
        "JOSEPH_WHATSAPP_LANGUAGE",
        "JOSEPH_WHATSAPP_API_VERSION",
        "JOSEPH_WEB_PUSH_PRIVATE_KEY",
        "JOSEPH_WEB_PUSH_PUBLIC_KEY",
        "JOSEPH_WEB_PUSH_CONTACT",
        "JOSEPH_SCRIPT_RUNTIME",
        "JOSEPH_SCRIPT_RUNNER_IMAGE",
        "JOSEPH_SCRIPT_TIMEOUT_S",
        "JOSEPH_MCP_PUBLIC_URL",
        "JOSEPH_MCP_SIGNING_KEY",
    }
)


def data_feed_environment(
    source: Mapping[str, str], *, include_alerts: bool = False
) -> Mapping[str, str]:
    """Loads selected data-feed settings from an optional dotenv file.

    Args:
        source: Environment variables. JOSEPH_MARKET_ENV_FILE optionally
            names a UTF-8 dotenv file. Explicit environment entries win,
            including empty values. Variable interpolation is disabled.
        include_alerts: Whether to also load the explicit alert settings
            whitelist. False preserves the data-feed-only reader.

    Returns:
        Merged settings without changing the process environment. Only
        selected settings and their secret-file paths are loaded, so
        execution credentials cannot enter market settings from this file.

    Raises:
        ValueError: If the explicitly configured file cannot be read.
    """
    filename = source.get("JOSEPH_MARKET_ENV_FILE", "").strip()
    if not filename:
        return source
    try:
        with pathlib.Path(filename).open(encoding="utf-8") as stream:
            values = dotenv.dotenv_values(stream=stream, interpolate=False)
    except (OSError, UnicodeError) as exc:
        raise ValueError("JOSEPH_MARKET_ENV_FILE is not readable") from exc
    names = (
        _DATA_FEED_ENV_NAMES | _ALERT_ENV_NAMES
        if include_alerts
        else _DATA_FEED_ENV_NAMES
    )
    selected = {
        key: value
        for key, value in values.items()
        if value is not None
        and (key in names or key.removesuffix("_FILE") in names)
    }
    return {**selected, **source}


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
        Parsed tokens in input order; empty entries are skipped.

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
        default: Value used when unset or empty.

    Returns:
        True for ``1``, ``true``, ``yes`` or ``on`` (any case, surrounding
        spaces ignored); False for any other non-empty value.
    """
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def secret_value(source: Mapping[str, str], name: str) -> str | None:
    """Reads a secret from ``NAME`` or the file named by ``NAME_FILE``.

    ``NAME`` wins when both are set. The value is registered for log
    redaction.

    Args:
        source: Environment mapping.
        name: Variable name, for example ``JOSEPH_SINOPAC_API_KEY``.

    Returns:
        Value (stripped), or ``None`` when unset.

    Raises:
        ValueError: When ``NAME_FILE`` points to an unreadable file (the
            message names the variable, never the value).
    """
    value = (source.get(name) or "").strip()
    path = source.get(f"{name}_FILE")
    if not value and path:
        try:
            value = pathlib.Path(path).read_text(encoding="utf-8").strip()
        except OSError as err:
            raise ValueError(f"{name}_FILE is not readable") from err
    if not value:
        return None
    redaction.register_secret(value)
    return value


TW_DATA_SOURCES = ("twse", "fugle")
"""Values of ``JOSEPH_TW_DATA_SOURCE``."""


def _names(raw: str) -> tuple[str, ...]:
    """Splits a comma-separated list of names.

    Args:
        raw: Variable value.

    Returns:
        The stripped, non-empty names in order.
    """
    return tuple(name.strip() for name in raw.split(",") if name.strip())


def _tw_data_source(value: str) -> str:
    """Validates ``JOSEPH_TW_DATA_SOURCE``.

    Args:
        value: Raw value.

    Returns:
        ``twse`` or ``fugle``.

    Raises:
        ValueError: For another value.
    """
    choice = value.strip().lower() or "twse"
    if choice not in TW_DATA_SOURCES:
        raise ValueError("JOSEPH_TW_DATA_SOURCE must be twse or fugle")
    return choice


def _bounded(
    source: Mapping[str, str], name: str, default: int, low: int, high: int
) -> int:
    """Reads an integer setting clamped into ``[low, high]``.

    Args:
        source: Environment mapping.
        name: Variable name.
        default: Value when unset.
        low: Smallest value.
        high: Largest value.

    Returns:
        Clamped value.

    Raises:
        ValueError: If the value is not an integer.
    """
    return min(high, max(low, int(source.get(name, str(default)))))


_ALPACA_FEEDS = frozenset({"iex"})


def _alpaca_feed(value: str) -> str:
    """Validates ``JOSEPH_ALPACA_FEED``.

    Args:
        value: Raw value.

    Returns:
        ``iex``.

    Raises:
        ValueError: For another value.
    """
    feed = value.strip().lower() or "iex"
    if feed not in _ALPACA_FEEDS:
        raise ValueError("JOSEPH_ALPACA_FEED must be iex")
    return feed


_MEXC_WS_FORMATS = frozenset({"protobuf", "json"})


def _mexc_ws_format(value: str) -> str:
    """Validates ``JOSEPH_MEXC_WS_FORMAT``.

    Args:
        value: Raw value.

    Returns:
        ``protobuf`` (default) or ``json``.

    Raises:
        ValueError: For another value.
    """
    fmt = value.strip().lower() or "protobuf"
    if fmt not in _MEXC_WS_FORMATS:
        raise ValueError("JOSEPH_MEXC_WS_FORMAT must be protobuf or json")
    return fmt


@dataclasses.dataclass(frozen=True)
class Settings:
    """Market-engine settings.

    ``from_env`` reads each field from the environment variable named in
    its entry below; an unset variable gives the stated default. Values
    marked "at least" or "clamped" are adjusted into range by ``from_env``
    only; constructing ``Settings`` directly applies no bounds. Lists are
    comma-separated, and booleans are true for ``1``, ``true``, ``yes`` or
    ``on`` (any case) and false for any other non-empty value.

    Attributes:
        dev_tokens: Development bearer tokens, ``JOSEPH_DEV_TOKENS``, as
            ``token:tenant:user`` entries. Default none, which makes every
            protected route answer 401.
        service_token: Bearer token sent to the execution engine when
            forwarding signals, ``JOSEPH_SERVICE_TOKEN``. Default none,
            which disables forwarding.
        execution_url: Base URL of the execution engine,
            ``JOSEPH_EXECUTION_URL``; a trailing ``/`` is removed. Default
            ``http://localhost:8100``.
        cors_origins: Allowed browser origins, ``JOSEPH_CORS_ORIGINS``.
            Default ``http://localhost:3000`` and
            ``http://127.0.0.1:3000``.
        database_url: SQLAlchemy URL for durable state,
            ``JOSEPH_DATABASE_URL``. Default ``sqlite:///./var/market.db``.
        enabled_venues: Venues whose adapters are constructed,
            ``JOSEPH_ENABLED_VENUES`` (contract venue ids, any case). The
            synthetic ``mock`` venue is reserved for ``demo_only``.
            Default every venue.
        demo_only: When true only the mock venue is enabled,
            ``JOSEPH_DEMO_ONLY``. Default false.
        replay_dir: Directory of JSONL candle files for the replay adapter,
            ``JOSEPH_REPLAY_DIR``. Default none (no replay source).
        http_timeout_s: Timeout of venue and outbound HTTP requests, in
            seconds, ``JOSEPH_HTTP_TIMEOUT_S``. Default 10.
        poll_interval_s: REST polling interval of polled feeds, in
            seconds, ``JOSEPH_POLL_INTERVAL_S``. Default 5, at least 1.
        snapshot_stale_after_ms: Age at which snapshots and feeds are
            flagged stale, in milliseconds,
            ``JOSEPH_SNAPSHOT_STALE_AFTER_MS``. Default 30000.
        stream_retention: Envelopes the stream hub keeps, across all
            channels, for clients resuming with ``last_sequence``,
            ``JOSEPH_STREAM_RETENTION``. Default 2000.
        strategy_activations_enabled: Whether active Python strategy
            activations are evaluated on closed bars,
            ``JOSEPH_STRATEGY_ACTIVATIONS_ENABLED``. Default true.
        strategy_interval_s: Time between strategy activation passes, in
            seconds, ``JOSEPH_STRATEGY_INTERVAL_S``. Default 10, at least 1.
        forward_signals: Whether entry signals of PAPER/SHADOW/AUTO_LIVE
            strategies are forwarded to the execution engine (also needs
            ``service_token``), ``JOSEPH_FORWARD_SIGNALS``. Default true.
        alert_interval_s: Time between alert outbox passes, in seconds,
            ``JOSEPH_ALERT_INTERVAL_S``. Default 2, at least 0.5.
        max_anchored_bars: Hard cap of chart bars an anchored study
            (anchored VWAP, anchored regression) computes from its anchor
            to now, ``JOSEPH_MAX_ANCHORED_BARS``. Default 50000, at least
            2000.
        indicator_plugin_packages: Indicator plugin packages to load,
            ``JOSEPH_INDICATOR_PLUGIN_PACKAGES``: ``*`` for every installed
            entry point of the ``joseph.indicator_plugins`` group, ``none``
            for none, or comma-separated entry-point names. Default ``*``.
            The built-in indicators always load.
        indicator_plugins_disabled: Indicator plugin ids to register but
            disable, ``JOSEPH_INDICATOR_PLUGINS_DISABLED`` (built-ins
            included); strategies that use them report the plugin as
            unavailable. Default none.
        indicator_plugins_required: Indicator plugin ids that must load and
            stay enabled, else startup fails,
            ``JOSEPH_INDICATOR_PLUGINS_REQUIRED``. Default none.
        script_plugin_packages: Script plugin packages to load,
            ``JOSEPH_SCRIPT_PLUGIN_PACKAGES``: ``*`` for every installed
            entry point of the ``joseph.scripts`` group, ``none`` for none,
            or comma-separated entry-point names. Default ``*``. The
            built-in scripts always load.
        script_plugins_disabled: Catalog script ids listed but not
            runnable, ``JOSEPH_SCRIPT_PLUGINS_DISABLED`` (built-ins
            included). Default none.
        script_plugins_required: Catalog script ids that must load and stay
            enabled, else startup fails, ``JOSEPH_SCRIPT_PLUGINS_REQUIRED``.
            Default none.
        telegram_bot_token: Telegram bot token (secret),
            ``JOSEPH_TELEGRAM_BOT_TOKEN``. Default none (channel off).
        script_runtime: User Python execution mode, disabled or docker;
            JOSEPH_SCRIPT_RUNTIME, default disabled.
        script_runner_image: Operator-owned isolated runtime image;
            JOSEPH_SCRIPT_RUNNER_IMAGE, default joseph-python-runner:1.
        script_timeout_s: Per-container wall-clock limit, one to thirty seconds;
            JOSEPH_SCRIPT_TIMEOUT_S, default ten seconds.
        mcp_public_url: Canonical audience and public MCP URL,
            JOSEPH_MCP_PUBLIC_URL, default http://localhost:8000/mcp.
        mcp_signing_key: Optional operator-managed secret signing key;
            JOSEPH_MCP_SIGNING_KEY, otherwise generated and persisted privately.
        whatsapp_access_token: Secret Meta Business token; default none.
            Environment variable JOSEPH_WHATSAPP_ACCESS_TOKEN.
        whatsapp_phone_number_id: Numeric business sender id; default none.
            Environment variable JOSEPH_WHATSAPP_PHONE_NUMBER_ID.
        whatsapp_template: Approved one-body-parameter template name;
            JOSEPH_WHATSAPP_TEMPLATE, default none.
        whatsapp_language: Approved locale; JOSEPH_WHATSAPP_LANGUAGE, en_US.
        whatsapp_api_version: Graph API version;
            JOSEPH_WHATSAPP_API_VERSION, default v27.0.
        web_push_private_key: Secret base64url raw P-256 private key;
            JOSEPH_WEB_PUSH_PRIVATE_KEY, default none.
        web_push_public_key: Matching public application server key;
            JOSEPH_WEB_PUSH_PUBLIC_KEY, default none.
        web_push_contact: VAPID mailto or HTTPS contact;
            JOSEPH_WEB_PUSH_CONTACT, default none.
        line_channel_token: LINE Messaging API channel token (secret),
            ``JOSEPH_LINE_CHANNEL_TOKEN``. Default none (channel off).
        webhook_secret: HMAC secret for outbound webhooks (secret),
            ``JOSEPH_WEBHOOK_SECRET``. Default none (channel off).
        smtp_host: SMTP host for e-mail alerts, ``JOSEPH_SMTP_HOST``.
            Default none; e-mail is off unless both this and ``smtp_from``
            are set.
        smtp_port: SMTP port, ``JOSEPH_SMTP_PORT``. Default 587.
        smtp_from: Sender address, ``JOSEPH_SMTP_FROM``. Default none.
        smtp_username: SMTP username, ``JOSEPH_SMTP_USERNAME``. Default
            none.
        smtp_password: SMTP password (secret), ``JOSEPH_SMTP_PASSWORD``.
            Default none.
        alpaca_key_id: Alpaca API key id for US exchange data (secret),
            ``JOSEPH_ALPACA_KEY_ID``. Default none.
        alpaca_secret_key: Alpaca API secret (secret),
            ``JOSEPH_ALPACA_SECRET_KEY``. Default none.
        alpaca_feed: Alpaca stock feed, ``iex`` only,
            ``JOSEPH_ALPACA_FEED``. Default ``iex``.
        alpaca_seconds_history_ms: Longest seconds-bar history built on
            demand from Alpaca trades and quotes, in milliseconds,
            ``JOSEPH_ALPACA_SECONDS_HISTORY_MS``. Default 1800000 (30
            minutes).
        trade_recording: Whether public trades are recorded (seconds
            history across restarts, true delta on covered bars),
            ``JOSEPH_TRADE_RECORDING``. Default true.
        trade_bar_retention_days: Recorded trades kept, in days (fractions
            allowed), ``JOSEPH_TRADE_BAR_RETENTION_DAYS``. Default 30, at
            least 1.
        trade_record_instruments: Canonical ids of instruments recorded
            permanently, ``JOSEPH_TRADE_RECORD_INSTRUMENTS``. Default none.
        trade_record_max_instruments: Instruments recorded live at once,
            ``JOSEPH_TRADE_RECORD_MAX_INSTRUMENTS``. Default 16, at least
            0.
        trade_backfill_max_calls: REST trade-history calls per backfill,
            ``JOSEPH_TRADE_BACKFILL_MAX_CALLS``. Default 20, at least 0.
        trade_archive_url: SQLAlchemy URL of the trade archive,
            ``JOSEPH_TRADE_ARCHIVE_URL``. Default none, which stores it in
            ``database_url``.
        binance_archive: Whether Binance seconds history older than the
            REST window is imported from data.binance.vision,
            ``JOSEPH_BINANCE_ARCHIVE``. Default true.
        binance_archive_dir: Cache directory of verified archive files,
            ``JOSEPH_BINANCE_ARCHIVE_DIR``. Default
            ``./var/binance-vision``.
        bitget_archive: Whether Bitget seconds history is imported from
            its public trade archive (verified against 1D klines),
            ``JOSEPH_BITGET_ARCHIVE``. Default true.
        bitget_archive_dir: Cache directory of downloaded Bitget parts,
            ``JOSEPH_BITGET_ARCHIVE_DIR``. Default ``./var/bitget-archive``.
        mexc_ws_format: MEXC futures WebSocket push format, ``protobuf``
            or ``json`` (any case), ``JOSEPH_MEXC_WS_FORMAT``. Default
            ``protobuf``; MEXC's deadline for migrating off JSON market
            data is 2026-12-01 10:00 UTC.
        lighter_archive: Whether Lighter seconds history is imported from
            the AWS Public Blockchain Data trade archive (remote range
            reads, verified against 1d candles, nothing cached on disk),
            ``JOSEPH_LIGHTER_ARCHIVE``. Default true.
        tw_data_source: Taiwan source selection. The active market engine
            enables only ``fugle`` with a configured key; ``twse`` is a
            legacy setting that leaves Taiwan data disabled. Default
            ``fugle``.
        fugle_adjusted_daily: Normalize every historical Fugle interval;
            ``JOSEPH_FUGLE_ADJUSTED_DAILY`` defaults to true.
            Current quotes and order prices stay native.
        fugle_api_key: Fugle market-data API key (secret),
            ``JOSEPH_FUGLE_API_KEY`` or its ``_FILE`` path. Required when
            ``tw_data_source`` is ``fugle``.
        fred_api_key: Optional FRED API key (secret),
            ``JOSEPH_FRED_API_KEY`` or its ``_FILE`` path. Enables distinct
            daily FRED rate instruments when present.
    """

    dev_tokens: tuple[DevToken, ...] = ()
    service_token: str | None = None
    execution_url: str = "http://localhost:8100"
    cors_origins: tuple[str, ...] = tuple(_DEFAULT_CORS.split(","))
    database_url: str = "sqlite:///./var/market.db"
    enabled_venues: tuple[instruments.Venue, ...] = _DEFAULT_VENUES
    demo_only: bool = False
    replay_dir: str | None = None
    http_timeout_s: float = 10.0
    poll_interval_s: float = 5.0
    snapshot_stale_after_ms: int = 30_000
    stream_retention: int = 2_000
    strategy_activations_enabled: bool = True
    strategy_interval_s: float = 10.0
    forward_signals: bool = True
    alert_interval_s: float = 2.0
    max_anchored_bars: int = 50_000
    indicator_plugin_packages: str = "*"
    indicator_plugins_disabled: tuple[str, ...] = ()
    indicator_plugins_required: tuple[str, ...] = ()
    script_plugin_packages: str = "*"
    script_plugins_disabled: tuple[str, ...] = ()
    script_plugins_required: tuple[str, ...] = ()
    telegram_bot_token: str | None = None
    script_runtime: str = "disabled"
    script_runner_image: str = "joseph-python-runner:1"
    script_timeout_s: float = 10.0
    mcp_public_url: str = "http://localhost:8000/mcp"
    mcp_signing_key: str | None = None
    whatsapp_access_token: str | None = None
    whatsapp_phone_number_id: str | None = None
    whatsapp_template: str | None = None
    whatsapp_language: str = "en_US"
    whatsapp_api_version: str = "v27.0"
    web_push_private_key: str | None = None
    web_push_public_key: str | None = None
    web_push_contact: str | None = None
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
    mexc_ws_format: str = "protobuf"
    lighter_archive: bool = True
    tw_data_source: str = "fugle"
    fugle_api_key: str | None = None
    fugle_adjusted_daily: bool = True
    fred_api_key: str | None = None

    def __repr__(self) -> str:
        """Returns a representation with secrets redacted."""
        return (
            f"Settings(dev_tokens=<{len(self.dev_tokens)} redacted>, "
            f"service_token={"'***'" if self.service_token else None}, "
            f"execution_url={self.execution_url!r}, "
            f"cors_origins={self.cors_origins!r}, "
            f"database_url={self.database_url!r}, "
            f"enabled_venues={[v.value for v in self.active_venues]!r}, "
            f"demo_only={self.demo_only!r}, "
            f"indicator_plugin_packages={self.indicator_plugin_packages!r}, "
            f"tw_data_source={self.tw_data_source!r}, "
            f"fugle_api_key={"'***'" if self.fugle_api_key else None}, "
            f"fred_api_key={"'***'" if self.fred_api_key else None})"
        )

    @property
    def active_venues(self) -> tuple[instruments.Venue, ...]:
        """The configured data venues with unavailable key feeds removed.

        Returns:
            Market-data venues to construct. US and Taiwan venues require
            their respective free-plan keys; legacy ``mock`` and TWSE/TPEx
            official-source entries are ignored outside demo mode.
        """
        if self.demo_only:
            return (instruments.Venue.MOCK,)
        return tuple(
            venue
            for venue in self.enabled_venues
            if venue is not instruments.Venue.MOCK
            and (
                venue not in (instruments.Venue.TWSE, instruments.Venue.TPEX)
                or (self.tw_data_source == "fugle" and bool(self.fugle_api_key))
            )
            and (
                venue
                not in (
                    instruments.Venue.NASDAQ,
                    instruments.Venue.NYSE,
                    instruments.Venue.CBOE,
                )
                or bool(self.alpaca_key_id and self.alpaca_secret_key)
            )
        )

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        """Builds settings from the environment and optional data-feed file.

        Args:
            env: Mapping to read; defaults to ``os.environ``. An optional
                JOSEPH_MARKET_ENV_FILE supplies data-feed defaults only.

        Returns:
            Parsed settings.

        Raises:
            ValueError: If a value is malformed: an unknown venue or
                choice, a number that does not parse, a bad dev token
                entry or an unreadable secret or market environment file.
        """
        source = data_feed_environment(
            os.environ if env is None else env, include_alerts=True
        )
        phone_id = source.get("JOSEPH_WHATSAPP_PHONE_NUMBER_ID") or None
        version = source.get("JOSEPH_WHATSAPP_API_VERSION", "v27.0")
        template = source.get("JOSEPH_WHATSAPP_TEMPLATE") or None
        script_runtime = source.get("JOSEPH_SCRIPT_RUNTIME", "disabled")
        if script_runtime not in {"disabled", "docker"}:
            raise ValueError("JOSEPH_SCRIPT_RUNTIME must be disabled or docker")
        if (phone_id and not phone_id.isascii()) or (
            phone_id and not phone_id.isdigit()
        ):
            raise ValueError(
                "JOSEPH_WHATSAPP_PHONE_NUMBER_ID must contain ASCII digits"
            )
        if not re.fullmatch(r"v[0-9]{1,2}\.[0-9]{1,2}", version):
            raise ValueError("invalid JOSEPH_WHATSAPP_API_VERSION")
        if template and not re.fullmatch(r"[a-z][a-z0-9_]{0,511}", template):
            raise ValueError("invalid JOSEPH_WHATSAPP_TEMPLATE")
        venues = tuple(
            instruments.Venue(name.strip().lower())
            for name in source.get(
                "JOSEPH_ENABLED_VENUES", _DEFAULT_VENUE_NAMES
            ).split(",")
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
            strategy_activations_enabled=_bool(
                source.get("JOSEPH_STRATEGY_ACTIVATIONS_ENABLED"), True
            ),
            strategy_interval_s=max(
                1.0, float(source.get("JOSEPH_STRATEGY_INTERVAL_S", "10"))
            ),
            forward_signals=_bool(source.get("JOSEPH_FORWARD_SIGNALS"), True),
            alert_interval_s=max(
                0.5, float(source.get("JOSEPH_ALERT_INTERVAL_S", "2"))
            ),
            max_anchored_bars=max(
                2_000, int(source.get("JOSEPH_MAX_ANCHORED_BARS", "50000"))
            ),
            indicator_plugin_packages=(
                source.get("JOSEPH_INDICATOR_PLUGIN_PACKAGES", "").strip()
                or "*"
            ),
            indicator_plugins_disabled=_names(
                source.get("JOSEPH_INDICATOR_PLUGINS_DISABLED", "")
            ),
            indicator_plugins_required=_names(
                source.get("JOSEPH_INDICATOR_PLUGINS_REQUIRED", "")
            ),
            script_plugin_packages=(
                source.get("JOSEPH_SCRIPT_PLUGIN_PACKAGES", "").strip() or "*"
            ),
            script_plugins_disabled=_names(
                source.get("JOSEPH_SCRIPT_PLUGINS_DISABLED", "")
            ),
            script_plugins_required=_names(
                source.get("JOSEPH_SCRIPT_PLUGINS_REQUIRED", "")
            ),
            telegram_bot_token=secret_value(
                source, "JOSEPH_TELEGRAM_BOT_TOKEN"
            ),
            script_runtime=script_runtime,
            script_runner_image=source.get(
                "JOSEPH_SCRIPT_RUNNER_IMAGE", "joseph-python-runner:1"
            ),
            script_timeout_s=float(source.get("JOSEPH_SCRIPT_TIMEOUT_S", "10")),
            mcp_public_url=source.get(
                "JOSEPH_MCP_PUBLIC_URL", "http://localhost:8000/mcp"
            ),
            mcp_signing_key=secret_value(source, "JOSEPH_MCP_SIGNING_KEY"),
            whatsapp_access_token=secret_value(
                source, "JOSEPH_WHATSAPP_ACCESS_TOKEN"
            ),
            whatsapp_phone_number_id=phone_id,
            whatsapp_template=template,
            whatsapp_language=source.get("JOSEPH_WHATSAPP_LANGUAGE", "en_US"),
            whatsapp_api_version=version,
            web_push_private_key=secret_value(
                source, "JOSEPH_WEB_PUSH_PRIVATE_KEY"
            ),
            web_push_public_key=source.get("JOSEPH_WEB_PUSH_PUBLIC_KEY")
            or None,
            web_push_contact=source.get("JOSEPH_WEB_PUSH_CONTACT") or None,
            line_channel_token=secret_value(
                source, "JOSEPH_LINE_CHANNEL_TOKEN"
            ),
            webhook_secret=secret_value(source, "JOSEPH_WEBHOOK_SECRET"),
            smtp_host=source.get("JOSEPH_SMTP_HOST") or None,
            smtp_port=int(source.get("JOSEPH_SMTP_PORT", "587")),
            smtp_from=source.get("JOSEPH_SMTP_FROM") or None,
            smtp_username=source.get("JOSEPH_SMTP_USERNAME") or None,
            smtp_password=secret_value(source, "JOSEPH_SMTP_PASSWORD"),
            alpaca_key_id=secret_value(source, "JOSEPH_ALPACA_KEY_ID"),
            alpaca_secret_key=secret_value(source, "JOSEPH_ALPACA_SECRET_KEY"),
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
            mexc_ws_format=_mexc_ws_format(
                source.get("JOSEPH_MEXC_WS_FORMAT", "protobuf")
            ),
            lighter_archive=_bool(source.get("JOSEPH_LIGHTER_ARCHIVE"), True),
            tw_data_source=_tw_data_source(
                source.get("JOSEPH_TW_DATA_SOURCE", "fugle")
            ),
            fugle_api_key=secret_value(source, "JOSEPH_FUGLE_API_KEY"),
            fugle_adjusted_daily=_bool(
                source.get("JOSEPH_FUGLE_ADJUSTED_DAILY"), True
            ),
            fred_api_key=secret_value(source, "JOSEPH_FRED_API_KEY"),
        )
