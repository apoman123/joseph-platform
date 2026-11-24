# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""HMAC request signing shared by the centralized venues.

* Binance and MEXC: lowercase hex HMAC-SHA256 of the venue's payload
  string (``hmac_sha256_hex``); each adapter documents its payload.
* Bitget: Base64 of the raw HMAC-SHA256 digest (``hmac_sha256_b64``) of
  ``timestamp + METHOD + requestPath[?query] + body``, sent with the API
  passphrase.
* Bitget and MEXC sign their query string without URL-encoding it
  (``plain_query``), which is only correct while every name and value is
  URL-safe; anything else is refused before signing.

Hyperliquid (EIP-712) and Lighter (native SignerClient) sign in their own
adapter modules because they need optional dependencies.

Secrets and signatures are never logged.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import urllib.parse
from collections.abc import Sequence

from joseph_execution.domain import errors

# RFC 3986 unreserved characters: the only ones a query carries unchanged
# whether or not it is URL-encoded.
_URL_SAFE = re.compile(r"[A-Za-z0-9._~-]*")


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

    The venue verifies the signature over the query string it receives, so
    the same encoded string must be both signed and sent; reordering or
    re-encoding the parameters would invalidate it.

    Args:
        params: ``(name, value)`` pairs.

    Returns:
        ``a=1&b=2`` with RFC 3986 percent-encoding.
    """
    return urllib.parse.urlencode(list(params), quote_via=urllib.parse.quote)


def plain_query(venue: str, params: Sequence[tuple[str, str]]) -> str:
    """Joins parameters in the given order without URL-encoding them.

    Bitget and MEXC sign the query string unencoded and verify the
    signature over the string they receive, so the same unencoded string
    is signed and sent. That is only correct while no name or value needs
    encoding; otherwise the request is refused before it is signed or sent.

    Args:
        venue: Venue name, for the error message.
        params: ``(name, value)`` pairs, already in signing order.

    Returns:
        ``a=1&b=2``.

    Raises:
        AdapterUnavailableError: ``VALIDATION_FAILED`` when a name or value
            holds a character other than ASCII letters, digits, ``-``,
            ``.``, ``_`` and ``~``.
    """
    for name, value in params:
        if not (_URL_SAFE.fullmatch(name) and _URL_SAFE.fullmatch(value)):
            raise errors.AdapterUnavailableError(
                f"{venue}: query parameter {name!r} holds a character that "
                "needs URL-encoding, which this venue's signature does not "
                "allow; nothing was sent",
                code="VALIDATION_FAILED",
            )
    return "&".join(f"{name}={value}" for name, value in params)
