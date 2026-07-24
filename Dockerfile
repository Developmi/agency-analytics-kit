# ─────────────────────────────────────────────────────────────────────────────
# Stage 1 - builder: install Python dependencies via uv
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
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
FROM python:3.12-slim

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

USER app

CMD ["tail", "-f", "/dev/null"]
