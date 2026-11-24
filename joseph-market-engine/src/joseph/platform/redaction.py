# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Logging setup with secret redaction.

A ``logging.Filter`` scrubs bearer tokens, API keys, secrets, signatures and
long hex/base64 blobs from every record before handlers format it, so a
mistaken ``logger.info("%s", request.headers)`` cannot leak credentials.
Secrets read from the environment or secret files (``register_secret``) are
also replaced wherever they appear, whatever their shape.
"""

from __future__ import annotations

import logging
import re

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+"), r"\1***"),
    (
        re.compile(
            r"(?i)((?:api[_-]?key|api[_-]?secret|secret|token|signature|"
            r"password|passphrase|private[_-]?key|authorization)"
            r"[\"']?\s*[:=]\s*[\"']?)[^\s\"',;&]+"
        ),
        r"\1***",
    ),
    (re.compile(r"\b0x[0-9a-fA-F]{40,}\b"), "0x***"),
    (re.compile(r"\b[0-9a-fA-F]{48,}\b"), "***"),
)
_MIN_SECRET_LENGTH = 4
_SECRETS: set[str] = set()


def register_secret(value: str | None) -> None:
    """Registers a configured secret so ``redact`` removes it verbatim.

    Args:
        value: Secret (``None`` and very short values are ignored, so
            redaction cannot mangle ordinary text).
    """
    if value and len(value) >= _MIN_SECRET_LENGTH:
        _SECRETS.add(value)


def redact(text: str) -> str:
    """Removes secrets from a string.

    Args:
        text: Arbitrary text.

    Returns:
        Text with secret values replaced by ``***``.
    """
    for secret in sorted(_SECRETS, key=len, reverse=True):
        text = text.replace(secret, "***")
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class RedactingFilter(logging.Filter):
    """Filter that redacts the fully formatted message of each record."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Redacts the record in place.

        Args:
            record: Log record.

        Returns:
            Always True (the record is kept, only scrubbed).
        """
        message = record.getMessage()
        record.msg = redact(message)
        record.args = None
        if record.exc_text:
            record.exc_text = redact(record.exc_text)
        return True


def configure_logging(level: int = logging.INFO) -> None:
    """Configures root logging with redaction on every handler.

    Args:
        level: Root log level.
    """
    root = logging.getLogger()
    if not root.handlers:
        stream = logging.StreamHandler()
        stream.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        )
        root.addHandler(stream)
    root.setLevel(level)
    for handler in root.handlers:
        if not any(isinstance(f, RedactingFilter) for f in handler.filters):
            handler.addFilter(RedactingFilter())
