# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Minimal Prometheus text-format metrics registry (no third-party deps).

Supports counters, gauges and histograms with string labels. Rendering
follows the Prometheus text exposition format 0.0.4. Updates take a lock,
so metrics may be updated from worker threads as well as the event loop.
"""

from __future__ import annotations

import bisect
import threading
from collections.abc import Iterable
from collections.abc import Sequence
from typing import TypeAlias
from typing import cast

# Label set as sorted ``(name, value)`` pairs, usable as a dict key.
LabelKey: TypeAlias = tuple[tuple[str, str], ...]

_DEFAULT_BUCKETS: tuple[float, ...] = (
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
)


def _labels(values: dict[str, str]) -> LabelKey:
    """Normalizes a label mapping into a sorted tuple key.

    Args:
        values: Label names to values.

    Returns:
        Sorted tuple of pairs.
    """
    return tuple(sorted((str(k), str(v)) for k, v in values.items()))


def _format_labels(key: LabelKey, extra: Iterable[tuple[str, str]] = ()) -> str:
    """Formats labels for exposition.

    Args:
        key: Label key.
        extra: Additional pairs (e.g. histogram ``le``).

    Returns:
        ``{a="1",b="2"}`` or an empty string.
    """
    pairs = list(key) + list(extra)
    if not pairs:
        return ""
    # chr(92) is a backslash, escaped by doubling; chr(34) is a double
    # quote, dropped rather than escaped.
    escaped = (
        f'{name}="{value.replace(chr(92), chr(92) * 2).replace(chr(34), "")}"'
        for name, value in pairs
    )
    body = ",".join(escaped)
    return f"{{{body}}}"


class _ScalarMetric:
    """Shared storage for single-value-per-label-set metrics.

    Attributes:
        kind: Prometheus metric type written on the ``# TYPE`` line.
        name: Metric name.
        help_text: Description written on the ``# HELP`` line.
    """

    kind = "untyped"

    def __init__(self, name: str, help_text: str) -> None:
        """Initializes the metric.

        Args:
            name: Metric name.
            help_text: Description written on the ``# HELP`` line.
        """
        self.name = name
        self.help_text = help_text
        self._values: dict[LabelKey, float] = {}
        self._lock = threading.Lock()

    def value(self, **labels: str) -> float:
        """Returns the current value for a label set.

        Args:
            **labels: Label values.

        Returns:
            Current value (0 when never set), read under the writers' lock.
        """
        key = _labels(labels)
        with self._lock:
            return self._values.get(key, 0.0)

    def _add(self, amount: float, labels: dict[str, str]) -> None:
        """Adds ``amount`` to the value of a label set.

        Args:
            amount: Delta.
            labels: Label values.
        """
        key = _labels(labels)
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + amount

    def render(self) -> list[str]:
        """Renders exposition lines.

        Returns:
            Sample lines.
        """
        with self._lock:
            items = sorted(self._values.items())
        return [f"{self.name}{_format_labels(k)} {v}" for k, v in items]


class Counter(_ScalarMetric):
    """Monotonically increasing counter.

    Attributes:
        kind: Always ``counter``.
    """

    kind = "counter"

    def inc(self, amount: float = 1.0, **labels: str) -> None:
        """Increments the counter.

        Args:
            amount: Non-negative increment.
            **labels: Label values.

        Raises:
            ValueError: If ``amount`` is negative.
        """
        if amount < 0:
            raise ValueError("counters only increase")
        self._add(amount, labels)


class Gauge(_ScalarMetric):
    """Value that can go up and down.

    Attributes:
        kind: Always ``gauge``.
    """

    kind = "gauge"

    def set(self, value: float, **labels: str) -> None:
        """Sets the gauge.

        Args:
            value: New value.
            **labels: Label values.
        """
        with self._lock:
            self._values[_labels(labels)] = value

    def inc(self, amount: float = 1.0, **labels: str) -> None:
        """Adjusts the gauge by ``amount`` (may be negative).

        Args:
            amount: Delta.
            **labels: Label values.
        """
        self._add(amount, labels)


class Histogram:
    """Cumulative-bucket histogram.

    Attributes:
        kind: Always ``histogram``.
        name: Metric name.
        help_text: Description written on the ``# HELP`` line.
        buckets: Inclusive upper bounds, ascending; ``+Inf`` is implied.
    """

    kind = "histogram"

    def __init__(
        self,
        name: str,
        help_text: str,
        buckets: Sequence[float] = _DEFAULT_BUCKETS,
    ) -> None:
        """Initializes the histogram.

        Args:
            name: Metric name.
            help_text: Description written on the ``# HELP`` line.
            buckets: Bucket upper bounds, in any order (they are sorted).
        """
        self.name = name
        self.help_text = help_text
        self.buckets = tuple(sorted(buckets))
        self._counts: dict[LabelKey, list[int]] = {}
        self._sums: dict[LabelKey, float] = {}
        self._lock = threading.Lock()

    def observe(self, value: float, **labels: str) -> None:
        """Records an observation.

        Args:
            value: Observed value.
            **labels: Label values.
        """
        key = _labels(labels)
        with self._lock:
            # One slot per bound plus a last one for values above them all.
            counts = self._counts.setdefault(key, [0] * (len(self.buckets) + 1))
            # bisect_left puts a value equal to a bound in that bound's
            # slot, matching the inclusive ``le`` of Prometheus buckets.
            counts[bisect.bisect_left(self.buckets, value)] += 1
            self._sums[key] = self._sums.get(key, 0.0) + value

    def render(self) -> list[str]:
        """Renders exposition lines.

        Returns:
            Sample lines including ``_bucket``, ``_sum`` and ``_count``.
        """
        lines: list[str] = []
        with self._lock:
            items = sorted(self._counts.items())
            sums = dict(self._sums)
        for key, counts in items:
            running = 0
            # ``counts`` has one more slot than ``buckets``; it is the
            # ``+Inf`` bucket added after the loop.
            for bound, count in zip(self.buckets, counts, strict=False):
                running += count
                lines.append(
                    f"{self.name}_bucket"
                    f"{_format_labels(key, [('le', repr(bound))])} {running}"
                )
            running += counts[-1]
            lines.append(
                f"{self.name}_bucket{_format_labels(key, [('le', '+Inf')])}"
                f" {running}"
            )
            lines.append(f"{self.name}_sum{_format_labels(key)} {sums[key]}")
            lines.append(f"{self.name}_count{_format_labels(key)} {running}")
        return lines


Metric: TypeAlias = Counter | Gauge | Histogram


class MetricsRegistry:
    """Holds metrics and renders the exposition document."""

    def __init__(self) -> None:
        """Initializes an empty registry."""
        self._metrics: dict[str, Metric] = {}
        self._lock = threading.Lock()

    def counter(self, name: str, help_text: str) -> Counter:
        """Returns (creating if needed) a counter.

        Args:
            name: Metric name.
            help_text: Description.

        Returns:
            The counter.
        """
        return cast(Counter, self._register(Counter(name, help_text)))

    def gauge(self, name: str, help_text: str) -> Gauge:
        """Returns (creating if needed) a gauge.

        Args:
            name: Metric name.
            help_text: Description.

        Returns:
            The gauge.
        """
        return cast(Gauge, self._register(Gauge(name, help_text)))

    def histogram(self, name: str, help_text: str) -> Histogram:
        """Returns (creating if needed) a histogram.

        Args:
            name: Metric name.
            help_text: Description.

        Returns:
            The histogram.
        """
        return cast(Histogram, self._register(Histogram(name, help_text)))

    def _register(self, candidate: Metric) -> Metric:
        """Registers ``candidate`` unless a metric with its name exists.

        A second request for an existing name returns the first metric, so
        modules can declare the metrics they use independently.

        Args:
            candidate: Newly constructed metric.

        Returns:
            The registered metric with that name.

        Raises:
            TypeError: If the name is registered with another kind.
        """
        with self._lock:
            existing = self._metrics.setdefault(candidate.name, candidate)
        if type(existing) is not type(candidate):
            raise TypeError(
                f"metric {candidate.name} already registered as {existing.kind}"
            )
        return existing

    def render(self) -> str:
        """Renders all metrics in Prometheus text format.

        Returns:
            Exposition document ending with a newline.
        """
        lines: list[str] = []
        with self._lock:
            metrics = sorted(self._metrics.values(), key=lambda m: m.name)
        for metric in metrics:
            lines.append(f"# HELP {metric.name} {metric.help_text}")
            lines.append(f"# TYPE {metric.name} {metric.kind}")
            lines.extend(metric.render())
        return "\n".join(lines) + "\n"


def standard_registry() -> MetricsRegistry:
    """Creates a registry pre-populated with the platform's core metrics.

    Returns:
        Registry with feed and strategy reliability metrics registered.
    """
    registry = MetricsRegistry()
    registry.counter("joseph_feed_gap_total", "Detected candle gaps.")
    registry.counter("joseph_ws_reconnect_total", "WebSocket reconnects.")
    registry.counter("joseph_venue_errors_total", "Venue request failures.")
    registry.gauge("joseph_feed_lag_ms", "Last message age per feed.")
    registry.histogram(
        "joseph_strategy_eval_lag_seconds",
        "Delay from bar close to strategy evaluation.",
    )
    registry.counter("joseph_http_requests_total", "HTTP requests served.")
    return registry
