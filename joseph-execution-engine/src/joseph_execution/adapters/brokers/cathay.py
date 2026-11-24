# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Cathay Securities (TWSE, TPEx): fail-closed until its SDK arrives.

Cathay Securities publishes no developer API; an SDK/spec is available
only through a Cathay representative. Until one is integrated this entry
declares Cathay's role (TWSE and TPEx, cash account, TWD) and refuses
every call before anything is sent: ``build`` returns a
``base.FailClosedBrokerAdapter``, which has no client and no network
access, whatever the deployment's LIVE flags say.

To replace it once the SDK arrives: implement ``base.BrokerAdapter`` (a
``ports.trading.TradingAdapter`` plus the broker pump hooks) in this
module (``sinopac.py`` is the template: a small client protocol, a fake
in tests, pure order mappings), keep ``build`` as the factory and update
``capabilities.cathay``. Nothing else changes:
routing (``domain.equities.BROKER_EXCHANGES``), risk checks and the OMS
already treat ``cathay`` as a TWSE and TPEx broker (for TPEx codes
the SDK will need the exchange, as Shioaji's ``Stocks.OTC`` group
provides).
"""

from __future__ import annotations

from joseph_execution.adapters.brokers import base
from joseph_execution.adapters.brokers import capabilities as broker_caps


def build() -> base.FailClosedBrokerAdapter:
    """Creates the Cathay adapter.

    Returns:
        Fail-closed adapter that refuses every call with
        ``CATHAY_AWAITING_SDK`` as the reason.
    """
    return base.FailClosedBrokerAdapter(
        broker_caps.cathay(), broker_caps.CATHAY_AWAITING_SDK
    )
