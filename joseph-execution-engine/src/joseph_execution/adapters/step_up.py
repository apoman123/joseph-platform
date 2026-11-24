# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""RFC 6238 TOTP step-up verifier for sensitive actions (live consent).

Without a configured secret every verification fails (fail closed).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import struct

from joseph_execution.ports import services


def totp(secret_b32: str, at_ms: int, step_s: int = 30, digits: int = 6) -> str:
    """Computes a TOTP code.

    Args:
        secret_b32: Base32 shared secret.
        at_ms: Time in UTC epoch milliseconds.
        step_s: Time step.
        digits: Code length.

    Returns:
        Zero-padded code.
    """
    key = base64.b32decode(secret_b32.upper() + "=" * (-len(secret_b32) % 8))
    counter = struct.pack(">Q", at_ms // 1000 // step_s)
    digest = hmac.new(key, counter, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(value % (10**digits)).zfill(digits)


class TotpStepUpVerifier:
    """Verifies TOTP codes with a +/- one step window."""

    def __init__(self, secret_b32: str | None, clock: services.Clock) -> None:
        """Initializes the verifier.

        Args:
            secret_b32: Shared secret, ``None`` to disable step-up.
            clock: Time source.
        """
        self._secret = secret_b32
        self._clock = clock

    def verify(self, user_id: str, code: str) -> bool:
        """Checks a code.

        Args:
            user_id: User (single shared secret in this build).
            code: Code.

        Returns:
            True when valid.
        """
        del user_id
        if not self._secret or not code.isdigit():
            return False
        now = self._clock.now_ms()
        return any(
            hmac.compare_digest(totp(self._secret, now + d * 30_000), code)
            for d in (-1, 0, 1)
        )
