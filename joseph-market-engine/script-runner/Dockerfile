# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT
# Build from joseph-market-engine: docker build -f script-runner/Dockerfile
# -t joseph-python-runner:1 .
FROM python:3.12-slim-bookworm
WORKDIR /runner
COPY src/joseph/scripts/worker.py /runner/worker.py
COPY src/joseph_script /usr/local/lib/python3.12/site-packages/joseph_script
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 65534:65534
ENTRYPOINT ["python", "-I", "/runner/worker.py"]
