# ─────────────────────────────────────────────────────────────────────────────
# Agency Analytics Kit — multi-stage image (publishable)
# Builder installs deps via uv; runtime ships venv + src only (non-root).
# The runtime image is self-sufficient: /app/src carries agency_analytics,
# connectors (run_*.py) and dbt_project. Dev compose bind-mounts ../../src
# over /app/src — the mount shadows this baked copy at runtime.
# No HEALTHCHECK here: the real healthcheck lives in services/pipeline
# compose (single source of truth; an image-level one would force a
# Postgres dependency on standalone `docker run`).
# ─────────────────────────────────────────────────────────────────────────────

# OCI build metadata. Defaults keep a plain `docker build` working; CI (see
# .github/workflows/docker-build-scan-sign.yml) overrides them from the
# GitHub context via --build-arg.
ARG SOURCE=https://github.com/Developmi/agency-analytics-kit
ARG REVISION=""
ARG CREATED=""

# ─────────────────────────────────────────────────────────────────────────────
# Stage 1 - builder: install Python dependencies via uv
# ─────────────────────────────────────────────────────────────────────────────
# python:3.12-slim <2026-09-04> — digest-pinned to stop base drift
# (dependabot `docker` updates bump this pin).
FROM python:3.14-slim@sha256:cad9a2c871761c413caa6fdd6441c783451e740a48aaeba60ae62a8b53525ef6 AS builder

# uv 0.12.7 <2026-09-04> — digest-pinned (no :latest, clears hadolint DL3007)
COPY --from=ghcr.io/astral-sh/uv:0.12.7@sha256:95f2aa1fe59274951cfe9b0cbc7972e879ff1004bc8945d130a32eb0dbd85945 /uv /uvx /bin/
WORKDIR /app

# Layer 1: dependencies only (caches on pyproject.toml + uv.lock)
COPY pyproject.toml uv.lock /app/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# Layer 2: copy project metadata and source code
COPY README.md /app/
COPY src/ /app/src/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# ─────────────────────────────────────────────────────────────────────────────
# Stage 2 - runtime: minimal image with Python venv only
# ─────────────────────────────────────────────────────────────────────────────
# python:3.12-slim <2026-09-04> — digest-pinned (matches builder stage).
FROM python:3.14-slim@sha256:cad9a2c871761c413caa6fdd6441c783451e740a48aaeba60ae62a8b53525ef6

# Re-declare ARGs inside the stage to consume the global defaults (Docker
# scoping); --build-arg values override them for CI builds.
ARG SOURCE
ARG REVISION
ARG CREATED

RUN groupadd --gid 1000 app && \
    useradd --uid 1000 --gid app --shell /bin/bash --create-home app

WORKDIR /app

# Copy virtual env and source from builder
COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --from=builder --chown=app:app /app/src /app/src

ENV \
    PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    UV_DISABLE=1

# OCI labels (org.opencontainers.image). Values come from the ARGs above:
# SOURCE = repo URL, REVISION = git sha, CREATED = RFC3339 build timestamp.
LABEL org.opencontainers.image.source="$SOURCE" \
      org.opencontainers.image.revision="$REVISION" \
      org.opencontainers.image.created="$CREATED" \
      org.opencontainers.image.title="Agency Analytics Kit" \
      org.opencontainers.image.description="Multi-tenant marketing data pipeline: extract, transform, and visualize ads + organic social metrics" \
      org.opencontainers.image.vendor="Developmi"

# Numeric UID (DL3066): non-numeric USER may not resolve in all runtimes; the
# app user is created with --uid 1000 above, so 1000 is the exact same identity.
USER 1000

CMD ["tail", "-f", "/dev/null"]
