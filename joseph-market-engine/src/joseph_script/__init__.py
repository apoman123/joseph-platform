# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Public abstract interfaces.

Public abstract interfaces for the five kinds of Joseph scripts. Every
script, including the platform's built-in indicators, strategies, filters and
alerts and installed plugin packages, is one concrete subclass of exactly one
of ``Indicator``, ``Strategy``, ``Library``, ``Filter`` or ``Alert``.

Subclasses may declare presentation metadata and typed inputs as class
attributes; the host reads them without running ``on_bar``::

    class Average(Indicator):
        ID = "average"
        TITLE = "Average"
        INPUTS = {"length": Input(20, title="Length", minimum=1, maximum=500)}

        def on_bar(self, ctx):
            ctx.plot("Average", ctx.sma(length=int(ctx.input("length"))))
"""

from __future__ import annotations

import abc
import dataclasses
from collections.abc import Mapping
from typing import TYPE_CHECKING
from typing import Any
from typing import ClassVar

if TYPE_CHECKING:
    from joseph.scripts import worker


@dataclasses.dataclass(frozen=True, slots=True)
class Input:
    """A declared, user-configurable script input.

    Attributes:
        default: JSON default value (number, string, boolean or list).
        title: Settings-dialog label; the input name when empty.
        minimum: Optional inclusive numeric lower bound.
        maximum: Optional inclusive numeric upper bound.
        options: Optional accepted values, shown as a choice list.
        step: Optional numeric spinner step.
        group: Optional settings-dialog section, such as "Style".
        tooltip: Optional help text shown next to the input.
    """

    default: Any
    title: str = ""
    minimum: float | None = None
    maximum: float | None = None
    options: tuple[Any, ...] | None = None
    step: float | None = None
    group: str = ""
    tooltip: str = ""

    def to_json(self) -> dict[str, Any]:
        """Describes the input as a JSON object for catalogs and dialogs.

        Returns:
            The declaration with ``options`` as a list and unset fields as
            None or empty strings.
        """
        return {
            "default": self.default,
            "title": self.title,
            "minimum": self.minimum,
            "maximum": self.maximum,
            "options": None if self.options is None else list(self.options),
            "step": self.step,
            "group": self.group,
            "tooltip": self.tooltip,
        }


@dataclasses.dataclass(frozen=True, slots=True)
class Request:
    """A declared additional symbol or resolution read through ctx.request.

    Attributes:
        tf: Resolution such as "1d"; source bars become visible only after
            they have closed. With ``intrabars`` it may be "auto": the host
            picks the lower timeframe for the chart resolution (1s on
            seconds charts, 1m intraday, 5m on daily and 1h on weekly and
            monthly charts; none on 1s and 1m charts).
        instrument_id: Optional canonical symbol; the chart symbol when None.
        limit: Maximum closed source bars, one through five hundred (five
            thousand for intrabars).
        intrabars: Lower-timeframe bars inside the chart bars, read with
            ``ctx.intrabars``; the host fetches only the newest chart bars'
            intrabars that fit ``limit`` (from the run's ``window_from_ms``
            when given).
        tf_input: Optional input name whose value, unless "auto", replaces
            ``tf``.
        unless: Optional optional-bar field (such as "taker_buy_volume");
            intrabars are fetched only for chart bars that lack it.
        unless_input: Optional boolean input; ``unless`` applies only while
            it is true.
    """

    tf: str
    instrument_id: str | None = None
    limit: int = 500
    intrabars: bool = False
    tf_input: str = ""
    unless: str = ""
    unless_input: str = ""

    def to_json(self) -> dict[str, Any]:
        """Describes the request in the script-version JSON shape.

        Returns:
            Mapping with tf, instrument_id and limit, plus intrabars,
            tf_input, unless and unless_input when set.
        """
        data: dict[str, Any] = {
            "tf": self.tf,
            "instrument_id": self.instrument_id,
            "limit": self.limit,
        }
        if self.intrabars:
            data["intrabars"] = True
        if self.tf_input:
            data["tf_input"] = self.tf_input
        if self.unless:
            data["unless"] = self.unless
        if self.unless_input:
            data["unless_input"] = self.unless_input
        return data


class Script:
    """Optional declarations shared by every script role.

    Attributes:
        ID: Stable catalog slug for built-in and plugin scripts (lowercase
            letters, digits, "-" and "_"); tenant scripts use their saved id.
        TITLE: Display name; the class name when empty.
        SHORT_TITLE: Compact legend name; TITLE when empty.
        DESCRIPTION: One or two plain-text sentences for catalogs.
        OVERLAY: Whether plots share the price pane (True) or get their own.
        INPUTS: Declared inputs in display order, keyed by input name.
        REQUESTS: Additional series aliases read with ctx.request.
        CATEGORY: Optional catalog section such as "Trend" or "Volume".
        PERIODS: Calendar periods ("1d", "1w", "1M") whose bounds the host
            computes for every bar on the instrument's session calendar,
            read with ``ctx.period``.
    """

    ID: ClassVar[str] = ""
    TITLE: ClassVar[str] = ""
    SHORT_TITLE: ClassVar[str] = ""
    DESCRIPTION: ClassVar[str] = ""
    OVERLAY: ClassVar[bool] = True
    INPUTS: ClassVar[Mapping[str, Input]] = {}
    REQUESTS: ClassVar[Mapping[str, Request]] = {}
    CATEGORY: ClassVar[str] = ""
    PERIODS: ClassVar[tuple[str, ...]] = ()


class Indicator(Script, abc.ABC):
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


class Strategy(Script, abc.ABC):
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


class Library(Script, abc.ABC):
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


FILTER_METRICS: tuple[str, ...] = ("order_book", "open_interest", "funding")
"""Market data a filter may declare in ``Filter.METRICS``.

* ``order_book``: ``ctx.snapshot["order_book"]``, a contract ``OrderBook``
  of up to one hundred levels per side, or None when unavailable;
* ``open_interest`` and ``funding``: fetched for derivatives when the bulk
  snapshot lacks them; they stay None when the venue has none.
"""


class Unavailable(Exception):  # noqa: N818 - Public SDK name reads as a scan outcome, not a failure.
    """Missing or stale data that keeps a filter from deciding.

    ``Filter.matches`` raises it instead of guessing; the scanner reports
    the instrument as unavailable with the message, never as a match.
    """


class Filter(Script, abc.ABC):
    """Base class for Python market-universe filtering.

    Attributes:
        METRICS: Data beyond the snapshot that the scanner loads before
            ``matches`` (names from ``FILTER_METRICS``).
    """

    METRICS: ClassVar[tuple[str, ...]] = ()

    @abc.abstractmethod
    def matches(self, ctx: worker.Context) -> bool:
        """Tests one instrument after its selected source history is available.

        Args:
            ctx: Instrument identity, closed bars, inputs and available metrics.

        Returns:
            True to include the instrument, False to exclude it. Missing data
            is reported separately by the scanner and never inferred as a
            match: raise ``Unavailable`` when required data is missing.
        """
        ...


class Alert(Script, abc.ABC):
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


ROLES: tuple[type[Script], ...] = (Indicator, Strategy, Library, Filter, Alert)
"""The five abstract script roles, in catalog order."""


def role_of(cls: type) -> str | None:
    """Names the single script role a class implements.

    Args:
        cls: Any class.

    Returns:
        "indicator", "strategy", "library", "filter" or "alert" when the class
        inherits exactly one role base, otherwise None.
    """
    found = [
        base.__name__.lower()
        for base in ROLES
        if isinstance(cls, type) and issubclass(cls, base)
    ]
    return found[0] if len(found) == 1 else None
