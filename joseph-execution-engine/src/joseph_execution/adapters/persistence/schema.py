"""SQLAlchemy Core schema (portable SQLite / PostgreSQL).

Uniqueness that protects against duplicate orders is enforced by the
database, not only by application code.
"""

from __future__ import annotations

import sqlalchemy as sa

METADATA = sa.MetaData()

_TEXT = sa.Text
_MS = sa.BigInteger

accounts = sa.Table(
    "accounts",
    METADATA,
    sa.Column("account_id", sa.String(128), primary_key=True),
    sa.Column("tenant_id", sa.String(128), nullable=False, index=True),
    sa.Column("venue", sa.String(32), nullable=False),
    sa.Column("label", _TEXT, nullable=False),
    sa.Column("status", sa.String(32), nullable=False),
    sa.Column("modes", _TEXT, nullable=False),
    sa.Column("currency", sa.String(16), nullable=False),
    sa.Column("created_at_ms", _MS, nullable=False),
    sa.Column("starting_equity", _TEXT),
    sa.Column("is_paper", sa.Boolean, nullable=False),
    sa.Column("credential_id", sa.String(128)),
    sa.Column("withdrawal_disabled", sa.Boolean, nullable=False),
)

credentials = sa.Table(
    "credentials",
    METADATA,
    sa.Column("credential_id", sa.String(128), primary_key=True),
    sa.Column("tenant_id", sa.String(128), nullable=False),
    sa.Column("account_id", sa.String(128), nullable=False, index=True),
    sa.Column("envelope", _TEXT, nullable=False),
    sa.Column("created_at_ms", _MS, nullable=False),
)

order_intents = sa.Table(
    "order_intents",
    METADATA,
    sa.Column("intent_id", sa.String(64), primary_key=True),
    sa.Column("tenant_id", sa.String(128), nullable=False),
    sa.Column("user_id", sa.String(128), nullable=False),
    sa.Column("account_id", sa.String(128), nullable=False),
    sa.Column("idempotency_key", sa.String(256), nullable=False),
    sa.Column("client_order_id", sa.String(64), nullable=False),
    sa.Column("body_hash", sa.String(64), nullable=False),
    sa.Column("request", _TEXT, nullable=False),
    sa.Column("decision", _TEXT, nullable=False),
    sa.Column("signal_id", sa.String(128)),
    sa.Column("deployment_id", sa.String(64)),
    sa.Column("created_at_ms", _MS, nullable=False),
    sa.UniqueConstraint("tenant_id", "intent_id", name="uq_intent_tenant"),
    sa.UniqueConstraint(
        "tenant_id", "idempotency_key", name="uq_intent_idempotency"
    ),
)

orders = sa.Table(
    "orders",
    METADATA,
    sa.Column("order_id", sa.String(64), primary_key=True),
    sa.Column("intent_id", sa.String(64), nullable=False, index=True),
    sa.Column("tenant_id", sa.String(128), nullable=False, index=True),
    sa.Column("account_id", sa.String(128), nullable=False),
    sa.Column("instrument_id", sa.String(96), nullable=False),
    sa.Column("client_order_id", sa.String(64), nullable=False),
    sa.Column("exchange_order_id", sa.String(128)),
    sa.Column("parent_order_id", sa.String(64), index=True),
    sa.Column("mode", sa.String(16), nullable=False),
    sa.Column("role", sa.String(16), nullable=False),
    sa.Column("side", sa.String(8), nullable=False),
    sa.Column("order_type", sa.String(16), nullable=False),
    sa.Column("time_in_force", sa.String(16), nullable=False),
    sa.Column("quantity_base", _TEXT, nullable=False),
    sa.Column("filled_base", _TEXT, nullable=False),
    sa.Column("avg_fill_price", _TEXT),
    sa.Column("limit_price", _TEXT),
    sa.Column("trigger_price", _TEXT),
    sa.Column("trigger_basis", sa.String(8)),
    sa.Column("reduce_only", sa.Boolean, nullable=False),
    sa.Column("state", sa.String(32), nullable=False, index=True),
    sa.Column("state_reason", _TEXT),
    sa.Column("strategy_version_id", sa.String(128)),
    sa.Column("signal_id", sa.String(128)),
    sa.Column("created_at_ms", _MS, nullable=False),
    sa.Column("updated_at_ms", _MS, nullable=False),
    sa.Column("version", sa.Integer, nullable=False),
    sa.Column("submit_attempts", sa.Integer, nullable=False),
    sa.Column("manual_intervention", sa.Boolean, nullable=False),
    sa.Column("fill_ids", _TEXT, nullable=False),
    sa.UniqueConstraint(
        "account_id", "client_order_id", name="uq_order_client_id"
    ),
)

fills = sa.Table(
    "fills",
    METADATA,
    sa.Column("fill_id", sa.String(64), primary_key=True),
    sa.Column("order_id", sa.String(64), nullable=False, index=True),
    sa.Column("tenant_id", sa.String(128), nullable=False, index=True),
    sa.Column("account_id", sa.String(128), nullable=False),
    sa.Column("instrument_id", sa.String(96), nullable=False),
    sa.Column("exchange_fill_id", sa.String(128), nullable=False),
    sa.Column("side", sa.String(8), nullable=False),
    sa.Column("price", _TEXT, nullable=False),
    sa.Column("quantity_base", _TEXT, nullable=False),
    sa.Column("fee", _TEXT, nullable=False),
    sa.Column("fee_asset", sa.String(16), nullable=False),
    sa.Column("liquidity", sa.String(8), nullable=False),
    sa.Column("ts_ms", _MS, nullable=False),
    sa.Column("mode", sa.String(16), nullable=False),
    sa.Column("realized_pnl", _TEXT, nullable=False),
    sa.UniqueConstraint(
        "account_id", "exchange_fill_id", name="uq_fill_exchange_id"
    ),
)

positions = sa.Table(
    "positions",
    METADATA,
    sa.Column("position_id", sa.String(64), primary_key=True),
    sa.Column("tenant_id", sa.String(128), nullable=False, index=True),
    sa.Column("account_id", sa.String(128), nullable=False),
    sa.Column("instrument_id", sa.String(96), nullable=False),
    sa.Column("mode", sa.String(16), nullable=False),
    sa.Column("signed_qty", _TEXT, nullable=False),
    sa.Column("entry_price", _TEXT),
    sa.Column("realized_pnl", _TEXT, nullable=False),
    sa.Column("fees_paid", _TEXT, nullable=False),
    sa.Column("leverage", _TEXT, nullable=False),
    sa.Column("updated_at_ms", _MS, nullable=False),
    sa.Column("unknown", sa.Boolean, nullable=False),
    sa.UniqueConstraint(
        "account_id", "instrument_id", "mode", name="uq_position_scope"
    ),
)

reservations = sa.Table(
    "reservations",
    METADATA,
    sa.Column("reservation_id", sa.String(64), primary_key=True),
    sa.Column("tenant_id", sa.String(128), nullable=False),
    sa.Column("account_id", sa.String(128), nullable=False, index=True),
    sa.Column("intent_id", sa.String(64), nullable=False),
    sa.Column("order_id", sa.String(64), nullable=False),
    sa.Column("amount", _TEXT, nullable=False),
    sa.Column("mode", sa.String(16), nullable=False),
    sa.Column("status", sa.String(16), nullable=False),
    sa.Column("created_at_ms", _MS, nullable=False),
    sa.Column("released_at_ms", _MS),
    sa.UniqueConstraint("intent_id", "order_id", name="uq_reservation"),
)

outbox = sa.Table(
    "outbox",
    METADATA,
    sa.Column("outbox_id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("aggregate_id", sa.String(64), nullable=False),
    sa.Column("event_key", sa.String(64), nullable=False),
    sa.Column("kind", sa.String(16), nullable=False),
    sa.Column("status", sa.String(16), nullable=False, index=True),
    sa.Column("attempts", sa.Integer, nullable=False),
    sa.Column("next_attempt_ms", _MS, nullable=False),
    sa.Column("last_error", _TEXT),
    sa.Column("created_at_ms", _MS, nullable=False),
    sa.UniqueConstraint("aggregate_id", "event_key", name="uq_outbox_key"),
)

audit_events = sa.Table(
    "audit_events",
    METADATA,
    sa.Column("seq", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("event_id", sa.String(64), nullable=False, unique=True),
    sa.Column("tenant_id", sa.String(128), nullable=False, index=True),
    sa.Column("account_id", sa.String(128)),
    sa.Column("actor", sa.String(160), nullable=False),
    sa.Column("action", sa.String(96), nullable=False),
    sa.Column("entity_type", sa.String(64), nullable=False),
    sa.Column("entity_id", sa.String(160), nullable=False),
    sa.Column("trace_id", sa.String(96), nullable=False),
    sa.Column("ts_ms", _MS, nullable=False),
    sa.Column("payload", _TEXT, nullable=False),
    sa.Column("prev_hash", sa.String(64), nullable=False),
    sa.Column("hash", sa.String(64), nullable=False),
)

kill_switches = sa.Table(
    "kill_switches",
    METADATA,
    sa.Column("tenant_id", sa.String(128), primary_key=True),
    sa.Column("scope", sa.String(16), primary_key=True),
    sa.Column("target_key", sa.String(160), primary_key=True),
    sa.Column("target_id", sa.String(160)),
    sa.Column("engaged", sa.Boolean, nullable=False),
    sa.Column("cancel_open_orders", sa.Boolean, nullable=False),
    sa.Column("flatten_positions", sa.Boolean, nullable=False),
    sa.Column("reason", _TEXT, nullable=False),
    sa.Column("actor", sa.String(160), nullable=False),
    sa.Column("changed_at_ms", _MS, nullable=False),
)

risk_limits = sa.Table(
    "risk_limits",
    METADATA,
    sa.Column("tenant_id", sa.String(128), primary_key=True),
    sa.Column("account_id", sa.String(128), primary_key=True),
    sa.Column("limits", _TEXT, nullable=False),
    sa.Column("updated_at_ms", _MS, nullable=False),
)

deployments = sa.Table(
    "deployments",
    METADATA,
    sa.Column("deployment_id", sa.String(64), primary_key=True),
    sa.Column("tenant_id", sa.String(128), nullable=False, index=True),
    sa.Column("strategy_version_id", sa.String(128), nullable=False),
    sa.Column("account_id", sa.String(128), nullable=False),
    sa.Column("mode", sa.String(16), nullable=False),
    sa.Column("state", sa.String(24), nullable=False),
    sa.Column("limits", _TEXT, nullable=False),
    sa.Column("sizing", _TEXT, nullable=False),
    sa.Column("live_consent", _TEXT),
    sa.Column("created_at_ms", _MS, nullable=False),
    sa.Column("updated_at_ms", _MS, nullable=False),
)

SQLITE_AUDIT_TRIGGERS = (
    """CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON
    audit_events BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END""",
    """CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON
    audit_events BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END""",
)
