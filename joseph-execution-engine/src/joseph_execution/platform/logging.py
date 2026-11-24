# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Logging with credential redaction."""

from __future__ import annotations

import logging
import re
import threading

# Secret-looking fragments redacted even when the value was never
# registered: bearer tokens, and key=value or "key": "value" pairs whose key
# names a credential.
_PATTERNS = (
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+"),
    re.compile(
        r"(?i)((?:api[_-]?secret|api[_-]?key|secret|password|passphrase|"
        r"private[_-]?key|token|signature)[\"']?\s*[:=]\s*[\"']?)"
        r"[^\s,\"'}]+"
    ),
)


class SecretRegistry:
    """Plaintext secret values that must never appear in logs."""

    def __init__(self) -> None:
        """Initializes an empty registry."""
        self._values: set[str] = set()
        self._lock = threading.Lock()

    def add(self, value: str) -> None:
        """Registers a secret value (short values are ignored).

        Values shorter than 6 characters are not registered: redacting them
        everywhere would garble ordinary log text.

        Args:
            value: Plaintext secret.
        """
        if len(value) >= 6:
            with self._lock:
                self._values.add(value)

    def scrub(self, text: str) -> str:
        """Redacts registered values and secret-like patterns.

        Args:
            text: Log text.

        Returns:
            Redacted text.
        """
        with self._lock:
            values = sorted(self._values, key=len, reverse=True)
        # Longest first, so a secret that contains another one is redacted
        # whole rather than leaving its remainder in the text.
        for value in values:
            text = text.replace(value, "[REDACTED]")
        for pattern in _PATTERNS:
            text = pattern.sub(r"\1[REDACTED]", text)
        return text


# Process-wide on purpose: config and the credential vault register secrets
# as they load them, and the filter installed by install() reads them.
SECRETS = SecretRegistry()


class RedactingFilter(logging.Filter):
    """Rewrites every record's message with secrets removed."""

    def __init__(self, registry: SecretRegistry = SECRETS) -> None:
        """Initializes the filter.

        Args:
            registry: Secret registry.
        """
        super().__init__()
        self._registry = registry

    def filter(self, record: logging.LogRecord) -> bool:
        """Redacts the record in place.

        The message is formatted with its arguments first, then scrubbed
        and stored without arguments, so secrets passed as ``%s`` arguments
        are redacted too.

        Args:
            record: Log record.

        Returns:
            Always True (the record is kept).
        """
        message = record.getMessage()
        record.msg = self._registry.scrub(message)
        record.args = ()
        return True


def install(registry: SecretRegistry = SECRETS) -> RedactingFilter:
    """Installs the redaction filter on the root logger and its handlers.

    Configures basic INFO logging first when the root logger has no
    handlers.

    Args:
        registry: Secret registry.

    Returns:
        The installed filter.
    """
    redactor = RedactingFilter(registry)
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(level=logging.INFO)
    # Logger filters only see records logged on that logger itself; records
    # propagated from child loggers reach the handlers, so filter both.
    for handler in root.handlers:
        handler.addFilter(redactor)
    root.addFilter(redactor)
    return redactor
