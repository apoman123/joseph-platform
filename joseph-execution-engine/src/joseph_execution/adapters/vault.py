# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""AES-256-GCM envelope encryption for venue credentials.

Each secret gets a fresh data key; the data key is wrapped by the master
key (``JOSEPH_VAULT_MASTER_KEY``). In production the master key belongs in
a KMS/HSM; this adapter keeps it in process memory only.
"""

from __future__ import annotations

import base64
import json
import os
from collections.abc import Callable
from collections.abc import Mapping

from cryptography.hazmat.primitives.ciphers import aead

from joseph_execution.domain import errors

_VERSION = "1"


def _b64(data: bytes) -> str:
    """Encodes bytes as URL-safe base64.

    Args:
        data: Bytes.

    Returns:
        Text.
    """
    return base64.urlsafe_b64encode(data).decode("ascii")


def _unb64(text: str) -> bytes:
    """Decodes URL-safe base64.

    Args:
        text: Text.

    Returns:
        Bytes.
    """
    return base64.urlsafe_b64decode(text.encode("ascii"))


class AesGcmVault:
    """Credential vault implementing ``CredentialVault``."""

    def __init__(
        self,
        master_key: bytes,
        key_id: str = "local-1",
        on_secret: Callable[[str], None] | None = None,
    ) -> None:
        """Initializes the vault.

        Args:
            master_key: 32-byte key-encryption key.
            key_id: Identifier of the master key (for rotation).
            on_secret: Callback registering plaintext values for log
                redaction.

        Raises:
            ValueError: If the key is not 32 bytes.
        """
        if len(master_key) != 32:
            raise ValueError("master key must be 32 bytes")
        self._kek = aead.AESGCM(master_key)
        self._key_id = key_id
        self._on_secret = on_secret

    def __repr__(self) -> str:
        """Returns a representation without key material."""
        return f"AesGcmVault(key_id={self._key_id!r}, key=[REDACTED])"

    def seal(self, secret: Mapping[str, str]) -> Mapping[str, str]:
        """Encrypts credential fields.

        Args:
            secret: Plaintext fields.

        Returns:
            Envelope safe to persist.
        """
        if self._on_secret is not None:
            for value in secret.values():
                self._on_secret(value)
        data_key = aead.AESGCM.generate_key(bit_length=256)
        nonce = os.urandom(12)
        wrap_nonce = os.urandom(12)
        plaintext = json.dumps(dict(secret), sort_keys=True).encode("utf-8")
        ciphertext = aead.AESGCM(data_key).encrypt(nonce, plaintext, None)
        wrapped = self._kek.encrypt(wrap_nonce, data_key, self._key_id.encode())
        return {
            "v": _VERSION,
            "alg": "AES-256-GCM",
            "key_id": self._key_id,
            "wrapped_key": _b64(wrapped),
            "wrap_nonce": _b64(wrap_nonce),
            "nonce": _b64(nonce),
            "ciphertext": _b64(ciphertext),
        }

    def open(self, envelope: Mapping[str, str]) -> dict[str, str]:
        """Decrypts an envelope.

        Args:
            envelope: Stored envelope.

        Returns:
            Plaintext fields.

        Raises:
            ExecutionError: ``FORBIDDEN`` when authentication fails.
        """
        try:
            data_key = self._kek.decrypt(
                _unb64(envelope["wrap_nonce"]),
                _unb64(envelope["wrapped_key"]),
                envelope["key_id"].encode(),
            )
            plaintext = aead.AESGCM(data_key).decrypt(
                _unb64(envelope["nonce"]),
                _unb64(envelope["ciphertext"]),
                None,
            )
        except Exception as err:
            raise errors.ExecutionError(
                "FORBIDDEN", "credential envelope failed authentication"
            ) from err
        result = json.loads(plaintext)
        if self._on_secret is not None:
            for value in result.values():
                self._on_secret(str(value))
        return {str(k): str(v) for k, v in result.items()}
