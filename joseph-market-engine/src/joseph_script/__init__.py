# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Public abstract interfaces.

Public abstract interfaces for the five kinds of isolated Joseph scripts.
"""

from __future__ import annotations

import abc
from collections.abc import Mapping
from typing import TYPE_CHECKING
from typing import Any

if TYPE_CHECKING:
    from joseph.scripts import worker


class Indicator(abc.ABC):
    """Base class for closed-bar visual calculations without trading authority.

    Subclasses implement on_bar and publish plots/drawings through the context.
    Instances are fresh per run; persistent run state belongs in ctx.state.
    """

    @abc.abstractmethod
    def on_bar(self, ctx: worker.Context) -> None:
        """Calculates indicator outputs when another source bar has closed.

        Args:
            ctx: Current closed-bar history, declared inputs and visual API.

        Returns:
            None; outputs are published through context visual methods.
        """
        ...


class Strategy(abc.ABC):
    """Base class for visual studies.

    Base class for visual studies with an isolated simulated trading account.

    Orders fill on subsequent source bars; these scripts cannot place broker
    orders. Existing account/deployment/consent gates remain authoritative.
    """

    @abc.abstractmethod
    def on_bar(self, ctx: worker.Context) -> None:
        """Calculates plots and queues simulated entry or exit orders.

        Args:
            ctx: Closed-bar data, inputs, visual API and simulated account.

        Returns:
            None; plots and simulated order intents are recorded by the context.
        """
        ...


class Library(abc.ABC):
    """Base class exporting reusable code without a standalone chart instance.

    Consumers import an alias pinned to an immutable, tenant-owned version.
    """

    @abc.abstractmethod
    def exports(self) -> Mapping[str, Any]:
        """Provides the public functions, classes and constants of this library.

        Returns:
            Nonempty mapping from public Python identifiers to reusable objects.
            Private implementation helpers stay outside the exported namespace.
        """
        ...


class Filter(abc.ABC):
    """Base class for Python market-universe filtering."""

    @abc.abstractmethod
    def matches(self, ctx: worker.Context) -> bool:
        """Tests one instrument after its selected source history is available.

        Args:
            ctx: Instrument identity, closed bars, inputs and available metrics.

        Returns:
            True to include the instrument, False to exclude it. Missing data
            is reported separately by the scanner and never inferred as a match.
        """
        ...


class Alert(abc.ABC):
    """Base class for Python alert conditions evaluated on closed market bars.

    Subclasses publish messages through ctx.alert; the host routes accepted
    events through the existing durable notification outbox, never to trading.
    """

    @abc.abstractmethod
    def on_bar(self, ctx: worker.Context) -> None:
        """Evaluates custom conditions and publishes matching alert messages.

        Args:
            ctx: Closed-bar history, inputs and the alert event publication API.

        Returns:
            None; matching events are recorded with bar-time idempotency keys.
        """
        ...
