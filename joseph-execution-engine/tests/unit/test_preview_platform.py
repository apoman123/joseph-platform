# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Unit tests for previews, vault, step-up, config, logging, metrics, hub."""

from __future__ import annotations

import asyncio
import base64
import dataclasses
import decimal
import logging

import pytest

from joseph_execution.adapters import step_up
from joseph_execution.adapters import vault
from joseph_execution.domain import enums
from joseph_execution.domain import errors
from joseph_execution.domain import intents
from joseph_execution.domain import market
from joseph_execution.oms import preview
from joseph_execution.platform import clock as clock_lib
from joseph_execution.platform import config
from joseph_execution.platform import logging as log_lib
from joseph_execution.platform import metrics
from joseph_execution.platform import streams

D = decimal.Decimal
BTC = "mock:perp:BTCUSD"
META = market.InstrumentMeta(
    BTC,
    "mock",
    D("0.5"),
    D("0.001"),
    D("0.001"),
    enums.InstrumentStatus.TRADING,
)


def _req(**overrides: object) -> intents.OrderIntentRequest:
    """Builds a limit request.

    Args:
        **overrides: Overrides.

    Returns:
        Request.
    """
    fields: dict[str, object] = {
        "account_id": "a",
        "instrument_id": BTC,
        "side": enums.OrderSide.BUY,
        "position_effect": enums.PositionEffect.OPEN,
        "order_type": enums.OrderType.LIMIT,
        "quantity_base": D("0.0109"),
        "time_in_force": enums.TimeInForce.GTC,
        "reduce_only": False,
        "mode": enums.OrderMode.PAPER,
        "source": enums.IntentSource.MANUAL_TICKET,
        "limit_price": D("100.26"),
        "leverage": D(2),
    }
    fields.update(overrides)
    return intents.OrderIntentRequest(**fields)  # type: ignore[arg-type]


def test_normalize_floors_quantity_and_rounds_prices() -> None:
    """Quantities round down to the lot; prices to the nearest tick."""
    req = _req(
        bracket=intents.Bracket(
            stop_loss=intents.StopLossSpec(D("95.1")),
            take_profit=(intents.TakeProfitSpec(D("110.3")),),
        )
    )
    normalized, warnings = preview.normalize(req, META)
    assert normalized.quantity_base == D("0.010")
    assert normalized.limit_price == D("100.5")
    assert normalized.bracket.stop_loss is not None
    assert normalized.bracket.stop_loss.trigger == D(95)
    assert normalized.bracket.take_profit[0].trigger == D("110.5")
    assert len(warnings) == 4


def test_normalize_rejects_zero_quantity() -> None:
    """A quantity below one lot is rejected, not rounded up."""
    with pytest.raises(errors.ExecutionError) as info:
        preview.normalize(_req(quantity_base=D("0.0004")), META)
    assert info.value.code == "INVALID_LOT_SIZE"


def test_estimates() -> None:
    """Estimates walk the book for market orders and price fees."""
    book = market.BookView(
        BTC, ((D(99), D(1)),), ((D(100), D("0.5")), (D(102), D(10))), 1
    )
    fees = preview.FeeSchedule()
    est = preview.estimate(
        _req(
            order_type=enums.OrderType.MARKET,
            limit_price=None,
            quantity_base=D(1),
            time_in_force=enums.TimeInForce.IOC,
        ),
        META,
        None,
        book,
        fees,
        now_ms=0,
    )
    assert est.reference_price == D(101)
    assert est.slippage_pct == D(1)
    assert est.fee == D(101) * D(5) / D(10000)
    maker = preview.estimate(
        _req(
            time_in_force=enums.TimeInForce.POST_ONLY,
            quantity_base=D(1),
            limit_price=D(100),
        ),
        META,
        None,
        None,
        fees,
        now_ms=0,
    )
    assert maker.fee == D("0.02")
    reduce = preview.estimate(
        _req(
            reduce_only=True,
            position_effect=enums.PositionEffect.CLOSE,
            side=enums.OrderSide.SELL,
            quantity_base=D(1),
            limit_price=D(100),
        ),
        META,
        None,
        None,
        fees,
        now_ms=0,
    )
    assert reduce.margin_required == 0
    unknown = preview.estimate(
        _req(
            order_type=enums.OrderType.MARKET,
            limit_price=None,
            time_in_force=enums.TimeInForce.IOC,
        ),
        META,
        None,
        None,
        fees,
        now_ms=0,
    )
    assert unknown.notional is None


def test_preview_token_binding_tamper_and_expiry() -> None:
    """Tokens bind tenant and parameters and expire."""
    signer = preview.PreviewSigner(b"secret", ttl_ms=1000)
    req = _req(quantity_base=D("0.01"), limit_price=D(100))
    token, expires = signer.sign("t", req, 0)
    assert expires == 1000
    signer.verify("t", req, token, 500)
    for bad_req, tenant in (
        (dataclasses.replace(req, quantity_base=D("0.02")), "t"),
        (req, "other"),
    ):
        with pytest.raises(errors.ExecutionError) as info:
            signer.verify(tenant, bad_req, token, 500)
        assert info.value.code == "PREVIEW_REQUIRED"
    with pytest.raises(errors.ExecutionError) as info:
        signer.verify("t", req, token, 1001)
    assert info.value.code == "PREVIEW_EXPIRED"
    with pytest.raises(errors.ExecutionError):
        signer.verify("t", req, "garbage", 0)


def test_vault_round_trip_tamper_and_repr() -> None:
    """Envelopes decrypt only intact and never expose key material."""
    seen: list[str] = []
    box = vault.AesGcmVault(b"m" * 32, on_secret=seen.append)
    envelope = box.seal({"api_key": "AKIA123", "api_secret": "s3cr3t-value"})
    assert "s3cr3t-value" not in str(envelope)
    assert box.open(envelope) == {
        "api_key": "AKIA123",
        "api_secret": "s3cr3t-value",
    }
    assert "s3cr3t-value" in seen
    broken = dict(envelope)
    raw = bytearray(base64.urlsafe_b64decode(broken["ciphertext"]))
    raw[0] ^= 1
    broken["ciphertext"] = base64.urlsafe_b64encode(bytes(raw)).decode()
    with pytest.raises(errors.ExecutionError):
        box.open(broken)
    with pytest.raises(errors.ExecutionError):
        vault.AesGcmVault(b"x" * 32).open(envelope)
    assert "REDACTED" in repr(box)
    with pytest.raises(ValueError):
        vault.AesGcmVault(b"short")


def test_totp_step_up() -> None:
    """TOTP codes verify within one step and fail closed without a secret."""
    clock = clock_lib.ManualClock(1_700_000_000_000)
    verifier = step_up.TotpStepUpVerifier("JBSWY3DPEHPK3PXP", clock)
    code = step_up.totp("JBSWY3DPEHPK3PXP", clock.now_ms())
    assert verifier.verify("u", code)
    clock.advance(30_000)
    assert verifier.verify("u", code)
    clock.advance(90_000)
    assert not verifier.verify("u", code)
    assert not verifier.verify("u", "abcdef")
    assert not step_up.TotpStepUpVerifier(None, clock).verify("u", code)


def test_config_parsing_and_production_requirements() -> None:
    """Settings parse tokens and refuse insecure production defaults."""
    dev = config.from_env({"JOSEPH_ENV": "development"})
    assert dev.live_trading_enabled is False
    assert "dev-local-token" in dev.dev_tokens
    assert len(dev.vault_master_key) == 32
    assert "secret" not in repr(dev).lower().replace("secrets=[redacted]", "")
    with pytest.raises(ValueError):
        config.from_env({"JOSEPH_ENV": "production"})
    prod = config.from_env(
        {
            "JOSEPH_ENV": "production",
            "JOSEPH_VAULT_MASTER_KEY": base64.b64encode(b"k" * 32).decode(),
        }
    )
    assert prod.dev_tokens == {}
    assert prod.service_token is None
    with pytest.raises(ValueError):
        config.parse_tokens("only-token")
    live = config.from_env({"JOSEPH_LIVE_TRADING_ENABLED": "TRUE"})
    assert live.live_trading_enabled
    with pytest.raises(ValueError):
        config.from_env({"JOSEPH_MARKET_GATEWAY": "magic"})


def test_log_redaction(caplog: pytest.LogCaptureFixture) -> None:
    """Registered secrets and secret-like patterns never reach logs."""
    registry = log_lib.SecretRegistry()
    registry.add("my-super-secret")
    logger = logging.getLogger("joseph.test.redaction")
    logger.addFilter(log_lib.RedactingFilter(registry))
    with caplog.at_level(logging.INFO, logger="joseph.test.redaction"):
        logger.info("value=%s auth=%s", "my-super-secret", "Bearer abc.def-123")
        logger.info("api_secret=hunter22 token: xyz123")
    text = caplog.text
    assert "my-super-secret" not in text
    assert "abc.def-123" not in text
    assert "hunter22" not in text
    assert "xyz123" not in text
    assert text.count("[REDACTED]") >= 4


def test_metrics_render() -> None:
    """Metrics render in Prometheus format including required series."""
    registry = metrics.MetricsRegistry()
    registry.increment("risk_block_total", {"decision": "BLOCK"})
    registry.increment("risk_block_total", {"decision": "BLOCK"})
    registry.gauge("reconcile_lag_ms", 12.5)
    text = registry.render()
    assert 'joseph_risk_block_total{decision="BLOCK"} 2.0' in text
    assert "joseph_reconcile_lag_ms 12.5" in text
    assert "joseph_order_unknown_total 0" in text
    assert registry.value("risk_block_total", {"decision": "BLOCK"}) == 2


async def test_stream_hub_tenant_isolation_and_replay(
    contract_validate: object,
) -> None:
    """Hub sequences per tenant, isolates tenants and bounds replay."""
    hub = streams.UserStreamHub(clock_lib.ManualClock(), retention=3)
    sub_a = hub.subscribe("a")
    sub_b = hub.subscribe("b")
    for n in range(5):
        hub.publish("a", "order.updated", "orders", {"n": n}, "acct-a")
    hub.publish("b", "order.updated", "orders", {"n": 0}, "acct-b")
    assert sub_a.queue.qsize() == 5
    assert sub_b.queue.qsize() == 1
    envelope = await asyncio.wait_for(sub_a.queue.get(), 1)
    contract_validate(
        "stream.schema.json",
        "StreamEnvelope",  # type: ignore[operator]
        envelope,
    )
    assert hub.current_sequence("a") == 5
    assert hub.current_sequence("b") == 1
    replay = hub.replay(sub_a, 3)
    assert replay is not None
    assert [e["sequence"] for e in replay] == [4, 5]
    assert hub.replay(sub_a, 0) is None
    assert hub.replay(sub_a, 5) == []
    sub_a.account_id = "acct-x"
    assert hub.replay(sub_a, 3) == []
    hub.unsubscribe(sub_b)
    hub.publish("b", "order.updated", "orders", {}, None)
    assert sub_b.queue.qsize() == 1
