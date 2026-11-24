# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""HMAC request signing shared by the centralized venues.

* Binance and MEXC: lowercase hex HMAC-SHA256 of the venue's payload
  string (``hmac_sha256_hex``).
* Bitget: Base64 of the raw HMAC-SHA256 digest (``hmac_sha256_b64``) of
  ``timestamp + METHOD + requestPath[?query] + body``, sent with the API
  passphrase.

Hyperliquid (EIP-712) and Lighter (native SignerClient) sign in their own
adapter modules because they need optional dependencies.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import urllib.parse
from collections.abc import Sequence


def hmac_sha256_hex(secret: str, payload: str) -> str:
    """Signs a payload with HMAC-SHA256 and returns lowercase hex.

    Args:
        secret: API secret.
        payload: Text to sign.

    Returns:
        64-character hex digest.
    """
    return hmac.new(
        secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def hmac_sha256_b64(secret: str, payload: str) -> str:
    """Signs a payload with HMAC-SHA256 and returns Base64.

    Args:
        secret: API secret.
        payload: Text to sign.

    Returns:
        Base64 digest.
    """
    digest = hmac.new(
        secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
    ).digest()
    return base64.b64encode(digest).decode("ascii")


def encode_query(params: Sequence[tuple[str, str]]) -> str:
    """Encodes parameters in the given order (signed exactly as sent).

    Args:
        params: ``(name, value)`` pairs.

    Returns:
        ``a=1&b=2`` with RFC 3986 percent-encoding.
    """
    return urllib.parse.urlencode(list(params), quote_via=urllib.parse.quote)
