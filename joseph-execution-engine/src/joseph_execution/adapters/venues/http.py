# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""HTTP transport shared by the live venue adapters.

Maps transport failures onto the OMS's safety semantics:

* the connection could not be opened (``ConnectError``, ``ConnectTimeout``,
  ``PoolTimeout``): nothing was sent, ``AdapterUnavailableError``;
* the request may have reached the venue (read/write timeouts, dropped
  connections): the outcome is unknown, ``AdapterTimeoutError``, which the
  OMS resolves by querying the client order id and never by resending;
* HTTP 429 and 418 (Binance's IP ban): the venue refused without executing,
  ``RateLimitedError``, and every budget of the venue's limiter pauses for
  ``Retry-After`` (or a default).

Error messages never include the URL, query or headers, so signatures and
API keys cannot leak through exceptions. JSON numbers are parsed as
``Decimal`` so no float reaches an order path.
"""

from __future__ import annotations

import dataclasses
import decimal
import json
import logging
from collections.abc import Mapping
from typing import Any

import httpx

from joseph_execution.adapters.venues import ratelimit
from joseph_execution.domain import errors

_LOG = logging.getLogger(__name__)
_DEFAULT_PENALTY_S = 10.0
_NOT_SENT = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)


@dataclasses.dataclass(frozen=True, slots=True)
class Response:
    """A venue HTTP response.

    Attributes:
        status: HTTP status.
        payload: Parsed JSON (``Decimal`` numbers) or the raw text.
        headers: Lower-cased response headers.
    """

    status: int
    payload: Any
    headers: Mapping[str, str]


def parse_json(text: str) -> Any:
    """Parses JSON with exact decimals.

    Args:
        text: JSON text.

    Returns:
        Parsed value (floats become ``Decimal``).
    """
    return json.loads(text, parse_float=decimal.Decimal)


def _retry_after(headers: Mapping[str, str]) -> float:
    """Reads a ``Retry-After`` header in seconds.

    Args:
        headers: Lower-cased headers.

    Returns:
        Seconds (default when absent or unparsable).
    """
    try:
        return max(float(headers.get("retry-after", "")), 1.0)
    except ValueError:
        return _DEFAULT_PENALTY_S


class VenueHttp:
    """Rate-limited HTTP client bound to one venue base URL."""

    def __init__(
        self,
        venue: str,
        base_url: str,
        limiter: ratelimit.WindowLimiter,
        *,
        timeout_s: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """Initializes the client.

        Args:
            venue: Venue code (for messages).
            base_url: Base URL without a trailing slash.
            limiter: Venue limiter.
            timeout_s: Per-request timeout.
            transport: Optional transport (tests).
        """
        self.venue = venue
        self.base_url = base_url.rstrip("/")
        self.limiter = limiter
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_s, connect=min(timeout_s, 5.0)),
            transport=transport,
            headers={"User-Agent": "joseph-execution/0.5"},
        )

    def __repr__(self) -> str:
        """Returns a representation without credentials."""
        return f"VenueHttp(venue={self.venue!r}, base_url={self.base_url!r})"

    async def aclose(self) -> None:
        """Closes the connection pool."""
        await self._client.aclose()

    async def request(
        self,
        method: str,
        path: str,
        *,
        query: str = "",
        body: str | None = None,
        form: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
        costs: Mapping[str, int] | None = None,
    ) -> Response:
        """Sends one request after acquiring rate budget.

        Args:
            method: HTTP method.
            path: Path starting with ``/``.
            query: Pre-encoded query string (signed exactly as sent).
            body: Raw request body (JSON text).
            form: Form fields (``application/x-www-form-urlencoded``).
            headers: Extra headers.
            costs: Rate budget costs (default ``{"weight": 1}``).

        Returns:
            Response (any status; venue adapters map error bodies).

        Raises:
            RateLimitedError: No budget, or the venue answered 429/418.
            AdapterUnavailableError: The connection could not be opened.
            AdapterTimeoutError: The outcome is unknown.
        """
        await self.limiter.acquire(costs or {"weight": 1})
        url = f"{self.base_url}{path}"
        if query:
            url = f"{url}?{query}"
        try:
            reply = await self._client.request(
                method,
                url,
                content=None if body is None else body.encode("utf-8"),
                data=None if form is None else dict(form),
                headers=dict(headers or {}),
            )
        except _NOT_SENT:
            raise errors.AdapterUnavailableError(
                f"{self.venue} is unreachable (request not sent)"
            ) from None
        except httpx.TimeoutException:
            raise errors.AdapterTimeoutError(
                f"{self.venue} {method} {path} timed out; outcome unknown"
            ) from None
        except httpx.TransportError as err:
            raise errors.AdapterTimeoutError(
                f"{self.venue} {method} {path} failed in transit "
                f"({type(err).__name__}); outcome unknown"
            ) from None
        lowered = {k.lower(): v for k, v in reply.headers.items()}
        text = reply.text
        try:
            payload: Any = parse_json(text) if text else None
        except ValueError:
            payload = text
        if reply.status_code in (418, 429):
            pause = _retry_after(lowered)
            self.limiter.penalize(pause)
            _LOG.warning(
                "%s answered %s; pausing requests for %.0fs",
                self.venue,
                reply.status_code,
                pause,
            )
            raise errors.RateLimitedError(
                f"{self.venue} rate limit hit (HTTP {reply.status_code}); "
                "request not executed",
                retry_after_s=pause,
            )
        return Response(reply.status_code, payload, lowered)
