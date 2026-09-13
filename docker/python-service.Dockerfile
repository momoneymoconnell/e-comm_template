# =============================================================================
# One Dockerfile, seven services.
#
# Every Python service is built from this file; `PACKAGE` selects which one.
# Keeping a single recipe means a security patch to the base image, or a change
# to the non-root user, happens once instead of seven times and cannot drift.
#
# Build context is the REPOSITORY ROOT, not the service directory, because the
# uv workspace lock file and the shared `ecom-shared` package live there.
# =============================================================================

# --- Stage 1: build the virtual environment ---------------------------------
FROM python:3.12-slim-bookworm AS builder

# Copy the uv binary from its official image rather than pip-installing it:
# it is a static binary, so this is faster and pins an exact uv version.
COPY --from=ghcr.io/astral-sh/uv:0.11.7 /uv /uvx /bin/

# UV_COMPILE_BYTECODE  : pre-compile .pyc at build time so the first request
#                        after a container start is not slowed by compilation.
# UV_LINK_MODE=copy    : the cache and the venv are on different layers, where
#                        hardlinks are impossible; copying avoids a warning and
#                        a silent fallback.
# UV_PYTHON_DOWNLOADS  : use the interpreter already in the image.
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Which workspace member to build, e.g. "ecom-auth".
ARG PACKAGE

# --- Layer 1: dependencies only ---------------------------------------------
# Copy just the manifests and the lock file first. Docker caches this layer, so
# editing application source does NOT re-resolve or re-download dependencies —
# the difference between a 2-second rebuild and a 90-second one.
# `--no-install-workspace` installs the third-party dependency graph while
# skipping our own source, which has not been copied yet.
COPY pyproject.toml uv.lock ./
COPY packages/ecom-shared/pyproject.toml packages/ecom-shared/
COPY services/gateway/pyproject.toml services/gateway/
COPY services/auth/pyproject.toml services/auth/
COPY services/catalog/pyproject.toml services/catalog/
COPY services/orders/pyproject.toml services/orders/
COPY services/payments/pyproject.toml services/payments/
COPY services/analytics/pyproject.toml services/analytics/
COPY services/notifications/pyproject.toml services/notifications/

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-workspace --package "${PACKAGE}"

# --- Layer 2: our own code --------------------------------------------------
COPY packages/ packages/
COPY services/ services/

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package "${PACKAGE}"

# --- Stage 2: runtime -------------------------------------------------------
# A fresh slim image. uv, build caches, compilers and every other service's
# source stay behind in the builder, so the shipped image is small and has a
# much smaller attack surface.
FROM python:3.12-slim-bookworm AS runtime

# curl is needed by the compose healthcheck. Installed without recommends and
# with the apt lists deleted in the same layer, so nothing extra is retained.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Run as an unprivileged user. If an attacker achieves code execution, they
# land as `app` with no write access to the code they are running — container
# escapes and privilege escalations both get materially harder.
RUN groupadd --system --gid 1001 app \
    && useradd --system --uid 1001 --gid app --create-home --shell /usr/sbin/nologin app

WORKDIR /app

ARG PACKAGE
ARG SERVICE_DIR

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONFAULTHANDLER=1 \
    SERVICE_DIR="${SERVICE_DIR}"

# --chown avoids a second layer that duplicates every file just to change
# ownership, which would roughly double the image size.
COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --from=builder --chown=app:app /app/packages /app/packages
COPY --from=builder --chown=app:app /app/services /app/services
COPY --chown=app:app docker/entrypoint.sh /app/entrypoint.sh

RUN chmod +x /app/entrypoint.sh

# Create the data directory in the image, owned by the runtime user.
#
# Docker seeds a new named volume from whatever is at the mount point in the
# image, ownership included. Without this the volume is created root-owned, and
# a service running as `app` cannot write its first upload - which surfaces as a
# permission error on a code path that works perfectly outside Docker.
RUN mkdir -p /data/media && chown -R app:app /data

USER app

EXPOSE 8000

ENTRYPOINT ["/app/entrypoint.sh"]
