"""Environment-driven settings.

Safety defaults: live trading off, emergency halt off, dev tokens only in
the ``development`` environment, step-up disabled unless a TOTP secret is
configured.
"""

from __future__ import annotations

import base64
import dataclasses
import decimal
import logging
import os
import secrets
from collections.abc import Mapping

_LOG = logging.getLogger(__name__)
_TRUE = ("1", "true", "yes", "on")


@dataclasses.dataclass(frozen=True, slots=True)
class Settings:
    """Execution engine settings.

    Attributes:
        environment: ``development`` or ``production``.
        database_url: SQLAlchemy URL.
        market_url: Market engine base URL.
        market_gateway: ``static`` (DEMO) or ``http``.
        dev_tokens: Bearer token to ``(tenant_id, user_id)``.
        service_token: Token for ``/internal`` endpoints.
        live_trading_enabled: Global LIVE gate.
        emergency_halt: Platform-wide halt.
        vault_master_key: 32-byte master key.
        preview_secret: HMAC key for preview tokens.
        step_up_totp_secret: Base32 TOTP secret or ``None``.
        cors_origins: Allowed browser origins.
        paper_starting_equity: Paper equity.
        background_tasks: Run outbox, tick and reconcile loops.
        tick_interval_ms: Paper tick interval.
        reconcile_interval_ms: Reconcile interval.
        stream_heartbeat_s: User stream heartbeat.
        demo_walk_bps: Random-walk step of the static DEMO gateway.
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

    def __repr__(self) -> str:
        """Returns a representation without secrets."""
        return (
            f"Settings(environment={self.environment!r}, "
            f"database_url={self.database_url!r}, "
            f"market_gateway={self.market_gateway!r}, "
            f"live_trading_enabled={self.live_trading_enabled}, "
            "secrets=[REDACTED])"
        )


def parse_tokens(raw: str) -> dict[str, tuple[str, str]]:
    """Parses ``token:tenant:user`` entries separated by commas.

    Args:
        raw: Configuration text.

    Returns:
        Token mapping.

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


def from_env(env: Mapping[str, str] | None = None) -> Settings:
    """Loads settings from environment variables.

    Args:
        env: Mapping to read (defaults to ``os.environ``).

    Returns:
        Settings.

    Raises:
        ValueError: For invalid values or missing production secrets.
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
    )
