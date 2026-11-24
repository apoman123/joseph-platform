# syntax=docker/dockerfile:1.7
# Joseph execution engine — production image (non-root, no build tools).
ARG PYTHON_VERSION=3.13

FROM python:${PYTHON_VERSION}-slim-bookworm AS build
COPY --from=ghcr.io/astral-sh/uv:0.12.15 /uv /bin/uv
# Precompile bytecode for a faster start; copy files out of the uv cache
# mount (another filesystem, so hard links fail); use the image's Python.
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
# Crypto venue signers (extra "venues") are always installed; broker SDKs
# are optional, e.g. UV_EXTRAS="--extra ibkr --extra sinopac".
ARG UV_EXTRAS=""
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
# Dependencies first, without the project, so source changes reuse this
# layer.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --extra venues --no-install-project ${UV_EXTRAS}
COPY src ./src
# --no-editable installs the project into the venv: the runtime stage
# copies only /app/.venv.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --extra venues --no-editable ${UV_EXTRAS}

FROM python:${PYTHON_VERSION}-slim-bookworm
LABEL org.opencontainers.image.title="joseph-execution-engine" \
      org.opencontainers.image.description="Joseph risk gateway, OMS, paper/shadow/live trading, audit and kill switch" \
      org.opencontainers.image.licenses="MIT"
# Fixed non-root UID that owns the /app/var data volume.
RUN useradd --system --uid 10001 --create-home --home-dir /home/joseph joseph \
 && mkdir -p /app/var && chown joseph:joseph /app/var
WORKDIR /app
COPY --from=build --chown=joseph:joseph /app/.venv /app/.venv
# JOSEPH_DATABASE_URL: four slashes make an absolute path on the volume.
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    JOSEPH_DATABASE_URL=sqlite:////app/var/execution.db
USER joseph
EXPOSE 8100
VOLUME ["/app/var"]
# The slim image has no curl, so Python's urllib probes /health.
HEALTHCHECK --interval=15s --timeout=3s --start-period=30s --retries=5 \
  CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8100/health', timeout=2).status == 200 else 1)"]
CMD ["uvicorn", "joseph_execution.bootstrap:create_application", "--factory", "--host", "0.0.0.0", "--port", "8100", "--proxy-headers"]
