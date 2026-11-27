# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Runs Python source only in bounded, network-isolated Docker containers."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import gzip
import json
from collections.abc import Mapping
from typing import Any

import docker
import requests

from joseph.domain import errors


class DockerRunner:
    """Executes tenant source.

    Executes tenant source without host files, broker keys or network access.

    Attributes:
        enabled: Whether the operator explicitly enabled user-code execution.
        image: Operator-owned runner image; user requests cannot override it.
        timeout_s: Wall-clock limit in seconds, bounded to one through thirty.
    """

    def __init__(
        self,
        *,
        enabled: bool = False,
        image: str = "joseph-python-runner:1",
        timeout_s: float = 10,
    ) -> None:
        """Sets execution policy without contacting Docker during app startup.

        Args:
            enabled: Explicit operator opt-in, default disabled.
            image: Prebuilt trusted runner image, default
                joseph-python-runner:1.
            timeout_s: Wall-clock seconds, clamped between one and thirty.
        """
        self.enabled = enabled
        self.image = image
        self.timeout_s = min(30.0, max(1.0, timeout_s))
        self._slots = asyncio.Semaphore(2)

    def _execute(self, bundle: Mapping[str, object]) -> dict[str, Any]:  # noqa: PLR0912 - Every container lifecycle branch must share unconditional cleanup.
        """Runs and removes one restricted container.

        Args:
            bundle: Bounded source, pinned libraries, inputs and closed bars.

        Returns:
            Parsed JSON result, never container objects or host environment.

        Raises:
            errors.UnsupportedError: If Docker or the trusted image is absent.
            errors.ValidationFailedError: If source fails, times out, exceeds
                output bounds or produces malformed/non-finite JSON.
        """
        try:
            serialized = json.dumps(dict(bundle), allow_nan=False).encode()
        except (ValueError, TypeError, RecursionError):
            raise errors.ValidationFailedError(
                "script input is not finite JSON"
            ) from None
        if len(serialized) > 2_000_000:
            raise errors.ValidationFailedError("script input exceeds two MB")
        encoded = base64.b64encode(gzip.compress(serialized)).decode()
        chunks = [
            encoded[index : index + 16000]
            for index in range(0, len(encoded), 16000)
        ]
        environment = {
            f"JOSEPH_SCRIPT_INPUT_{index}": value
            for index, value in enumerate(chunks)
        }
        environment["JOSEPH_SCRIPT_INPUT_COUNT"] = str(len(chunks))
        container = None
        client = None
        try:
            client = docker.from_env(timeout=5)
            # Running user code never pulls an arbitrary image.
            client.images.get(self.image)
            container = client.containers.run(
                self.image,
                detach=True,
                environment=environment,
                network_mode="none",
                read_only=True,
                user="65534:65534",
                cap_drop=["ALL"],
                security_opt=["no-new-privileges:true"],
                mem_limit="256m",
                memswap_limit="256m",
                nano_cpus=1_000_000_000,
                pids_limit=32,
                tmpfs={"/tmp": "rw,noexec,nosuid,size=16m"},  # noqa: S108 - Private memory filesystem inside the isolated container.
                log_config=docker.types.LogConfig(
                    type="json-file",
                    config={"max-size": "16m", "max-file": "1"},
                ),
            )
            try:
                status = container.wait(timeout=self.timeout_s)
            except (requests.Timeout, requests.ConnectionError):
                raise errors.ValidationFailedError(
                    "script exceeded its wall-clock limit or Docker "
                    "interrupted the wait"
                ) from None
            output = container.logs(stdout=True, stderr=False, tail="all")
            if not output or len(output) > 16_000_000:
                raise errors.ValidationFailedError(
                    "script stopped or exceeded its output/memory limit"
                )
            reply = json.loads(output)
            if (
                not isinstance(reply, dict)
                or not reply.get("ok")
                or status.get("StatusCode") != 0
            ):
                message = (
                    str(reply.get("error", "script execution failed"))[:400]
                    if isinstance(reply, dict)
                    else "invalid script output"
                )
                raise errors.ValidationFailedError(message)
            result = reply.get("result")
            if not isinstance(result, dict):
                raise errors.ValidationFailedError(
                    "script returned an invalid result"
                )
            return result
        except requests.Timeout:
            raise errors.ValidationFailedError(
                "script exceeded its wall-clock limit"
            ) from None
        except docker.errors.ImageNotFound:
            raise errors.UnsupportedError(
                "build the trusted Python runner image; see README script setup"
            ) from None
        except docker.errors.DockerException:
            raise errors.UnsupportedError(
                "Python runner is unavailable; check "
                "Docker setup and permissions"
            ) from None
        except requests.RequestException:
            raise errors.UnsupportedError(
                "Docker control connection failed"
            ) from None
        except (ValueError, TypeError):
            raise errors.ValidationFailedError(
                "script produced malformed output"
            ) from None
        finally:
            if container is not None:
                with contextlib.suppress(
                    docker.errors.DockerException, requests.RequestException
                ):
                    container.remove(force=True)
            if client is not None:
                client.close()

    async def run(self, bundle: Mapping[str, object]) -> dict[str, Any]:
        """Runs isolated source off the event loop with bounded concurrency.

        Args:
            bundle: JSON-compatible source and real closed-bar input data.

        Returns:
            User output after deterministic simulation and validation.

        Raises:
            errors.UnsupportedError: If runtime execution is not
                enabled/available.
            errors.ValidationFailedError: On script errors or resource limits.
        """
        if not self.enabled:
            raise errors.UnsupportedError(
                "enable JOSEPH_SCRIPT_RUNTIME=docker and build the "
                "runner image; see README"
            )
        async with self._slots:
            task = asyncio.create_task(asyncio.to_thread(self._execute, bundle))
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                with contextlib.suppress(errors.JosephError):
                    await task
                raise
