# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

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


class HttpStatusError(errors.VenueUnavailableError):
    """A venue rejected a request with a 4xx status (not rate limiting).

    Attributes:
        status_code: HTTP status of the response.
    """

    def __init__(self, message: str, status_code: int) -> None:
        """Initializes the error (never retryable).

        Args:
            message: Explanation.
            status_code: HTTP status.
        """
        super().__init__(message, retryable=False)
        self.status_code = status_code


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
        name: str | None = None,
        limiter: RateLimiter | None = None,
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
            name: Source name for error messages (defaults to the venue;
                e.g. ``alpaca`` serves two venues).
            limiter: Shared token bucket (a data source whose budget spans
                several clients); ``rate_per_s``/``burst`` are then unused.
        """
        self.venue = venue
        self.name = name or venue.value
        self.base_url = base_url.rstrip("/")
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=timeout_s, headers={"User-Agent": USER_AGENT}
        )
        self._limiter = limiter or RateLimiter(rate_per_s, burst)
        self._semaphore = asyncio.Semaphore(max_concurrency)

    async def get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        headers: dict[str, str] | None = None,
    ) -> Any:
        """Performs a GET and parses JSON.

        Args:
            path: Path appended to the base URL.
            params: Query parameters.
            headers: Extra request headers (e.g. API keys; never logged).

        Returns:
            Parsed JSON body.
        """
        response = await self._request(
            "GET", path, params=params, headers=headers
        )
        return self._json(response, path)

    async def get_text(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        headers: dict[str, str] | None = None,
    ) -> str:
        """Performs a GET and returns the body as text (CSV, pipe files).

        Args:
            path: Path appended to the base URL.
            params: Query parameters.
            headers: Extra request headers.

        Returns:
            Decoded body.
        """
        response = await self._request(
            "GET", path, params=params, headers=headers
        )
        return response.text

    async def post(self, path: str, body: dict[str, Any]) -> Any:
        """Performs a JSON POST and parses JSON.

        Args:
            path: Path appended to the base URL.
            body: JSON body.

        Returns:
            Parsed JSON body.
        """
        response = await self._request("POST", path, json_body=body)
        return self._json(response, path)

    def _json(self, response: httpx.Response, path: str) -> Any:
        """Parses a JSON body.

        Args:
            response: Successful response.
            path: Request path for the error message.

        Returns:
            Parsed JSON body.

        Raises:
            errors.VenueUnavailableError: On invalid JSON.
        """
        try:
            return response.json()
        except ValueError as err:
            raise errors.VenueUnavailableError(
                f"{self.name} returned invalid JSON on {path}"
            ) from err

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        """Sends a request with rate limiting and error mapping.

        Args:
            method: HTTP method.
            path: Path appended to the base URL.
            params: Query parameters.
            json_body: JSON body.
            headers: Extra request headers.

        Returns:
            The successful (2xx/3xx) response.

        Raises:
            errors.RateLimitedError: On HTTP 418/429.
            errors.VenueUnavailableError: On timeouts, transport errors,
                non-2xx responses.
        """
        await self._limiter.acquire()
        name = self.name
        try:
            async with self._semaphore:
                response = await self._client.request(
                    method,
                    f"{self.base_url}{path}",
                    params=params,
                    json=json_body,
                    headers=headers,
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
            raise HttpStatusError(
                f"{name} rejected {path} with HTTP {response.status_code}",
                response.status_code,
            )
        return response

    async def close(self) -> None:
        """Closes the underlying client if owned."""
        if self._owns_client:
            await self._client.aclose()
