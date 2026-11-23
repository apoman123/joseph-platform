"""Opt-in live checks of the MEXC private stream endpoints.

Run with ``JOSEPH_NETWORK_TESTS=1 uv run pytest tests/network -m network``.
No credentials are used: the stream logs in with a fake key, which MEXC
refuses. This verifies that both the JSON and the Protobuf endpoint answer
the login in JSON text and that the adapter reports the refusal. Private
Protobuf pushes need a real account and are not covered here.
"""

from __future__ import annotations

import os

import pytest

from joseph_execution.adapters.venues import catalog
from joseph_execution.adapters.venues import credentials
from joseph_execution.adapters.venues import live
from joseph_execution.adapters.venues import mexc
from joseph_execution.domain import errors
from joseph_execution.platform import clock as clock_lib
from joseph_execution.testing import venue_fakes

pytestmark = [
    pytest.mark.network,
    pytest.mark.skipif(
        os.environ.get("JOSEPH_NETWORK_TESTS") != "1",
        reason="set JOSEPH_NETWORK_TESTS=1 to hit live APIs",
    ),
]

_FAKE_KEYS = {"api_key": "invalid", "api_secret": "invalid"}
"""Too short to be a credential; MEXC refuses the login."""


@pytest.mark.parametrize(
    "ws_url", [mexc.WS_URL, mexc.WS_PROTOBUF_URL], ids=["json", "protobuf"]
)
async def test_live_login_refusal_is_reported_on_both_endpoints(
    ws_url: str,
) -> None:
    """A refused login surfaces as a permission error, not a parse error."""
    adapter = mexc.MexcFuturesAdapter(
        catalog.DOCUMENTED["mexc"],
        credentials.StaticCredentials({"acct": _FAKE_KEYS}),
        clock_lib.SystemClock(),
        live.VenueOptions(),
        urls={"ws_url": ws_url},
    )
    with pytest.raises(errors.VenuePermissionError):
        await venue_fakes.collect(adapter.stream_account_events("acct"), 1)
    await adapter.aclose()
