# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Minimal Prometheus-style metrics registry."""

from __future__ import annotations

import threading
from collections.abc import Mapping
from typing import TypeAlias

# A metric name with its labels sorted by label name.
_Key: TypeAlias = tuple[str, tuple[tuple[str, str], ...]]


def _key(name: str, labels: Mapping[str, str] | None) -> _Key:
    """Builds a metric key.

    Args:
        name: Metric name.
        labels: Labels.

    Returns:
        Key, independent of the order of ``labels``.
    """
    return name, tuple(sorted((labels or {}).items()))


class MetricsRegistry:
    """Thread-safe counters and gauges rendered in Prometheus text format.

    Names are given without the ``joseph_`` prefix that ``render`` adds.
    """

    def __init__(self) -> None:
        """Initializes empty metrics."""
        self._counters: dict[_Key, float] = {}
        self._gauges: dict[_Key, float] = {}
        self._lock = threading.Lock()

    def increment(
        self, name: str, labels: Mapping[str, str] | None = None
    ) -> None:
        """Increments a counter.

        Args:
            name: Metric name.
            labels: Labels.
        """
        with self._lock:
            key = _key(name, labels)
            self._counters[key] = self._counters.get(key, 0.0) + 1.0

    def gauge(
        self,
        name: str,
        value: float,
        labels: Mapping[str, str] | None = None,
    ) -> None:
        """Sets a gauge.

        Args:
            name: Metric name.
            value: Value.
            labels: Labels.
        """
        with self._lock:
            self._gauges[_key(name, labels)] = value

    def value(
        self, name: str, labels: Mapping[str, str] | None = None
    ) -> float:
        """Reads a counter or gauge value.

        Args:
            name: Metric name.
            labels: Labels.

        Returns:
            The counter's value, else the gauge's, else 0.
        """
        key = _key(name, labels)
        with self._lock:
            return self._counters.get(key, self._gauges.get(key, 0.0))

    def render(self) -> str:
        """Renders all metrics in Prometheus text exposition format.

        Returns:
            Text ending in a newline. The alerting metrics
            ``order_unknown_total``, ``risk_block_total`` and
            ``reconcile_lag_ms`` are always present (0 until first set), so
            scrapers and alert rules see them before the first event.
        """
        lines: list[str] = []
        with self._lock:
            for kind, store in (
                ("counter", self._counters),
                ("gauge", self._gauges),
            ):
                names = sorted({name for name, _ in store})
                for name in names:
                    lines.append(f"# TYPE joseph_{name} {kind}")
                    for (metric, labels), value in sorted(store.items()):
                        if metric != name:
                            continue
                        label_text = ",".join(f'{k}="{v}"' for k, v in labels)
                        suffix = f"{{{label_text}}}" if label_text else ""
                        lines.append(f"joseph_{name}{suffix} {value}")
        for base in (
            "order_unknown_total",
            "risk_block_total",
            "reconcile_lag_ms",
        ):
            if not any(line.startswith(f"joseph_{base}") for line in lines):
                kind = "gauge" if base.endswith("_ms") else "counter"
                lines.append(f"# TYPE joseph_{base} {kind}")
                lines.append(f"joseph_{base} 0")
        return "\n".join(lines) + "\n"
