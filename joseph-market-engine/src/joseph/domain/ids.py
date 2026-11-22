"""Identifier generation.

Provides RFC 9562 UUIDv7 (time ordered) identifiers for server-generated
entities and deterministic content-derived identifiers for structures that
must stay stable across batch, streaming and replay runs (AOIs, events).
"""

from __future__ import annotations

import hashlib
import os
import threading
import time
import uuid

_LOCK = threading.Lock()
_LAST_MS = 0
_SEQ = 0


def uuid7(now_ms: int | None = None) -> str:
    """Generates a monotonic RFC 9562 version-7 UUID string.

    Within one millisecond a 12-bit counter keeps identifiers ordered.

    Args:
        now_ms: Optional timestamp override (UTC epoch ms), used in tests.

    Returns:
        Canonical lowercase UUID text, e.g. ``"0193...-7..."``.
    """
    global _LAST_MS, _SEQ  # noqa: PLW0603 - process-wide monotonic counter.
    with _LOCK:
        ms = int(time.time() * 1000) if now_ms is None else now_ms
        if ms <= _LAST_MS:
            ms = _LAST_MS
            _SEQ = (_SEQ + 1) & 0x0FFF
            if _SEQ == 0:
                ms += 1
        else:
            _SEQ = int.from_bytes(os.urandom(2), "big") & 0x03FF
        _LAST_MS = ms
        seq = _SEQ
    rand_b = int.from_bytes(os.urandom(8), "big") & ((1 << 62) - 1)
    value = (ms & ((1 << 48) - 1)) << 80
    value |= 0x7 << 76
    value |= seq << 64
    value |= 0b10 << 62
    value |= rand_b
    return str(uuid.UUID(int=value))


def stable_id(prefix: str, *parts: object) -> str:
    """Builds a deterministic identifier from its defining parts.

    The same inputs always yield the same id, so a structure detected in a
    batch run and in an incremental run share one identity.

    Args:
        prefix: Short readable prefix such as ``"fvg"``.
        *parts: Values that uniquely define the entity.

    Returns:
        Identifier like ``"fvg-3f1c2a9b0d4e"``.
    """
    digest = hashlib.sha256(
        "|".join(str(part) for part in parts).encode("utf-8")
    ).hexdigest()
    return f"{prefix}-{digest[:12]}"


def sha256_hex(payload: bytes) -> str:
    """Returns the hex SHA-256 digest of ``payload``.

    Args:
        payload: Bytes to hash.

    Returns:
        64-character lowercase hex digest.
    """
    return hashlib.sha256(payload).hexdigest()
