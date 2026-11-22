"""Data provenance, feed modes and health states.

Every value shown to a user or consumed by a strategy carries its provenance
so that degraded, replayed or synthetic data is never mistaken for live data.
"""

from __future__ import annotations

import dataclasses
import enum


class FeedMode(enum.StrEnum):
    """Requested transport policy (contract ``FeedMode``).

    AUTO prefers native WebSocket and may fall back to visible REST polling
    for non-critical chart data. NATIVE_WS never falls back; it reports
    UNSUPPORTED instead.
    """

    AUTO = "AUTO"
    NATIVE_WS = "NATIVE_WS"
    REST_POLL = "REST_POLL"
    EXTERNAL_VENDOR = "EXTERNAL_VENDOR"
    REPLAY = "REPLAY"


class FeedState(enum.StrEnum):
    """Observed provenance of delivered data (contract ``FeedState``)."""

    LIVE = "LIVE"
    POLL = "POLL"
    REPLAY = "REPLAY"
    STALE = "STALE"
    DEMO = "DEMO"
    UNAVAILABLE = "UNAVAILABLE"
    CONNECTING = "CONNECTING"

    @property
    def tradable(self) -> bool:
        """Whether data in this state may support new real exposure.

        Only native live data qualifies. Polled data is display-grade, and
        replay/demo data must never reach a real order.
        """
        return self is FeedState.LIVE


class HealthState(enum.StrEnum):
    """Health of a feed or adapter (contract ``HealthState``)."""

    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    STALE = "STALE"
    UNAVAILABLE = "UNAVAILABLE"


class Transport(enum.StrEnum):
    """Concrete transport currently delivering a feed."""

    NATIVE_WS = "NATIVE_WS"
    REST_POLL = "REST_POLL"
    REPLAY = "REPLAY"
    MOCK = "MOCK"
    NONE = "NONE"


@dataclasses.dataclass(frozen=True, slots=True)
class DataQuality:
    """Quality summary attached to derived outputs.

    Attributes:
        feed_state: Provenance of the underlying candles.
        missing_bars: Number of expected bars that were absent.
        coverage_pct: Optional coverage of required sub-data, e.g. intrabar
            volume coverage for order-flow estimates, as a decimal string.
    """

    feed_state: FeedState
    missing_bars: int = 0
    coverage_pct: str | None = None

    def to_contract(self) -> dict[str, object]:
        """Serializes to the contract ``DataQuality`` object.

        Returns:
            JSON-compatible dict.
        """
        return {
            "feed_state": self.feed_state.value,
            "missing_bars": self.missing_bars,
            "coverage_pct": self.coverage_pct,
        }
