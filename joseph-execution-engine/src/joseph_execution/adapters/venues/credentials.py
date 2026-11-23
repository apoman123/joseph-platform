"""Venue credentials for live adapters, loaded from the credential vault.

Accounts store credentials as an AES-GCM envelope (``adapters/vault.py``)
referenced by ``Account.credential_id``; the plaintext fields are whatever
the user submitted to ``POST /v1/accounts/connect/start``. Live adapters
read them per call through a ``CredentialSource`` and never persist, log
or echo them: ``VenueCredentials`` redacts every value in ``repr``.

Field names per venue (all values are strings):

* ``binance`` / ``mexc``: ``api_key``, ``api_secret``.
* ``bitget``: ``api_key``, ``api_secret``, ``passphrase``.
* ``hyperliquid``: ``agent_private_key`` (an API wallet approved by the
  account) and ``account_address`` (the master account it trades for).
* ``lighter``: ``api_private_key``, ``account_index``, ``api_key_index``.

Every venue also accepts ``environment``: ``mainnet`` (default) or
``testnet`` (aliases ``demo``, ``sandbox``) to use the venue's test
environment for that account.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from typing import Protocol

from joseph_execution.domain import errors
from joseph_execution.ports import repositories
from joseph_execution.ports import services

ENVIRONMENT_FIELD = "environment"
_SANDBOX_NAMES = ("testnet", "demo", "sandbox", "paper")
_MAINNET_NAMES = ("mainnet", "live", "production", "")


@dataclasses.dataclass(frozen=True, slots=True)
class VenueCredentials:
    """Decrypted credential fields of one account (never logged).

    Attributes:
        fields: Plaintext fields.
        sandbox: Requested environment: True for testnet, False for
            mainnet, ``None`` when the account does not say.
    """

    fields: Mapping[str, str] = dataclasses.field(repr=False)
    sandbox: bool | None = None

    def __repr__(self) -> str:
        """Returns a representation listing field names only."""
        return (
            f"VenueCredentials(fields={sorted(self.fields)!r}, "
            f"sandbox={self.sandbox!r}, values=[REDACTED])"
        )

    def get(self, name: str) -> str:
        """Returns a stripped field value or an empty string.

        Args:
            name: Field name.

        Returns:
            Value.
        """
        return self.fields.get(name, "").strip()

    def missing(self, required: tuple[str, ...]) -> list[str]:
        """Lists required fields that are absent or empty.

        Args:
            required: Field names.

        Returns:
            Missing names.
        """
        return [name for name in required if not self.get(name)]

    def fingerprint(self) -> int:
        """Returns a process-local hash used to rebuild cached sessions.

        Returns:
            Hash of the field values (not reversible, never persisted).
        """
        return hash(tuple(sorted(self.fields.items())))


def from_fields(fields: Mapping[str, str]) -> VenueCredentials:
    """Builds credentials, interpreting the ``environment`` field.

    Args:
        fields: Plaintext fields.

    Returns:
        Credentials.

    Raises:
        ExecutionError: ``VALIDATION_FAILED`` for an unknown environment.
    """
    raw = fields.get(ENVIRONMENT_FIELD)
    sandbox: bool | None = None
    if raw is not None:
        name = raw.strip().lower()
        if name in _SANDBOX_NAMES:
            sandbox = True
        elif name in _MAINNET_NAMES:
            sandbox = False
        else:
            raise errors.ExecutionError(
                "VALIDATION_FAILED",
                "environment must be mainnet or testnet",
            )
    values = {k: v for k, v in fields.items() if k != ENVIRONMENT_FIELD}
    return VenueCredentials(values, sandbox)


class CredentialSource(Protocol):
    """Loads the credentials of an account (``None`` when it has none)."""

    async def load(self, account_id: str) -> VenueCredentials | None:
        """Loads credentials.

        Args:
            account_id: Account.

        Returns:
            Credentials or ``None``.
        """
        ...


class NoCredentials:
    """A source without credentials: every adapter stays fail-closed."""

    async def load(self, account_id: str) -> VenueCredentials | None:
        """Returns ``None``.

        Args:
            account_id: Account.

        Returns:
            Always ``None``.
        """
        del account_id
        return None


class StaticCredentials:
    """Fixed credentials per account id (tests and sandbox runs)."""

    def __init__(self, by_account: Mapping[str, Mapping[str, str]]) -> None:
        """Initializes the source.

        Args:
            by_account: Plaintext fields keyed by account id.
        """
        self._by_account = {k: dict(v) for k, v in by_account.items()}

    def __repr__(self) -> str:
        """Returns a representation without values."""
        return f"StaticCredentials(accounts={sorted(self._by_account)!r})"

    async def load(self, account_id: str) -> VenueCredentials | None:
        """Returns the account's credentials.

        Args:
            account_id: Account.

        Returns:
            Credentials or ``None``.
        """
        fields = self._by_account.get(account_id)
        return None if fields is None else from_fields(fields)


class VaultCredentialSource:
    """Opens the account's sealed envelope on every call (no caching).

    Not caching means a revoked credential stops working on the next call.
    """

    def __init__(
        self,
        uow_factory: repositories.UnitOfWorkFactory,
        vault: services.CredentialVault,
    ) -> None:
        """Initializes the source.

        Args:
            uow_factory: Unit of work factory.
            vault: Credential vault.
        """
        self._uow = uow_factory
        self._vault = vault

    def __repr__(self) -> str:
        """Returns a representation without key material."""
        return "VaultCredentialSource(vault=[REDACTED])"

    async def load(self, account_id: str) -> VenueCredentials | None:
        """Loads and decrypts the account's credential envelope.

        Args:
            account_id: Account.

        Returns:
            Credentials, or ``None`` for unknown, paper or revoked accounts.
        """
        with self._uow() as uow:
            account = uow.accounts.get(account_id)
            if account is None or account.credential_id is None:
                return None
            envelope = uow.credentials.get(account.credential_id)
        if envelope is None:
            return None
        return from_fields(self._vault.open(envelope))
