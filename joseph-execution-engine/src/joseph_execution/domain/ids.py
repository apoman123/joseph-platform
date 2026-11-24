# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Identifier and hashing helpers."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid

# Process-wide UUIDv7 generator state: ``uuid7`` must see the last
# timestamp and counter of every caller in the process to stay monotonic.
# ``_LOCK`` makes the read-modify-write safe if callers run on several
# threads.
_LOCK = threading.Lock()
_STATE = {"last_ms": 0, "seq": 0}


def uuid7(now_ms: int | None = None) -> str:
    """Generates a monotonic RFC 9562 UUIDv7.

    Ids sort in generation order within the process, even when the clock
    stalls or steps back: the timestamp then stays at the last one used and
    the 12-bit counter in ``rand_a`` (RFC 9562 method 1) increments; when
    the counter wraps, the timestamp advances by one millisecond.

    Args:
        now_ms: Optional timestamp override in UTC epoch milliseconds.

    Returns:
        Canonical UUID text.
    """
    with _LOCK:
        ms = int(time.time() * 1000) if now_ms is None else now_ms
        if ms <= _STATE["last_ms"]:
            ms = _STATE["last_ms"]
            _STATE["seq"] = (_STATE["seq"] + 1) & 0x0FFF
            if _STATE["seq"] == 0:
                ms += 1
        else:
            # A random 10-bit start leaves at least 3072 increments of
            # headroom in the 12-bit counter within one millisecond.
            _STATE["seq"] = int.from_bytes(os.urandom(2), "big") & 0x03FF
        _STATE["last_ms"] = ms
        seq = _STATE["seq"]
    rand_b = int.from_bytes(os.urandom(8), "big") & ((1 << 62) - 1)
    # Layout: 48-bit ms timestamp, version 7, 12-bit counter, variant 0b10,
    # 62 random bits.
    value = (ms & ((1 << 48) - 1)) << 80
    value |= 0x7 << 76
    value |= seq << 64
    value |= 0b10 << 62
    value |= rand_b
    return str(uuid.UUID(int=value))


def canonical_json(payload: object) -> str:
    """Serializes JSON deterministically (sorted keys, compact).

    Args:
        payload: JSON-compatible value.

    Returns:
        Canonical JSON text.
    """
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


def sha256_hex(text: str) -> str:
    """Returns the hex SHA-256 of UTF-8 text.

    Args:
        text: Input text.

    Returns:
        64-character hex digest.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def stable_id(prefix: str, *parts: object) -> str:
    """Builds a deterministic identifier from defining parts.

    Args:
        prefix: Readable prefix.
        *parts: Values defining the entity; joined with ``|`` after ``str``.

    Returns:
        ``<prefix>_`` plus the first 16 hex digits of the parts' SHA-256,
        e.g. ``pos_3f1c2a9b0d4e5f60``.
    """
    digest = sha256_hex("|".join(str(part) for part in parts))
    return f"{prefix}_{digest[:16]}"


def client_order_id(intent_id: str) -> str:
    """Derives a venue-safe client order id (<= 31 chars) from an intent id.

    Args:
        intent_id: UUIDv7 text.

    Returns:
        ``j`` plus the first 30 hex digits of the intent id without dashes
        (letters and digits only), such as ``j0193f0a0...``.
    """
    return "j" + intent_id.replace("-", "")[:30]
