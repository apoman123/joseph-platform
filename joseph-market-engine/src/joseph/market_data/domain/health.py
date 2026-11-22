"""Feed health tracking (pure).

Health is derived, never asserted: a feed is ``STALE`` once no message has
arrived within its budget, ``DEGRADED`` while it runs on a fallback
transport or after recent reconnects/gaps, ``UNAVAILABLE`` when the source
cannot be reached, otherwise ``HEALTHY``.
"""

from __future__ import annotations

import dataclasses

from joseph.domain import instruments
from joseph.domain import quality

_RECENT_TROUBLE_MS = 60_000


@dataclasses.dataclass(frozen=True, slots=True)
class FeedHealth:
    """Contract ``FeedHealth``.

    Attributes:
        venue: Venue.
        state: Derived health.
        feed_mode: Requested policy.
        feed_state: Provenance shown to users.
        transport: Active transport.
        reconnects: Reconnect count.
        gaps_detected: Detected gaps.
        as_of_ms: Evaluation time.
        last_message_ms: Last message receipt time.
        lag_ms: ``as_of_ms - last_message_ms``.
        last_error: Last error summary.
    """

    venue: instruments.Venue
    state: quality.HealthState
    feed_mode: quality.FeedMode
    feed_state: quality.FeedState
    transport: quality.Transport
    reconnects: int
    gaps_detected: int
    as_of_ms: int
    last_message_ms: int | None = None
    lag_ms: int | None = None
    last_error: str | None = None

    def to_contract(self) -> dict[str, object]:
        """Serializes to the contract object.

        Returns:
            JSON-compatible dict.
        """
        return {
            "venue": self.venue.value,
            "state": self.state.value,
            "feed_mode": self.feed_mode.value,
            "feed_state": self.feed_state.value,
            "transport": self.transport.value,
            "last_message_ms": self.last_message_ms,
            "lag_ms": self.lag_ms,
            "reconnects": self.reconnects,
            "gaps_detected": self.gaps_detected,
            "last_error": self.last_error,
            "as_of_ms": self.as_of_ms,
        }


class FeedHealthTracker:
    """Mutable accumulator of feed observations."""

    def __init__(
        self,
        venue: instruments.Venue,
        feed_mode: quality.FeedMode = quality.FeedMode.AUTO,
        stale_after_ms: int = 30_000,
    ) -> None:
        """Initializes the tracker.

        Args:
            venue: Venue being tracked.
            feed_mode: Requested policy.
            stale_after_ms: Silence budget before the feed is stale.
        """
        self.venue = venue
        self.feed_mode = feed_mode
        self.stale_after_ms = stale_after_ms
        self.transport = quality.Transport.NONE
        self.feed_state = quality.FeedState.CONNECTING
        self.fallback = False
        self.reconnects = 0
        self.gaps = 0
        self.last_message_ms: int | None = None
        self.last_error: str | None = None
        self._last_trouble_ms: int | None = None
        self._unavailable = False

    def set_transport(
        self,
        transport: quality.Transport,
        feed_state: quality.FeedState,
        fallback: bool = False,
    ) -> None:
        """Records the active transport.

        Args:
            transport: Transport now delivering data.
            feed_state: Provenance of delivered data.
            fallback: True when running on a degraded fallback.
        """
        self.transport = transport
        self.feed_state = feed_state
        self.fallback = fallback
        self._unavailable = False

    def record_message(self, now_ms: int) -> None:
        """Records a received message.

        Args:
            now_ms: Receipt time.
        """
        self.last_message_ms = now_ms
        self._unavailable = False

    def record_reconnect(self, now_ms: int, error: str | None = None) -> None:
        """Records a reconnect attempt.

        Args:
            now_ms: Attempt time.
            error: Cause summary.
        """
        self.reconnects += 1
        self._last_trouble_ms = now_ms
        if error:
            self.last_error = error

    def record_gap(self, now_ms: int, bars: int = 1) -> None:
        """Records detected missing bars.

        Args:
            now_ms: Detection time.
            bars: Number of missing bars.
        """
        self.gaps += bars
        self._last_trouble_ms = now_ms

    def record_error(self, now_ms: int, error: str) -> None:
        """Records a transient error.

        Args:
            now_ms: Error time.
            error: Summary (never secrets).
        """
        self.last_error = error
        self._last_trouble_ms = now_ms

    def set_unavailable(self, now_ms: int, error: str) -> None:
        """Marks the source unreachable.

        Args:
            now_ms: Time of the failure.
            error: Summary.
        """
        self._unavailable = True
        self.transport = quality.Transport.NONE
        self.feed_state = quality.FeedState.UNAVAILABLE
        self.record_error(now_ms, error)

    def snapshot(self, now_ms: int) -> FeedHealth:
        """Derives the current health.

        Args:
            now_ms: Evaluation time.

        Returns:
            Feed health.
        """
        lag = (
            None
            if self.last_message_ms is None
            else max(0, now_ms - self.last_message_ms)
        )
        feed_state = self.feed_state
        if self._unavailable:
            state = quality.HealthState.UNAVAILABLE
        elif lag is not None and lag > self.stale_after_ms:
            state = quality.HealthState.STALE
            if feed_state in (quality.FeedState.LIVE, quality.FeedState.POLL):
                feed_state = quality.FeedState.STALE
        elif (
            self.fallback
            or (
                self._last_trouble_ms is not None
                and now_ms - self._last_trouble_ms < _RECENT_TROUBLE_MS
            )
            or self.last_message_ms is None
        ):
            state = quality.HealthState.DEGRADED
        else:
            state = quality.HealthState.HEALTHY
        return FeedHealth(
            venue=self.venue,
            state=state,
            feed_mode=self.feed_mode,
            feed_state=feed_state,
            transport=self.transport,
            reconnects=self.reconnects,
            gaps_detected=self.gaps,
            as_of_ms=now_ms,
            last_message_ms=self.last_message_ms,
            lag_ms=lag,
            last_error=self.last_error,
        )
