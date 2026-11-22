"""Logging with credential redaction."""

from __future__ import annotations

import logging
import re
import threading

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
        for value in values:
            text = text.replace(value, "[REDACTED]")
        for pattern in _PATTERNS:
            text = pattern.sub(r"\1[REDACTED]", text)
        return text


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
    """Installs the redaction filter on the root logger's handlers.

    Args:
        registry: Secret registry.

    Returns:
        The installed filter.
    """
    redactor = RedactingFilter(registry)
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(level=logging.INFO)
    for handler in root.handlers:
        handler.addFilter(redactor)
    root.addFilter(redactor)
    return redactor
