# syntax=docker/dockerfile:1.7
# Joseph market engine — production image (non-root, no build tools).
ARG PYTHON_VERSION=3.13

FROM python:${PYTHON_VERSION}-slim-bookworm AS build
COPY --from=ghcr.io/astral-sh/uv:0.12.15 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
# git + ssh fetch the private indicator library (apoman123/PineScripts).
# Build with an agent holding its deploy key:  docker build --ssh default .
# The key is only forwarded to RUN --mount=type=ssh, never stored in a layer.
RUN apt-get update \
 && apt-get install -y --no-install-recommends git openssh-client \
 && rm -rf /var/lib/apt/lists/* \
 && install -m 700 -d /root/.ssh \
 && echo 'github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl' > /root/.ssh/known_hosts
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv --mount=type=ssh,required=true \
    uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv --mount=type=ssh,required=true \
    uv sync --frozen --no-dev --no-editable

FROM python:${PYTHON_VERSION}-slim-bookworm
LABEL org.opencontainers.image.title="joseph-market-engine" \
      org.opencontainers.image.description="Joseph market data, screening, indicators, strategies, signals and alerts" \
      org.opencontainers.image.licenses="Proprietary"
RUN useradd --system --uid 10001 --create-home --home-dir /home/joseph joseph \
 && mkdir -p /app/var && chown joseph:joseph /app/var
WORKDIR /app
COPY --from=build --chown=joseph:joseph /app/.venv /app/.venv
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    JOSEPH_DATABASE_URL=sqlite:////app/var/market.db
USER joseph
EXPOSE 8000
VOLUME ["/app/var"]
HEALTHCHECK --interval=15s --timeout=3s --start-period=30s --retries=5 \
  CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status == 200 else 1)"]
CMD ["uvicorn", "joseph.bootstrap:create_application", "--factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
