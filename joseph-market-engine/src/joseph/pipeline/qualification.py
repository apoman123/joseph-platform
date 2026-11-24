# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Synchronous qualification gate over cached screening reports."""

from __future__ import annotations

import logging
from collections.abc import Iterable

from joseph.domain import clock as clock_lib
from joseph.domain import instruments
from joseph.screening.app import service as screening_service
from joseph.screening.domain import policy
from joseph.strategy.domain import tristate

_LOG = logging.getLogger(__name__)


class CachedQualificationGate:
    """Implements the strategy ``QualificationGate`` port.

    The strategy runtime asks synchronously; the live pipeline refreshes the
    cache asynchronously. Missing data is UNKNOWN, never qualified.
    """

    def __init__(
        self,
        service: screening_service.QualificationService,
        clock: clock_lib.Clock,
    ) -> None:
        """Initializes the gate.

        Args:
            service: Qualification service.
            clock: Time source.
        """
        self._service = service
        self._clock = clock
        self._status: dict[str, policy.Status] = {}
        self._qualified_since: dict[str, int] = {}
        self._ranked: dict[instruments.Venue, list[str]] = {}

    async def refresh(self, venues: Iterable[instruments.Venue]) -> None:
        """Reloads reports for venues (errors leave the cache untouched).

        Args:
            venues: Venues to refresh.
        """
        now = self._clock.now_ms()
        for venue in set(venues):
            try:
                report = await self._service.report(venue)
            except Exception:
                _LOG.warning("qualification refresh failed for %s", venue)
                continue
            ranked: list[str] = []
            for record in report.records:
                key = str(record.instrument_id)
                ranked.append(key)
                self._status[key] = record.status
                if record.status is policy.Status.QUALIFIED:
                    self._qualified_since.setdefault(key, now)
                else:
                    self._qualified_since.pop(key, None)
            self._ranked[venue] = ranked

    def ranked(self, venue: instruments.Venue) -> list[str]:
        """Returns instrument ids of a venue in report rank order.

        Args:
            venue: Venue.

        Returns:
            Instrument ids (empty before the first refresh).
        """
        return list(self._ranked.get(venue, []))

    def is_qualified(self, instrument_id: str) -> bool:
        """Checks the cached status.

        Args:
            instrument_id: Canonical instrument id.

        Returns:
            True when the latest report says QUALIFIED.
        """
        return self._status.get(instrument_id) is policy.Status.QUALIFIED

    def qualification(
        self, instrument_id: str, at_ms: int, min_minutes: int
    ) -> tristate.TriState:
        """Returns the qualification of an instrument at a time.

        Args:
            instrument_id: Canonical instrument id.
            at_ms: Bar close.
            min_minutes: Minimum continuous qualification time.

        Returns:
            TRUE, FALSE, or UNKNOWN when no report covers the instrument.
        """
        status = self._status.get(instrument_id)
        if status is None or status is policy.Status.INSUFFICIENT_DATA:
            return tristate.TriState.UNKNOWN
        if status is policy.Status.REJECTED:
            return tristate.TriState.FALSE
        since = self._qualified_since.get(instrument_id, at_ms)
        if min_minutes > 0 and at_ms - since < min_minutes * 60_000:
            return tristate.TriState.FALSE
        return tristate.TriState.TRUE
