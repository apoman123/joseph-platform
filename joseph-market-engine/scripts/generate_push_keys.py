# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Prints a new Web Push VAPID key pair for server environment configuration.

Run ``uv run python scripts/generate_push_keys.py`` from the market engine.
The private output is a secret: copy it into a protected environment file.
"""

from __future__ import annotations

import base64
import sys

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


def main() -> int:
    """Generates P-256 keys and writes shell-compatible settings to stdout.

    Returns:
        Zero after printing a secret private scalar and matching public point
        as unpadded base64url; no keys or files are retained by this script.
    """
    private = ec.generate_private_key(ec.SECP256R1())
    scalar = private.private_numbers().private_value.to_bytes(32, "big")
    public = private.public_key().public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )
    for name, value in (("PRIVATE", scalar), ("PUBLIC", public)):
        encoded = base64.urlsafe_b64encode(value).decode().rstrip("=")
        sys.stdout.write(f"JOSEPH_WEB_PUSH_{name}_KEY={encoded}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
