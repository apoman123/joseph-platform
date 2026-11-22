"""Shared HTTP plumbing for venue adapters.

Maps transport failures to domain errors (timeouts and 5xx become
``VenueUnavailableError``; 429/418 become ``RateLimitedError``) and applies a
per-venue token-bucket rate limit plus a concurrency cap so screening scans
stay within public API budgets.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from joseph.domain import errors
from joseph.domain import instruments

USER_AGENT = "joseph-market-engine/0.5"


class RateLimiter:
    """Async token bucket.

    Attributes:
        rate_per_s: Sustained requests per second.
        burst: Maximum bucket size.
    """

    def __init__(self, rate_per_s: float, burst: int) -> None:
        """Initializes the bucket full.

        Args:
            rate_per_s: Sustained rate.
            burst: Bucket capacity.
        """
        self.rate_per_s = rate_per_s
        self.burst = burst
        self._tokens = float(burst)
        self._updated = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Waits until a token is available and consumes it."""
        async with self._lock:
            while True:
                now = time.monotonic()
                self._tokens = min(
                    self.burst,
                    self._tokens + (now - self._updated) * self.rate_per_s,
                )
                self._updated = now
                if self._tokens >= 1:
                    self._tokens -= 1
                    return
                await asyncio.sleep((1 - self._tokens) / self.rate_per_s)


class VenueHttp:
    """JSON HTTP client bound to one venue."""

    def __init__(
        self,
        venue: instruments.Venue,
        base_url: str,
        *,
        client: httpx.AsyncClient | None = None,
        timeout_s: float = 10.0,
        rate_per_s: float = 10.0,
        burst: int = 10,
        max_concurrency: int = 5,
    ) -> None:
        """Initializes the client.

        Args:
            venue: Venue for error messages.
            base_url: Base URL without trailing slash.
            client: Optional injected client (tests use respx).
            timeout_s: Request timeout.
            rate_per_s: Token-bucket rate.
            burst: Token-bucket capacity.
            max_concurrency: Concurrent request cap.
        """
        self.venue = venue
        self.base_url = base_url.rstrip("/")
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=timeout_s, headers={"User-Agent": USER_AGENT}
        )
        self._limiter = RateLimiter(rate_per_s, burst)
        self._semaphore = asyncio.Semaphore(max_concurrency)

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """Performs a GET and parses JSON.

        Args:
            path: Path appended to the base URL.
            params: Query parameters.

        Returns:
            Parsed JSON body.
        """
        return await self._request("GET", path, params=params)

    async def post(self, path: str, body: dict[str, Any]) -> Any:
        """Performs a JSON POST and parses JSON.

        Args:
            path: Path appended to the base URL.
            body: JSON body.

        Returns:
            Parsed JSON body.
        """
        return await self._request("POST", path, json_body=body)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        """Sends a request with rate limiting and error mapping.

        Args:
            method: HTTP method.
            path: Path appended to the base URL.
            params: Query parameters.
            json_body: JSON body.

        Returns:
            Parsed JSON body.

        Raises:
            errors.RateLimitedError: On HTTP 418/429.
            errors.VenueUnavailableError: On timeouts, transport errors,
                non-2xx responses or invalid JSON.
        """
        await self._limiter.acquire()
        name = self.venue.value
        try:
            async with self._semaphore:
                response = await self._client.request(
                    method,
                    f"{self.base_url}{path}",
                    params=params,
                    json=json_body,
                )
        except httpx.TimeoutException as err:
            raise errors.VenueUnavailableError(
                f"{name} timed out on {path}"
            ) from err
        except httpx.HTTPError as err:
            raise errors.VenueUnavailableError(
                f"{name} unreachable: {type(err).__name__}"
            ) from err
        if response.status_code in (418, 429):
            raise errors.RateLimitedError(f"{name} rate limited {path}")
        if response.status_code >= 500:
            raise errors.VenueUnavailableError(
                f"{name} returned HTTP {response.status_code} on {path}"
            )
        if response.status_code >= 400:
            raise errors.VenueUnavailableError(
                f"{name} rejected {path} with HTTP {response.status_code}",
                retryable=False,
            )
        try:
            return response.json()
        except ValueError as err:
            raise errors.VenueUnavailableError(
                f"{name} returned invalid JSON on {path}"
            ) from err

    async def close(self) -> None:
        """Closes the underlying client if owned."""
        if self._owns_client:
            await self._client.aclose()
