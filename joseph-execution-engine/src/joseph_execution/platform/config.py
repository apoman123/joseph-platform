# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Environment-driven settings.

Every setting is read from a ``JOSEPH_*`` environment variable by
``from_env``; the attribute docs below name each variable, its default and
its unit. Boolean variables are true for ``1``, ``true``, ``yes`` or ``on``
(any case) and false for anything else.

Safety defaults: LIVE trading is off. The built-in dev bearer token and
service token exist only when ``JOSEPH_ENV`` is ``development``; any other
environment needs ``JOSEPH_VAULT_MASTER_KEY`` and gets no default tokens
(``JOSEPH_DEV_TOKENS`` is still honoured when set). Step-up verification
fails, so no AUTO_LIVE consent or LIVE arming can be granted, unless
``JOSEPH_STEP_UP_TOTP_SECRET`` is set.
"""

from __future__ import annotations

import base64
import dataclasses
import decimal
import logging
import os
import pathlib
import secrets
from collections.abc import Mapping

from joseph_execution.domain import accounts
from joseph_execution.domain import risk
from joseph_execution.platform import logging as log_lib

_LOG = logging.getLogger(__name__)
_TRUE = ("1", "true", "yes", "on")


# ----------------------------------------------------------------------
# Live venue adapters (crypto venues).
# ----------------------------------------------------------------------

CRYPTO_VENUES = ("binance", "bitget", "hyperliquid", "lighter", "mexc")
"""Crypto venues with live adapters; the only names venue settings accept."""


@dataclasses.dataclass(frozen=True, slots=True)
class VenueSettings:
    """Live venue adapter settings (no secrets: keys live in the vault).

    Attributes:
        sandbox_venues: Venues whose accounts default to the venue's test
            environment (an account's ``environment`` credential field
            overrides it). ``JOSEPH_VENUE_SANDBOX``: comma-separated venue
            names or ``all``; default none (production endpoints).
        timeout_s: Venue HTTP timeout in seconds
            (``JOSEPH_VENUE_TIMEOUT_S``, default 10).
        recv_window_ms: Validity window of signed requests in milliseconds
            (``JOSEPH_VENUE_RECV_WINDOW_MS``, default 5000).
        market_slippage_bps: Worst-price band of market orders in basis
            points (1 bp = 0.01%) on venues that need one (Hyperliquid,
            Lighter) (``JOSEPH_VENUE_MARKET_SLIPPAGE_BPS``, default 50).
        urls: Endpoint overrides per venue (``rest_url``,
            ``testnet_rest_url``, ``ws_url`` …). ``JOSEPH_VENUE_URLS``:
            comma-separated ``venue.key=url`` entries; default none.
        streams: Run private account streams for live accounts; they also
            need LIVE trading enabled (``JOSEPH_LIVE_STREAMS``, default
            true).
    """

    sandbox_venues: tuple[str, ...] = ()
    timeout_s: float = 10.0
    recv_window_ms: int = 5_000
    market_slippage_bps: int = 50
    urls: Mapping[str, Mapping[str, str]] = dataclasses.field(
        default_factory=dict
    )
    streams: bool = True


def parse_venue_urls(raw: str) -> dict[str, dict[str, str]]:
    """Parses ``venue.key=url`` overrides separated by commas.

    Args:
        raw: Configuration text, e.g.
            ``binance.testnet_rest_url=https://testnet.binancefuture.com``.

    Returns:
        Overrides by venue; empty for empty text.

    Raises:
        ValueError: For malformed entries or unknown venues.
    """
    result: dict[str, dict[str, str]] = {}
    for entry in filter(None, (e.strip() for e in raw.split(","))):
        name, _, url = entry.partition("=")
        venue, _, key = name.partition(".")
        if venue not in CRYPTO_VENUES or not key or not url:
            raise ValueError("JOSEPH_VENUE_URLS entries are venue.key=url")
        result.setdefault(venue, {})[key] = url
    return result


def venue_settings(source: Mapping[str, str]) -> VenueSettings:
    """Reads ``JOSEPH_VENUE_*`` and ``JOSEPH_LIVE_STREAMS``.

    Args:
        source: Environment.

    Returns:
        Venue settings.

    Raises:
        ValueError: For unknown sandbox venues, bad numbers or malformed
            ``JOSEPH_VENUE_URLS`` entries.
    """
    raw = source.get("JOSEPH_VENUE_SANDBOX", "").strip().lower()
    venues = (
        CRYPTO_VENUES
        if raw == "all"
        else tuple(v.strip() for v in raw.split(",") if v.strip())
    )
    unknown = sorted(set(venues) - set(CRYPTO_VENUES))
    if unknown:
        raise ValueError(f"JOSEPH_VENUE_SANDBOX: unknown venues {unknown}")
    return VenueSettings(
        sandbox_venues=venues,
        timeout_s=float(source.get("JOSEPH_VENUE_TIMEOUT_S", "10")),
        recv_window_ms=int(source.get("JOSEPH_VENUE_RECV_WINDOW_MS", "5000")),
        market_slippage_bps=int(
            source.get("JOSEPH_VENUE_MARKET_SLIPPAGE_BPS", "50")
        ),
        urls=parse_venue_urls(source.get("JOSEPH_VENUE_URLS", "")),
        streams=source.get("JOSEPH_LIVE_STREAMS", "true").lower() in _TRUE,
    )


# ---------------------------------------------------------------------------
# Securities brokers (Interactive Brokers, SinoPac, Cathay)
# ---------------------------------------------------------------------------

# IB Gateway (4001/4002), its container relay (4003/4004) and TWS
# (7496/7497) API ports, live and paper; JOSEPH_IBKR_PAPER must agree with
# the port so that a paper deployment cannot reach a live login.
_IB_LIVE_PORTS = frozenset({4001, 4003, 7496})
_IB_PAPER_PORTS = frozenset({4002, 4004, 7497})
_CLOSED_MARKET_POLICIES = ("reject", "queue")


@dataclasses.dataclass(frozen=True, slots=True)
class SecuritiesSettings:
    """Securities trading, broker connections and the TWD paper account.

    Broker secrets come only from the environment or ``*_FILE`` secret
    files and are registered for log redaction; ``repr`` never shows them.
    Defaults below are those of ``securities_from_env``; the dataclass
    defaults match except where noted. Settings marked secret are read
    with ``secret_value`` from the variable or its ``_FILE`` file.

    Attributes:
        closed_market_orders: ``reject`` market orders while the exchange
            is closed, or ``queue`` them as DAY orders for the next open
            where the broker allows it
            (``JOSEPH_EQUITY_CLOSED_MARKET_ORDERS``, default ``reject``).
        paper_twd_starting_cash: Starting cash in TWD of the TWD paper
            account (TWSE and TPEx); ``None`` disables it
            (``JOSEPH_PAPER_TWD_STARTING_CASH``, default 3000000; empty,
            ``0``, ``off`` or ``none`` disable it; the dataclass default is
            ``None``).
        twd_limit_factor: Multiplier of the USD-sized money risk limits
            (defaults and hard caps) for TWD accounts
            (``JOSEPH_TWD_LIMIT_FACTOR``, default 30); a sizing
            convention, not an exchange rate.
        ibkr_enabled: Connect to IB Gateway/TWS (``JOSEPH_IBKR_ENABLED``,
            default false).
        ibkr_host: Gateway host (``JOSEPH_IBKR_HOST``, default
            ``127.0.0.1``).
        ibkr_port: Gateway API port (paper 4002 / live 4001; the container
            relays 4004 / 4003) (``JOSEPH_IBKR_PORT``, default 4002).
        ibkr_client_id: API client id (``JOSEPH_IBKR_CLIENT_ID``, default
            17).
        ibkr_paper: The gateway is logged in to the IBKR paper account
            (``JOSEPH_IBKR_PAPER``, default true); must match the port.
        ibkr_short_selling: The IBKR account may short (margin account)
            (``JOSEPH_IBKR_SHORT_SELLING``, default false).
        ibkr_readonly: Connect read-only (``JOSEPH_IBKR_READONLY``, default
            false).
        sinopac_enabled: Log in to Shioaji (``JOSEPH_SINOPAC_ENABLED``,
            default false).
        sinopac_simulation: Shioaji simulation mode
            (``JOSEPH_SINOPAC_SIMULATION``, default true).
        sinopac_api_key: API key (secret ``JOSEPH_SINOPAC_API_KEY``; no
            default).
        sinopac_secret_key: Secret key (secret
            ``JOSEPH_SINOPAC_SECRET_KEY``; no default).
        sinopac_ca_path: Path of the CA ``.pfx`` certificate
            (``JOSEPH_SINOPAC_CA_PATH``; no default).
        sinopac_ca_password: CA password (secret
            ``JOSEPH_SINOPAC_CA_PASSWORD``; no default).
        sinopac_person_id: National id for CA activation (secret
            ``JOSEPH_SINOPAC_PERSON_ID``; no default).
        broker_resync_interval_ms: Interval of the periodic broker resync
            in milliseconds, checked on every tick
            (``JOSEPH_BROKER_RESYNC_INTERVAL_MS``, default 60000).
    """

    closed_market_orders: str = "reject"
    paper_twd_starting_cash: decimal.Decimal | None = None
    twd_limit_factor: decimal.Decimal = risk.DEFAULT_TWD_LIMIT_FACTOR
    ibkr_enabled: bool = False
    ibkr_host: str = "127.0.0.1"
    ibkr_port: int = 4002
    ibkr_client_id: int = 17
    ibkr_paper: bool = True
    ibkr_short_selling: bool = False
    ibkr_readonly: bool = False
    sinopac_enabled: bool = False
    sinopac_simulation: bool = True
    sinopac_api_key: str | None = None
    sinopac_secret_key: str | None = None
    sinopac_ca_path: str | None = None
    sinopac_ca_password: str | None = None
    sinopac_person_id: str | None = None
    broker_resync_interval_ms: int = 60_000

    @property
    def sinopac_configured(self) -> bool:
        """Whether Shioaji API credentials are present."""
        return bool(self.sinopac_api_key and self.sinopac_secret_key)

    def __repr__(self) -> str:
        """Returns a representation without secrets."""
        return (
            f"SecuritiesSettings(closed_market_orders="
            f"{self.closed_market_orders!r}, ibkr_enabled={self.ibkr_enabled}"
            f", ibkr={self.ibkr_host}:{self.ibkr_port}, ibkr_paper="
            f"{self.ibkr_paper}, sinopac_enabled={self.sinopac_enabled}, "
            f"sinopac_simulation={self.sinopac_simulation}, "
            "secrets=[REDACTED])"
        )


def _flag(source: Mapping[str, str], name: str, default: bool) -> bool:
    """Reads a boolean variable.

    Args:
        source: Environment.
        name: Variable.
        default: Value when the variable is unset.

    Returns:
        True when the variable is set to a true word (``_TRUE``, any case,
        surrounding spaces ignored), False when set to anything else.
    """
    raw = source.get(name)
    return default if raw is None else raw.strip().lower() in _TRUE


def secret_value(source: Mapping[str, str], name: str) -> str | None:
    """Reads a secret from ``NAME`` or the file named by ``NAME_FILE``.

    ``NAME`` wins when both are set; the file content is stripped. The
    value is registered for log redaction.

    Args:
        source: Environment.
        name: Variable.

    Returns:
        Value, or ``None`` when unset or empty.

    Raises:
        ValueError: When ``NAME_FILE`` points to an unreadable file.
    """
    value = source.get(name)
    path = source.get(f"{name}_FILE")
    if not value and path:
        try:
            value = pathlib.Path(path).read_text(encoding="utf-8").strip()
        except OSError as err:
            raise ValueError(f"{name}_FILE is not readable") from err
    if not value:
        return None
    log_lib.SECRETS.add(value)
    return value


def twd_limit_factor(source: Mapping[str, str]) -> decimal.Decimal:
    """Reads ``JOSEPH_TWD_LIMIT_FACTOR`` (fail closed on bad values).

    Args:
        source: Environment.

    Returns:
        Multiplier of USD-sized money limits for TWD accounts (default
        ``risk.DEFAULT_TWD_LIMIT_FACTOR``, 30).

    Raises:
        ValueError: Unless the value is a positive, finite number.
    """
    raw = source.get("JOSEPH_TWD_LIMIT_FACTOR", "").strip()
    if not raw:
        return risk.DEFAULT_TWD_LIMIT_FACTOR
    message = "JOSEPH_TWD_LIMIT_FACTOR must be a positive number"
    try:
        value = decimal.Decimal(raw)
    except decimal.InvalidOperation as err:
        raise ValueError(message) from err
    if not value.is_finite() or value <= 0:
        raise ValueError(message)
    return value


def securities_from_env(source: Mapping[str, str]) -> SecuritiesSettings:
    """Loads the securities settings.

    Args:
        source: Environment.

    Returns:
        Settings.

    Raises:
        ValueError: For invalid values, e.g. an IBKR paper flag that
            contradicts the gateway port, or an unreadable secret file.
    """
    policy = source.get("JOSEPH_EQUITY_CLOSED_MARKET_ORDERS", "reject")
    if policy not in _CLOSED_MARKET_POLICIES:
        raise ValueError(
            "JOSEPH_EQUITY_CLOSED_MARKET_ORDERS must be reject or queue"
        )
    twd_raw = source.get("JOSEPH_PAPER_TWD_STARTING_CASH", "3000000").strip()
    twd = None
    if twd_raw and twd_raw.lower() not in ("0", "off", "none"):
        twd = decimal.Decimal(twd_raw)
    port = int(source.get("JOSEPH_IBKR_PORT", "4002"))
    paper = _flag(source, "JOSEPH_IBKR_PAPER", True)
    if paper and port in _IB_LIVE_PORTS:
        raise ValueError(
            f"JOSEPH_IBKR_PAPER=true but {port} is a live IB Gateway/TWS port"
        )
    if not paper and port in _IB_PAPER_PORTS:
        raise ValueError(
            f"JOSEPH_IBKR_PAPER=false but {port} is a paper IB Gateway/TWS port"
        )
    return SecuritiesSettings(
        closed_market_orders=policy,
        paper_twd_starting_cash=twd,
        twd_limit_factor=twd_limit_factor(source),
        ibkr_enabled=_flag(source, "JOSEPH_IBKR_ENABLED", False),
        ibkr_host=source.get("JOSEPH_IBKR_HOST", "127.0.0.1"),
        ibkr_port=port,
        ibkr_client_id=int(source.get("JOSEPH_IBKR_CLIENT_ID", "17")),
        ibkr_paper=paper,
        ibkr_short_selling=_flag(source, "JOSEPH_IBKR_SHORT_SELLING", False),
        ibkr_readonly=_flag(source, "JOSEPH_IBKR_READONLY", False),
        sinopac_enabled=_flag(source, "JOSEPH_SINOPAC_ENABLED", False),
        sinopac_simulation=_flag(source, "JOSEPH_SINOPAC_SIMULATION", True),
        sinopac_api_key=secret_value(source, "JOSEPH_SINOPAC_API_KEY"),
        sinopac_secret_key=secret_value(source, "JOSEPH_SINOPAC_SECRET_KEY"),
        sinopac_ca_path=source.get("JOSEPH_SINOPAC_CA_PATH") or None,
        sinopac_ca_password=secret_value(source, "JOSEPH_SINOPAC_CA_PASSWORD"),
        sinopac_person_id=secret_value(source, "JOSEPH_SINOPAC_PERSON_ID"),
        broker_resync_interval_ms=int(
            source.get("JOSEPH_BROKER_RESYNC_INTERVAL_MS", "60000")
        ),
    )


@dataclasses.dataclass(frozen=True, slots=True)
class Settings:
    """Execution engine settings.

    Defaults below are those of ``from_env``; the dataclass defaults match
    except where noted (tests build ``Settings`` directly).

    Attributes:
        environment: ``development`` or another name such as
            ``production``; only ``development`` gets dev defaults
            (``JOSEPH_ENV``, default ``development``).
        database_url: SQLAlchemy URL (``JOSEPH_DATABASE_URL``, default
            ``sqlite:///./var/execution.db``).
        market_url: Market engine base URL (``JOSEPH_MARKET_URL``, default
            ``http://localhost:8000``).
        market_gateway: ``static`` (built-in DEMO data) or ``http`` (the
            market engine) (``JOSEPH_MARKET_GATEWAY``, default ``static``).
        dev_tokens: Bearer token to ``(tenant_id, user_id)``.
            ``JOSEPH_DEV_TOKENS``: comma-separated ``token:tenant:user``
            entries; default ``dev-local-token:tenant-01:user-01`` in
            development, none elsewhere (dataclass default: none).
        service_token: Token for ``/internal`` endpoints
            (``JOSEPH_SERVICE_TOKEN``; default ``dev-service-token`` in
            development, ``None`` elsewhere, which disables those
            endpoints; dataclass default ``None``).
        live_trading_enabled: Deployment-wide LIVE gate
            (``JOSEPH_LIVE_TRADING_ENABLED``, default false).
        emergency_halt: Platform-wide halt: every order that is not
            risk-reducing is blocked (``JOSEPH_EMERGENCY_HALT``, default
            false).
        vault_master_key: 32-byte key-encryption key of the credential
            vault (``JOSEPH_VAULT_MASTER_KEY``, base64; required outside
            development, where an unset key becomes a random per-process
            key; dataclass default empty).
        preview_secret: HMAC key of order preview tokens
            (``JOSEPH_PREVIEW_SECRET``, taken as UTF-8 bytes; default a
            random per-process key, so tokens do not survive a restart;
            dataclass default empty).
        step_up_totp_secret: Base32 TOTP (time-based one-time password)
            secret of step-up verification, or ``None`` to make every
            step-up fail (``JOSEPH_STEP_UP_TOTP_SECRET``, default ``None``).
        live_arm_max_ms: Longest (and default) account LIVE arming in
            milliseconds (``JOSEPH_LIVE_ARM_MAX_MS``, default 28800000 =
            8 hours, at most 86400000 = 24 hours).
        cors_origins: Allowed browser origins (``JOSEPH_CORS_ORIGINS``,
            comma-separated; default ``http://localhost:3000`` and
            ``http://127.0.0.1:3000``).
        paper_starting_equity: Starting equity in USD of the USD paper
            account (``JOSEPH_PAPER_STARTING_EQUITY``, default 100000).
        background_tasks: Run the outbox, tick, reconcile and broker event
            loops (``JOSEPH_BACKGROUND_TASKS``, default true).
        tick_interval_ms: Interval in milliseconds of the paper/DEMO tick
            and of the broker event pump (``JOSEPH_TICK_INTERVAL_MS``,
            default 1000).
        reconcile_interval_ms: Interval in milliseconds between
            reconciliation passes (``JOSEPH_RECONCILE_INTERVAL_MS``,
            default 10000).
        stream_heartbeat_s: Heartbeat interval of the user stream in
            seconds (``JOSEPH_STREAM_HEARTBEAT_S``, default 15).
        demo_walk_bps: Random-walk step of the static DEMO gateway in basis
            points per tick (``JOSEPH_DEMO_WALK_BPS``, default 3).
        securities: Brokers, securities paper account and session policy.
        venues: Live crypto venue adapter settings.
    """

    environment: str = "development"
    database_url: str = "sqlite:///./var/execution.db"
    market_url: str = "http://localhost:8000"
    market_gateway: str = "static"
    dev_tokens: Mapping[str, tuple[str, str]] = dataclasses.field(
        default_factory=dict
    )
    service_token: str | None = None
    live_trading_enabled: bool = False
    emergency_halt: bool = False
    vault_master_key: bytes = b""
    preview_secret: bytes = b""
    step_up_totp_secret: str | None = None
    live_arm_max_ms: int = accounts.LIVE_ARM_DEFAULT_MS
    cors_origins: tuple[str, ...] = (
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    )
    paper_starting_equity: decimal.Decimal = decimal.Decimal(100_000)
    background_tasks: bool = True
    tick_interval_ms: int = 1_000
    reconcile_interval_ms: int = 10_000
    stream_heartbeat_s: float = 15.0
    demo_walk_bps: int = 3
    securities: SecuritiesSettings = dataclasses.field(
        default_factory=SecuritiesSettings
    )
    venues: VenueSettings = dataclasses.field(default_factory=VenueSettings)

    def __repr__(self) -> str:
        """Returns a representation without secrets."""
        return (
            f"Settings(environment={self.environment!r}, "
            f"database_url={self.database_url!r}, "
            f"market_gateway={self.market_gateway!r}, "
            f"live_trading_enabled={self.live_trading_enabled}, "
            f"sandbox_venues={self.venues.sandbox_venues!r}, "
            "secrets=[REDACTED])"
        )


def parse_tokens(raw: str) -> dict[str, tuple[str, str]]:
    """Parses ``token:tenant:user`` entries separated by commas.

    Args:
        raw: Configuration text.

    Returns:
        Token to ``(tenant_id, user_id)``; empty for empty text.

    Raises:
        ValueError: For malformed entries.
    """
    tokens: dict[str, tuple[str, str]] = {}
    for entry in filter(None, (e.strip() for e in raw.split(","))):
        parts = entry.split(":")
        if len(parts) != 3 or not all(parts):
            raise ValueError("JOSEPH_DEV_TOKENS entries are token:tenant:user")
        tokens[parts[0]] = (parts[1], parts[2])
    return tokens


def live_arm_max_ms(source: Mapping[str, str]) -> int:
    """Reads ``JOSEPH_LIVE_ARM_MAX_MS`` (fail closed on bad values).

    Args:
        source: Environment.

    Returns:
        Maximum account LIVE arming in milliseconds (default 8 hours).

    Raises:
        ValueError: Unless the value is an integer with
            0 < value <= 24 hours.
    """
    raw = source.get("JOSEPH_LIVE_ARM_MAX_MS", "").strip()
    value = int(raw) if raw else accounts.LIVE_ARM_DEFAULT_MS
    if not 0 < value <= accounts.LIVE_ARM_CEILING_MS:
        raise ValueError(
            "JOSEPH_LIVE_ARM_MAX_MS must be between 1 and 86400000 (24 h)"
        )
    return value


def from_env(env: Mapping[str, str] | None = None) -> Settings:
    """Loads settings from environment variables.

    Args:
        env: Mapping to read (defaults to ``os.environ``).

    Returns:
        Settings.

    Raises:
        ValueError: For invalid values, or a missing
            ``JOSEPH_VAULT_MASTER_KEY`` outside development.
    """
    source = os.environ if env is None else env
    environment = source.get("JOSEPH_ENV", "development")
    dev = environment == "development"
    tokens_raw = source.get(
        "JOSEPH_DEV_TOKENS",
        "dev-local-token:tenant-01:user-01" if dev else "",
    )
    service = source.get("JOSEPH_SERVICE_TOKEN") or (
        "dev-service-token" if dev else None
    )
    master_raw = source.get("JOSEPH_VAULT_MASTER_KEY")
    if master_raw:
        master = base64.b64decode(master_raw)
    elif dev:
        _LOG.warning(
            "JOSEPH_VAULT_MASTER_KEY unset: using an ephemeral dev "
            "key; stored credentials will not survive restarts"
        )
        master = secrets.token_bytes(32)
    else:
        raise ValueError("JOSEPH_VAULT_MASTER_KEY is required in production")
    preview_raw = source.get("JOSEPH_PREVIEW_SECRET")
    preview = preview_raw.encode() if preview_raw else secrets.token_bytes(32)
    origins = tuple(
        o.strip()
        for o in source.get(
            "JOSEPH_CORS_ORIGINS",
            "http://localhost:3000,http://127.0.0.1:3000",
        ).split(",")
        if o.strip()
    )
    gateway = source.get("JOSEPH_MARKET_GATEWAY", "static")
    if gateway not in ("static", "http"):
        raise ValueError("JOSEPH_MARKET_GATEWAY must be static or http")
    return Settings(
        environment=environment,
        database_url=source.get(
            "JOSEPH_DATABASE_URL", "sqlite:///./var/execution.db"
        ),
        market_url=source.get("JOSEPH_MARKET_URL", "http://localhost:8000"),
        market_gateway=gateway,
        dev_tokens=parse_tokens(tokens_raw),
        service_token=service,
        live_trading_enabled=source.get(
            "JOSEPH_LIVE_TRADING_ENABLED", "false"
        ).lower()
        in _TRUE,
        emergency_halt=source.get("JOSEPH_EMERGENCY_HALT", "false").lower()
        in _TRUE,
        vault_master_key=master,
        preview_secret=preview,
        step_up_totp_secret=source.get("JOSEPH_STEP_UP_TOTP_SECRET") or None,
        live_arm_max_ms=live_arm_max_ms(source),
        cors_origins=origins,
        paper_starting_equity=decimal.Decimal(
            source.get("JOSEPH_PAPER_STARTING_EQUITY", "100000")
        ),
        background_tasks=source.get("JOSEPH_BACKGROUND_TASKS", "true").lower()
        in _TRUE,
        tick_interval_ms=int(source.get("JOSEPH_TICK_INTERVAL_MS", "1000")),
        reconcile_interval_ms=int(
            source.get("JOSEPH_RECONCILE_INTERVAL_MS", "10000")
        ),
        stream_heartbeat_s=float(source.get("JOSEPH_STREAM_HEARTBEAT_S", "15")),
        demo_walk_bps=int(source.get("JOSEPH_DEMO_WALK_BPS", "3")),
        securities=securities_from_env(source),
        venues=venue_settings(source),
    )
