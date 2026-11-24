# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Wire helpers shared by the live venue adapters.

* Canonical instrument ids are ``{venue}:perp:{venue_market_id}`` (the
  market engine's convention, e.g. ``binance:perp:BTCUSDT``,
  ``mexc:perp:BTC_USDT``, ``hyperliquid:perp:BTC``).
* Decimals go on the wire as plain strings (never floats, never exponents).
* Joseph client order ids are ``j`` + 30 hex digits (``ids.client_order_id``)
  with an optional protective suffix (``-sl``, ``-tp1`` …). Venues that only
  accept a 128-bit id (Hyperliquid ``cloid``) get a reversible encoding so
  stream events map back to the OMS order after a restart.
"""

from __future__ import annotations

import decimal
import hashlib
import re

from joseph_execution.domain import decimals
from joseph_execution.domain import errors

_JOSEPH_CID = re.compile(r"^j([0-9a-f]{30})(?:-(sl|tp(\d{1,3})))?$")
_CLOID = re.compile(r"^0x([0-9a-f]{2})([0-9a-f]{30})$")
_TAG_PRIMARY = 0x00
_TAG_STOP = 0x01
_TAG_TP_BASE = 0x10
_TAG_HASHED = 0xFF


def symbol_of(venue: str, instrument_id: str) -> str:
    """Extracts the venue market id from a canonical perp instrument id.

    Args:
        venue: Expected venue code.
        instrument_id: ``{venue}:perp:{symbol}``.

    Returns:
        Venue symbol.

    Raises:
        AdapterUnavailableError: ``UNSUPPORTED`` for another venue or a
            non-perpetual market (refused before sending).
    """
    parts = instrument_id.split(":", 2)
    if len(parts) != 3 or parts[0] != venue or parts[1] != "perp":
        raise errors.AdapterUnavailableError(
            f"{venue} trades perpetuals only; got {instrument_id!r}",
            code="UNSUPPORTED",
        )
    return parts[2]


def instrument_of(venue: str, symbol: str) -> str:
    """Builds the canonical perp instrument id.

    Args:
        venue: Venue code.
        symbol: Venue symbol.

    Returns:
        Instrument id.
    """
    return f"{venue}:perp:{symbol}"


def text(value: decimal.Decimal) -> str:
    """Formats a decimal for the wire (no exponent, no trailing zeros).

    Args:
        value: Finite decimal.

    Returns:
        Text such as ``0.001`` or ``95000``.
    """
    return decimals.fmt(value)


def dec(value: object) -> decimal.Decimal:
    """Parses a venue number (string, int or JSON decimal).

    Args:
        value: Venue value.

    Returns:
        Decimal (``0`` for ``None`` or empty strings).
    """
    if value is None or value == "":
        return decimals.ZERO
    return decimals.to_decimal(value)


def opt_dec(value: object) -> decimal.Decimal | None:
    """Parses an optional venue number.

    Args:
        value: Venue value.

    Returns:
        Decimal, or ``None`` for missing/zero-like placeholders.
    """
    if value is None or value == "":
        return None
    return decimals.to_decimal(value)


def cloid_of(client_order_id: str) -> str:
    """Encodes a client order id as a 128-bit hex ``cloid``.

    Joseph ids encode reversibly: a tag byte (primary, stop-loss or
    take-profit leg) followed by the 30 hex digits. Other ids hash (tag
    ``ff``) and can only be mapped back by the adapter that sent them.

    Args:
        client_order_id: Client order id.

    Returns:
        ``0x`` + 32 lowercase hex digits.
    """
    match = _JOSEPH_CID.match(client_order_id)
    if match is None:
        digest = hashlib.sha256(client_order_id.encode("utf-8")).hexdigest()
        return f"0x{_TAG_HASHED:02x}{digest[:30]}"
    body, leg, tp_index = match.groups()
    if leg is None:
        tag = _TAG_PRIMARY
    elif leg == "sl":
        tag = _TAG_STOP
    else:
        index = int(tp_index)
        if not 1 <= index <= _TAG_HASHED - _TAG_TP_BASE - 1:
            digest = hashlib.sha256(client_order_id.encode()).hexdigest()
            return f"0x{_TAG_HASHED:02x}{digest[:30]}"
        tag = _TAG_TP_BASE + index
    return f"0x{tag:02x}{body}"


def client_id_of_cloid(cloid: str) -> str | None:
    """Decodes a ``cloid`` produced by ``cloid_of``.

    Args:
        cloid: ``0x`` + 32 hex digits.

    Returns:
        The client order id, or ``None`` for hashed/foreign cloids.
    """
    match = _CLOID.match(cloid.lower())
    if match is None:
        return None
    tag = int(match.group(1), 16)
    body = match.group(2)
    if tag == _TAG_PRIMARY:
        return f"j{body}"
    if tag == _TAG_STOP:
        return f"j{body}-sl"
    if _TAG_TP_BASE < tag < _TAG_HASHED:
        return f"j{body}-tp{tag - _TAG_TP_BASE}"
    return None


_COMPACT_SAFE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
_COMPACT_STOP = re.compile(r"^s([0-9a-f]{30})$")
_COMPACT_TP = re.compile(r"^t([0-9a-z])([0-9a-f]{30})$")
_BASE36 = "0123456789abcdefghijklmnopqrstuvwxyz"


def compact_id_of(client_order_id: str) -> str:
    """Encodes a client order id in at most 32 safe characters.

    Bitget (``clientOid``) and MEXC (``externalOid``) accept 32 characters,
    while protective legs (``j…-tp1``) need 35. Joseph ids encode
    reversibly: ``j<hex>`` (primary, unchanged), ``s<hex>`` (stop-loss) and
    ``t<n><hex>`` (take-profit leg ``n``, base 36). Other ids pass through
    when short and safe, else hash to ``h…`` (one-way).

    Args:
        client_order_id: Client order id.

    Returns:
        Venue client id.
    """
    match = _JOSEPH_CID.match(client_order_id)
    if match is not None:
        body, leg, tp_index = match.groups()
        if leg is None:
            return client_order_id
        if leg == "sl":
            return f"s{body}"
        index = int(tp_index)
        if 1 <= index < len(_BASE36):
            return f"t{_BASE36[index]}{body}"
    elif _COMPACT_SAFE.match(client_order_id) and not (
        _COMPACT_STOP.match(client_order_id)
        or _COMPACT_TP.match(client_order_id)
    ):
        return client_order_id
    digest = hashlib.sha256(client_order_id.encode("utf-8")).hexdigest()
    return f"h{digest[:31]}"


def client_id_of_compact(venue_id: str) -> str | None:
    """Decodes ``compact_id_of``.

    Args:
        venue_id: Venue client id.

    Returns:
        The client order id, or ``None`` for hashed ids.
    """
    stop = _COMPACT_STOP.match(venue_id)
    if stop is not None:
        return f"j{stop.group(1)}-sl"
    take = _COMPACT_TP.match(venue_id)
    if take is not None:
        return f"j{take.group(2)}-tp{_BASE36.index(take.group(1))}"
    if venue_id.startswith("h") and len(venue_id) == 32:
        return None
    return venue_id


def index_of(client_order_id: str, bits: int) -> int:
    """Derives a stable integer client index from a client order id.

    Used where the venue only accepts a bounded integer (Lighter's
    ``client_order_index``). The mapping is a truncated SHA-256, so the
    adapter keeps the reverse map; collisions are astronomically unlikely
    for one account's order history.

    Args:
        client_order_id: Client order id.
        bits: Width of the venue integer.

    Returns:
        Integer in ``[1, 2**bits - 1]``.
    """
    digest = hashlib.sha256(client_order_id.encode("utf-8")).digest()
    value = int.from_bytes(digest[:8], "big") % ((1 << bits) - 1)
    return value + 1
