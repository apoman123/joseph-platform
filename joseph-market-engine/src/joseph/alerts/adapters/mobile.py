# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Official WhatsApp Cloud API and encrypted browser/mobile Web Push senders."""

from __future__ import annotations

import asyncio
import base64
import dataclasses
import json
from collections.abc import Mapping

import httpx
import py_vapid
import pywebpush
import requests
from cryptography.hazmat.primitives import serialization

from joseph.alerts.app import ports
from joseph.alerts.domain import model


class WhatsAppSender:  # noqa: DOC302 - Document constructor mutation.
    """Delivers approved one-parameter WhatsApp templates through Meta.

    Attributes:
        channel: WHATSAPP; HTTP acceptance does not imply recipient receipt.
    """

    channel = model.Channel.WHATSAPP

    def __init__(  # noqa: DOC303 - Document None after mutation.
        self,
        client: httpx.AsyncClient,
        token: str,
        phone_id: str,
        template: str,
        *,
        language: str = "en_US",
        version: str = "v27.0",
    ) -> None:
        """Stores server-only credentials for the official Cloud API.

        Args:
            client: Shared asynchronous HTTP client owned by the application.
            token: Secret WhatsApp Business access token.
            phone_id: Numeric sending business phone number identifier.
            template: Approved template with exactly one BODY text parameter.
            language: Approved template locale; defaults to en_US.
            version: Supported Graph API version; defaults to v27.0.

        Returns:
            None after configuring the sender without making network calls.
        """
        self._client = client
        self._token = token
        self._url = f"https://graph.facebook.com/{version}/{phone_id}/messages"
        self._template = template
        self._language = language

    async def send(self, record: model.DeliveryRecord) -> None:
        """Sends an approved template outside chat-session windows.

        Args:
            record: Pending delivery with an international E.164 target.

        Returns:
            None when Meta accepts the message, without claiming it was read.

        Raises:
            model.DeliveryError: On retryable transport/rate/server failure or
                permanent template, credential or recipient rejection.
        """
        text = " ".join(model.format_text(record.payload).split())[:900]
        body = {
            "messaging_product": "whatsapp",
            "to": record.target.lstrip("+"),
            "type": "template",
            "template": {
                "name": self._template,
                "language": {"code": self._language},
                "components": [
                    {
                        "type": "body",
                        "parameters": [{"type": "text", "text": text}],
                    }
                ],
            },
        }
        try:
            response = await self._client.post(
                self._url,
                headers={"Authorization": f"Bearer {self._token}"},
                json=body,
                timeout=10.0,
                follow_redirects=False,
            )
        except httpx.HTTPError:
            raise model.DeliveryError(
                "WhatsApp transport failure", retryable=True
            ) from None
        if response.status_code != 200:
            retry = response.headers.get("retry-after", "")
            raise model.DeliveryError(
                f"WhatsApp returned HTTP {response.status_code}; "
                "check server credentials, template and recipient",
                retryable=response.status_code in (408, 425, 429)
                or response.status_code >= 500,
                retry_after_ms=int(retry) * 1000 if retry.isdigit() else None,
            )
        try:
            data = response.json()
            accepted = (
                isinstance(data, dict)
                and isinstance(data.get("messages"), list)
                and bool(data["messages"])
                and isinstance(data["messages"][0], dict)
                and bool(data["messages"][0].get("id"))
            )
        except (ValueError, AttributeError, TypeError):
            accepted = False
        if not accepted:
            raise model.DeliveryError(
                "WhatsApp did not accept the message", retryable=False
            )


class WebPushSender:  # noqa: DOC302 - Document constructor mutation.
    """Encrypts notifications using VAPID and private device registrations.

    Attributes:
        channel: WEB_PUSH; expired device endpoints are disabled automatically.
    """

    channel = model.Channel.WEB_PUSH

    def __init__(  # noqa: DOC303 - Document None after mutation.
        self,
        repository: ports.SubscriptionRepository,
        private_key: str,
        public_key: str,
        contact: str,
    ) -> None:
        """Validates the VAPID key pair and stores server-only signing material.

        Args:
            repository: Tenant-scoped private push registration storage.
            private_key: Base64url raw P-256 private key, never a file path.
            public_key: Matching base64url uncompressed P-256 public key.
            contact: VAPID contact URI, mailto or HTTPS.

        Returns:
            None after validating configuration without network calls.

        Raises:
            ValueError: If the VAPID keys or contact are invalid or mismatched.
        """
        try:
            key = py_vapid.Vapid02.from_string(private_key)
            raw = key.public_key.public_bytes(
                serialization.Encoding.X962,
                serialization.PublicFormat.UncompressedPoint,
            )
            expected = base64.urlsafe_b64encode(raw).decode().rstrip("=")
            if expected != public_key.rstrip("=") or not contact.startswith(
                ("mailto:", "https://")
            ):
                raise ValueError
        except Exception:  # noqa: BLE001 - SDK boundary; never expose keys.
            raise ValueError(
                "invalid Web Push VAPID key pair or contact"
            ) from None
        self._repository = repository
        self._private_key = private_key
        self._contact = contact

    def _send(
        self,
        registration: model.WebPushRegistration,
        payload: Mapping[str, object],
    ) -> int:
        """Performs one encrypted bounded request on a worker thread.

        Args:
            registration: Validated private endpoint and encryption keys.
            payload: Small public notification content without credentials.

        Returns:
            Push service HTTP status; redirects are never followed.

        Raises:
            pywebpush.WebPushException: On an unsuccessful push response.
            requests.RequestException: On transport or redirect failure.
        """
        with requests.Session() as session:
            session.max_redirects = 0
            session.trust_env = False
            response = pywebpush.webpush(
                subscription_info=registration.to_payload(),
                data=json.dumps(dict(payload), ensure_ascii=False),
                vapid_private_key=self._private_key,
                vapid_claims={"sub": self._contact},
                ttl=300,
                timeout=8,
                requests_session=session,
            )
            return int(response.status_code)

    async def send(self, record: model.DeliveryRecord) -> None:
        """Pushes to an enabled owned device and disables expired registrations.

        Args:
            record: Pending delivery containing a public device identifier.

        Returns:
            None when the push service accepts the encrypted notification.

        Raises:
            model.DeliveryError: On a missing/expired device, bad registration
                or retryable network, rate limit or service failure.
        """
        subscriptions = [
            row
            for row in self._repository.list_for_tenant(record.tenant_id)
            if row.channel is self.channel
            and row.target == record.target
            and row.enabled
            and row.web_push is not None
        ]
        if not subscriptions:
            raise model.DeliveryError(
                "Web Push device is disabled or removed", retryable=False
            )
        registration = subscriptions[0].web_push
        if registration is None:
            raise model.DeliveryError(
                "Web Push registration is missing", retryable=False
            )
        payload: dict[str, object] = {
            "title": "Joseph alert",
            # Bound UTF-8 bytes so multilingual alerts fit push limits.
            "body": model.format_text(record.payload)
            .encode()[:2400]
            .decode(errors="ignore"),
            "tag": record.delivery_id,
            "url": "/",
        }
        try:
            status = await asyncio.to_thread(self._send, registration, payload)
        except pywebpush.WebPushException as exc:
            status = (
                int(exc.response.status_code) if exc.response is not None else 0
            )
        except requests.TooManyRedirects:
            raise model.DeliveryError(
                "Web Push redirects are not permitted", retryable=False
            ) from None
        except requests.RequestException:
            raise model.DeliveryError(
                "Web Push transport failure", retryable=True
            ) from None
        except (ValueError, TypeError):
            raise model.DeliveryError(
                "Web Push registration or signing key is invalid",
                retryable=False,
            ) from None
        if status in (404, 410):
            for row in subscriptions:
                self._repository.upsert(dataclasses.replace(row, enabled=False))
        if not 200 <= status < 300:
            raise model.DeliveryError(
                f"Web Push returned HTTP {status}; renew the device if expired",
                retryable=status == 0
                or status in (408, 425, 429)
                or status >= 500,
            )
