# syntax=docker/dockerfile:1.7
# Joseph market engine — production image (non-root, no build tools).
ARG PYTHON_VERSION=3.13

FROM python:${PYTHON_VERSION}-slim-bookworm AS build
COPY --from=ghcr.io/astral-sh/uv:0.12.15 /uv /bin/uv
# Precompile bytecode for a faster start; copy files out of the uv cache
# mount (another filesystem, so hard links fail); use the image's Python.
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
# git fetches the private indicator library (apoman123/PineScripts) with one
# of two build-time credentials; neither ends up in a layer or a remote URL:
#   docker build --secret id=pinescripts_token,env=PINESCRIPTS_TOKEN .  (CI)
#   docker build --ssh default .  (local agent with access to PineScripts)
# known_hosts pins GitHub's published ed25519 host key for the ssh route.
RUN apt-get update \
 && apt-get install -y --no-install-recommends git openssh-client \
 && rm -rf /var/lib/apt/lists/* \
 && install -m 700 -d /root/.ssh \
 && echo 'github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl' > /root/.ssh/known_hosts
# Optional extras, e.g. UV_EXTRAS="--extra sinopac" for SinoPac Shioaji
# market data (JOSEPH_TW_DATA_SOURCE=sinopac); none by default.
ARG UV_EXTRAS=""
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
# Dependencies first, without the project, so source changes reuse this
# layer. The URL rewrite lives in GIT_CONFIG_* variables of this RUN only,
# so the token never reaches a git config file or a layer.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=secret,id=pinescripts_token,required=false \
    --mount=type=ssh,required=false \
    set -eu; \
    repo=https://github.com/apoman123/PineScripts; \
    token_file=/run/secrets/pinescripts_token; \
    if [ -s "${token_file}" ]; then \
      export GIT_CONFIG_COUNT=1 GIT_CONFIG_VALUE_0="${repo}" \
        GIT_CONFIG_KEY_0="url.https://x-access-token:$(cat "${token_file}")@github.com/apoman123/PineScripts.insteadOf"; \
    elif [ -S "${SSH_AUTH_SOCK:-}" ]; then \
      export GIT_CONFIG_COUNT=1 GIT_CONFIG_VALUE_0="${repo}" \
        GIT_CONFIG_KEY_0="url.ssh://git@github.com/apoman123/PineScripts.insteadOf"; \
    else \
      echo "error: no access to the private ${repo}. Build with --secret id=pinescripts_token,env=PINESCRIPTS_TOKEN or --ssh default (docs/CI_CD.md)." >&2; \
      exit 1; \
    fi; \
    uv sync --frozen --no-dev --no-install-project ${UV_EXTRAS}
COPY src ./src
# --no-editable installs the project into the venv: the runtime stage
# copies only /app/.venv.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable ${UV_EXTRAS}

FROM python:${PYTHON_VERSION}-slim-bookworm
LABEL org.opencontainers.image.title="joseph-market-engine" \
      org.opencontainers.image.description="Joseph market data, screening, indicators, strategies, signals and alerts" \
      org.opencontainers.image.licenses="MIT"
# Fixed non-root UID that owns the /app/var data volume.
RUN useradd --system --uid 10001 --create-home --home-dir /home/joseph joseph \
 && mkdir -p /app/var && chown joseph:joseph /app/var
WORKDIR /app
COPY --from=build --chown=joseph:joseph /app/.venv /app/.venv
# JOSEPH_DATABASE_URL: four slashes make an absolute path on the volume.
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    JOSEPH_DATABASE_URL=sqlite:////app/var/market.db
USER joseph
EXPOSE 8000
VOLUME ["/app/var"]
# The slim image has no curl, so Python's urllib probes /health.
HEALTHCHECK --interval=15s --timeout=3s --start-period=30s --retries=5 \
  CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status == 200 else 1)"]
CMD ["uvicorn", "joseph.bootstrap:create_application", "--factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
