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

    HMAC-SHA1 over the big-endian 8-byte step counter, with RFC 4226
    dynamic truncation: the defaults authenticator apps use.

    Args:
        secret_b32: Base32 shared secret, case-insensitive, padding
            optional.
        at_ms: Time in UTC epoch milliseconds.
        step_s: Time step in seconds.
        digits: Code length.

    Returns:
        Zero-padded code.
    """
    # Authenticator secrets are usually shared without base32 padding, which
    # b32decode requires.
    key = base64.b32decode(secret_b32.upper() + "=" * (-len(secret_b32) % 8))
    counter = struct.pack(">Q", at_ms // 1000 // step_s)
    digest = hmac.new(key, counter, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(value % (10**digits)).zfill(digits)


class TotpStepUpVerifier:
    """Verifies 30-second TOTP codes with a +/- one step window."""

    def __init__(self, secret_b32: str | None, clock: services.Clock) -> None:
        """Initializes the verifier.

        Args:
            secret_b32: Base32 shared secret; ``None`` or empty makes every
                verification fail.
            clock: Time source.
        """
        self._secret = secret_b32
        self._clock = clock

    def verify(self, user_id: str, code: str) -> bool:
        """Checks a code.

        Fails closed: without a secret, or for a non-numeric code, nothing
        verifies. Codes are compared in constant time against the previous,
        current and next step, which tolerates clock drift of up to one
        step.

        Args:
            user_id: User (ignored: this build has one shared secret).
            code: Code the user typed.

        Returns:
            True when the code matches one of the three steps.
        """
        del user_id  # Unused.
        if not self._secret or not code.isdigit():
            return False
        now = self._clock.now_ms()
        return any(
            hmac.compare_digest(totp(self._secret, now + d * 30_000), code)
            for d in (-1, 0, 1)
        )
