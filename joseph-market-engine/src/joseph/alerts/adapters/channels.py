"""Notification channel adapters.

Every sender raises ``DeliveryError`` with a redacted message: bot tokens,
access tokens, SMTP passwords, webhook secrets and service tokens never
appear in errors or logs.
"""

from __future__ import annotations

import asyncio
import email.message
import hashlib
import hmac
import json
import smtplib
import time
import urllib.parse
import uuid
from collections.abc import Awaitable
from collections.abc import Callable
from collections.abc import Mapping

import httpx

from joseph.alerts.domain import model

_TIMEOUT = httpx.Timeout(10.0)
_RETRYABLE_STATUS = frozenset({408, 425, 429})


def _status_error(
    channel: str, response: httpx.Response, secrets: tuple[str, ...]
) -> model.DeliveryError:
    """Maps a non-success HTTP response to a delivery error.

    Args:
        channel: Channel name for the message.
        response: HTTP response.
        secrets: Values to redact.

    Returns:
        Error; 5xx, 408, 425 and 429 are retryable.
    """
    retry_after: int | None = None
    header = response.headers.get("retry-after")
    if header and header.isdigit():
        retry_after = int(header) * 1000
    retryable = response.status_code >= 500 or (
        response.status_code in _RETRYABLE_STATUS
    )
    body = model.redact(response.text[:200], secrets)
    return model.DeliveryError(
        f"{channel} returned HTTP {response.status_code}: {body}",
        retryable=retryable,
        retry_after_ms=retry_after,
    )


def _transport_error(
    channel: str, err: Exception, secrets: tuple[str, ...]
) -> model.DeliveryError:
    """Maps a transport failure to a retryable delivery error.

    Args:
        channel: Channel name.
        err: Original exception.
        secrets: Values to redact.

    Returns:
        Retryable error with a redacted message.
    """
    text = model.redact(f"{type(err).__name__}: {err}", secrets)
    return model.DeliveryError(
        f"{channel} transport error: {text}", retryable=True
    )


class TelegramSender:
    """Telegram Bot API ``sendMessage`` sender.

    Attributes:
        channel: Always TELEGRAM.
    """

    channel = model.Channel.TELEGRAM

    def __init__(
        self,
        client: httpx.AsyncClient,
        bot_token: str,
        *,
        base_url: str = "https://api.telegram.org",
    ) -> None:
        """Initializes the sender.

        Args:
            client: Shared HTTP client.
            bot_token: Bot token (secret).
            base_url: API base URL.
        """
        self._client = client
        self._token = bot_token
        self._base = base_url.rstrip("/")

    def __repr__(self) -> str:
        """Returns a representation without the token."""
        return "TelegramSender(token=***)"

    async def send(self, record: model.DeliveryRecord) -> None:
        """Sends the formatted signal to a chat.

        Args:
            record: Delivery whose target is the chat id.

        Raises:
            model.DeliveryError: On failure.
        """
        secrets = (self._token,)
        url = f"{self._base}/bot{self._token}/sendMessage"
        body = {
            "chat_id": record.target,
            "text": model.format_text(record.payload),
            "disable_web_page_preview": True,
        }
        try:
            response = await self._client.post(url, json=body, timeout=_TIMEOUT)
        except httpx.HTTPError as err:
            raise _transport_error("telegram", err, secrets) from None
        if response.status_code != 200:
            raise _status_error("telegram", response, secrets)
        data = response.json()
        if not isinstance(data, dict) or not data.get("ok"):
            raise model.DeliveryError(
                "telegram rejected the message", retryable=False
            )


class LineSender:
    """LINE Messaging API push sender.

    LINE Notify was discontinued on 2025-03-31, so this uses the Messaging
    API with an ``X-Line-Retry-Key`` derived from the idempotency key; a
    409 for that key means the message was already accepted.

    Attributes:
        channel: Always LINE.
    """

    channel = model.Channel.LINE

    def __init__(
        self,
        client: httpx.AsyncClient,
        channel_access_token: str,
        *,
        base_url: str = "https://api.line.me",
    ) -> None:
        """Initializes the sender.

        Args:
            client: Shared HTTP client.
            channel_access_token: Channel access token (secret).
            base_url: API base URL.
        """
        self._client = client
        self._token = channel_access_token
        self._base = base_url.rstrip("/")

    def __repr__(self) -> str:
        """Returns a representation without the token."""
        return "LineSender(token=***)"

    async def send(self, record: model.DeliveryRecord) -> None:
        """Pushes the formatted signal to a LINE user or group.

        Args:
            record: Delivery whose target is the LINE user id.

        Raises:
            model.DeliveryError: On failure.
        """
        secrets = (self._token,)
        retry_key = str(
            uuid.uuid5(uuid.NAMESPACE_URL, f"joseph:{record.idempotency_key}")
        )
        try:
            response = await self._client.post(
                f"{self._base}/v2/bot/message/push",
                json={
                    "to": record.target,
                    "messages": [
                        {
                            "type": "text",
                            "text": model.format_text(record.payload)[:5000],
                        }
                    ],
                },
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "X-Line-Retry-Key": retry_key,
                },
                timeout=_TIMEOUT,
            )
        except httpx.HTTPError as err:
            raise _transport_error("line", err, secrets) from None
        if response.status_code in (200, 409):
            return
        raise _status_error("line", response, secrets)


class WebhookSender:
    """Generic HTTPS webhook with an HMAC-SHA256 signature.

    Headers: ``X-Joseph-Timestamp`` (UTC ms), ``X-Joseph-Signature``
    (``sha256=`` hex HMAC of ``"{timestamp}.{body}"``), ``X-Joseph-Delivery``
    and ``Idempotency-Key``. Only ``https`` targets are accepted unless
    ``allow_insecure_localhost`` is set for local development (limits SSRF
    to plain-text internal endpoints).

    Attributes:
        channel: Always WEBHOOK.
    """

    channel = model.Channel.WEBHOOK

    def __init__(
        self,
        client: httpx.AsyncClient,
        secret: str,
        *,
        allow_insecure_localhost: bool = False,
        now_ms: Callable[[], int] | None = None,
    ) -> None:
        """Initializes the sender.

        Args:
            client: Shared HTTP client.
            secret: HMAC secret (secret).
            allow_insecure_localhost: Permit ``http://localhost`` targets.
            now_ms: Clock for the timestamp header.
        """
        self._client = client
        self._secret = secret
        self._allow_local = allow_insecure_localhost
        self._now_ms = now_ms or (lambda: int(time.time() * 1000))

    def __repr__(self) -> str:
        """Returns a representation without the secret."""
        return "WebhookSender(secret=***)"

    def _check_target(self, target: str) -> None:
        """Validates the webhook URL.

        Args:
            target: URL.

        Raises:
            model.DeliveryError: For disallowed URLs (not retryable).
        """
        parsed = urllib.parse.urlparse(target)
        if parsed.scheme == "https" and parsed.hostname:
            return
        local = parsed.hostname in ("localhost", "127.0.0.1")
        if self._allow_local and parsed.scheme == "http" and local:
            return
        raise model.DeliveryError(
            "webhook target must be an https URL", retryable=False
        )

    def sign(self, timestamp_ms: int, body: bytes) -> str:
        """Computes the signature header value.

        Args:
            timestamp_ms: Timestamp sent in ``X-Joseph-Timestamp``.
            body: Exact request body.

        Returns:
            ``sha256=<hex>``.
        """
        message = f"{timestamp_ms}.".encode() + body
        digest = hmac.new(
            self._secret.encode("utf-8"), message, hashlib.sha256
        ).hexdigest()
        return f"sha256={digest}"

    async def send(self, record: model.DeliveryRecord) -> None:
        """Posts the signal JSON to the webhook URL.

        Args:
            record: Delivery whose target is the URL.

        Raises:
            model.DeliveryError: On failure.
        """
        self._check_target(record.target)
        secrets = (self._secret,)
        body = json.dumps(
            {"delivery_id": record.delivery_id, "signal": record.payload},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        timestamp = self._now_ms()
        try:
            response = await self._client.post(
                record.target,
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-Joseph-Timestamp": str(timestamp),
                    "X-Joseph-Signature": self.sign(timestamp, body),
                    "X-Joseph-Delivery": record.delivery_id,
                    "Idempotency-Key": record.idempotency_key,
                },
                timeout=_TIMEOUT,
            )
        except httpx.HTTPError as err:
            raise _transport_error("webhook", err, secrets) from None
        if 200 <= response.status_code < 300:
            return
        raise _status_error("webhook", response, secrets)


class ExecutionForwarder:
    """Forwards entry signals to the execution engine's intake endpoint.

    The execution engine decides whether a deployment exists and runs the
    full risk gateway; this forwarder never creates orders itself.

    Attributes:
        channel: Always EXECUTION.
    """

    channel = model.Channel.EXECUTION

    def __init__(
        self, client: httpx.AsyncClient, base_url: str, service_token: str
    ) -> None:
        """Initializes the forwarder.

        Args:
            client: Shared HTTP client.
            base_url: Execution engine base URL (``JOSEPH_EXECUTION_URL``).
            service_token: Service-to-service token (secret).
        """
        self._client = client
        self._base = base_url.rstrip("/")
        self._token = service_token

    def __repr__(self) -> str:
        """Returns a representation without the token."""
        return f"ExecutionForwarder(base_url={self._base!r}, token=***)"

    async def send(self, record: model.DeliveryRecord) -> None:
        """Posts the contract signal.

        Args:
            record: Delivery whose payload is the signal.

        Raises:
            model.DeliveryError: On failure.
        """
        secrets = (self._token,)
        signal_id = str(record.payload.get("signal_id", ""))
        try:
            response = await self._client.post(
                f"{self._base}/internal/v1/signals",
                json=dict(record.payload),
                headers={
                    "X-Joseph-Service-Token": self._token,
                    "Idempotency-Key": signal_id,
                },
                timeout=_TIMEOUT,
            )
        except httpx.HTTPError as err:
            raise _transport_error("execution", err, secrets) from None
        if response.status_code in (200, 201, 202, 409):
            return
        raise _status_error("execution", response, secrets)


SmtpFactory = Callable[[], smtplib.SMTP]
"""Creates a connected SMTP client."""


class EmailSender:
    """SMTP e-mail sender (blocking client run in a worker thread).

    Attributes:
        channel: Always EMAIL.
    """

    channel = model.Channel.EMAIL

    def __init__(
        self,
        smtp_factory: SmtpFactory,
        from_address: str,
        *,
        username: str | None = None,
        password: str | None = None,
        starttls: bool = True,
    ) -> None:
        """Initializes the sender.

        Args:
            smtp_factory: Returns a connected ``smtplib.SMTP``.
            from_address: Sender address.
            username: SMTP user.
            password: SMTP password (secret).
            starttls: Upgrade the connection with STARTTLS.
        """
        self._factory = smtp_factory
        self._from = from_address
        self._username = username
        self._password = password
        self._starttls = starttls

    def __repr__(self) -> str:
        """Returns a representation without the password."""
        return f"EmailSender(from={self._from!r}, password=***)"

    def _send_blocking(self, record: model.DeliveryRecord) -> None:
        """Sends synchronously.

        Args:
            record: Delivery whose target is the e-mail address.
        """
        message = email.message.EmailMessage()
        payload = record.payload
        message["Subject"] = (
            f"Joseph {payload.get('kind')} {payload.get('direction')} "
            f"{payload.get('instrument_id')}"
        )
        message["From"] = self._from
        message["To"] = record.target
        message["Message-ID"] = f"<{record.delivery_id}@joseph.local>"
        message.set_content(model.format_text(payload))
        with self._factory() as smtp:
            if self._starttls:
                smtp.starttls()
            if self._username and self._password:
                smtp.login(self._username, self._password)
            smtp.send_message(message)

    async def send(self, record: model.DeliveryRecord) -> None:
        """Sends the formatted signal by e-mail.

        Args:
            record: Delivery whose target is the e-mail address.

        Raises:
            model.DeliveryError: On failure.
        """
        secrets = tuple(s for s in (self._password,) if s)
        try:
            await asyncio.to_thread(self._send_blocking, record)
        except smtplib.SMTPRecipientsRefused as err:
            raise model.DeliveryError(
                model.redact(f"recipient refused: {err}", secrets),
                retryable=False,
            ) from None
        except (smtplib.SMTPException, OSError) as err:
            raise _transport_error("email", err, secrets) from None


UiSink = Callable[[str, Mapping[str, object]], Awaitable[None]]
"""``(tenant_id, signal_payload)`` coroutine publishing to the UI stream."""


class UiSender:
    """Pushes signals to the authenticated user stream.

    Attributes:
        channel: Always UI.
    """

    channel = model.Channel.UI

    def __init__(self, sink: UiSink) -> None:
        """Initializes the sender.

        Args:
            sink: Coroutine publishing to the tenant's stream.
        """
        self._sink = sink

    async def send(self, record: model.DeliveryRecord) -> None:
        """Publishes the signal to the tenant's UI stream.

        Args:
            record: Delivery.

        Raises:
            model.DeliveryError: If the sink fails.
        """
        try:
            await self._sink(record.tenant_id, record.payload)
        except Exception as err:
            raise model.DeliveryError(
                f"ui sink failed: {type(err).__name__}", retryable=True
            ) from None
