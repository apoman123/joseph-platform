# syntax=docker/dockerfile:1.7
# Joseph market engine — production image (non-root, no build tools).
ARG PYTHON_VERSION=3.13

FROM python:${PYTHON_VERSION}-slim-bookworm AS build
COPY --from=ghcr.io/astral-sh/uv:0.12.15 /uv /bin/uv
# Precompile bytecode for a faster start; copy files out of the uv cache
# mount (another filesystem, so hard links fail); use the image's Python.
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
# git fetches git-URL plugin requirements (PLUGIN_REQUIREMENTS, below);
# known_hosts pins GitHub's published ed25519 host key for ssh:// URLs.
RUN apt-get update \
 && apt-get install -y --no-install-recommends git openssh-client \
 && rm -rf /var/lib/apt/lists/* \
 && install -m 700 -d /root/.ssh \
 && echo 'github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl' > /root/.ssh/known_hosts
# Optional market-data extras; the free-first build uses none.
ARG UV_EXTRAS=""
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE NOTICE COPYING-MIT ./
# Dependencies first, without the project, so source changes reuse this
# layer.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project ${UV_EXTRAS}
COPY src ./src
# --no-editable installs the project into the venv: the runtime stage
# copies only /app/.venv.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable ${UV_EXTRAS}
# Optional script plugin packages (joseph.scripts entry points): pip requirements
# (pinned git URLs, wheels or index names) installed without their
# dependencies, so list every package a plugin needs; the catalog check
# then fails the build if any plugin does not load. Empty (the default)
# builds the image with the built-in scripts only. Credentials for a
# private git host never reach a layer or a git config file:
#   --secret id=plugin_git_token,env=PLUGIN_GIT_TOKEN  rewrites
#     https://github.com/ URLs to use the token, in this RUN only;
#   --ssh default  forwards an ssh-agent for git+ssh:// URLs.
ARG PLUGIN_REQUIREMENTS=""
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=secret,id=plugin_git_token,required=false \
    --mount=type=ssh,required=false \
    set -eu; \
    if [ -n "${PLUGIN_REQUIREMENTS}" ]; then \
      token_file=/run/secrets/plugin_git_token; \
      if [ -s "${token_file}" ]; then \
        export GIT_CONFIG_COUNT=1 GIT_CONFIG_VALUE_0="https://github.com/" \
          GIT_CONFIG_KEY_0="url.https://x-access-token:$(cat "${token_file}")@github.com/.insteadOf"; \
      fi; \
      uv pip install --python /app/.venv/bin/python --no-deps ${PLUGIN_REQUIREMENTS}; \
      /app/.venv/bin/python -m joseph.scripts.catalog --check; \
    fi

FROM python:${PYTHON_VERSION}-slim-bookworm
# SPDX licence expression of the image; set it when plugin packages add
# other licences.
ARG IMAGE_LICENSES=AGPL-3.0-only
LABEL org.opencontainers.image.title="joseph-market-engine" \
      org.opencontainers.image.description="Joseph market data, screening, indicators, strategies, signals and alerts" \
      org.opencontainers.image.licenses="${IMAGE_LICENSES}"
# Fixed non-root UID that owns the /app/var data volume.
RUN useradd --system --uid 10001 --create-home --home-dir /home/joseph joseph \
 && mkdir -p /app/var && chown joseph:joseph /app/var
WORKDIR /app
COPY --from=build --chown=joseph:joseph /app/.venv /app/.venv
# JOSEPH_DATABASE_URL: four slashes make an absolute path on the volume.
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    JOSEPH_DATABASE_URL=sqlite:////app/var/market.db
COPY --from=build --chown=joseph:joseph /app/LICENSE /app/NOTICE /app/COPYING-MIT ./
USER joseph
EXPOSE 8000
VOLUME ["/app/var"]
# The slim image has no curl, so Python's urllib probes /health.
HEALTHCHECK --interval=15s --timeout=3s --start-period=30s --retries=5 \
  CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status == 200 else 1)"]
CMD ["uvicorn", "joseph.bootstrap:create_application", "--factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
