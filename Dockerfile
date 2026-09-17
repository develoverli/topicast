# syntax=docker/dockerfile:1
FROM python:3.12-slim-bookworm AS build

COPY --from=ghcr.io/astral-sh/uv:0.12.15 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependencies first: this layer is cached until the lockfile changes.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project --no-dev

COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
# --no-editable copies the package into the venv; an editable install would point at
# /app/src, which does not exist in the runtime stage.
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-editable


FROM python:3.12-slim-bookworm AS runtime

LABEL org.opencontainers.image.title="topicast" \
      org.opencontainers.image.description="Self-hosted notification hub for Telegram forum topics." \
      org.opencontainers.image.source="https://github.com/develoverli/topicast" \
      org.opencontainers.image.licenses="MIT"

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TOPICAST_DATA_DIR=/data \
    TOPICAST_CONFIG_FILE=/config/config.yaml \
    TOPICAST_PORT=8080

RUN groupadd --gid 10001 topicast \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin topicast \
    && mkdir -p /data /config \
    && chown -R topicast:topicast /data /config

COPY --from=build --chown=topicast:topicast /app/.venv /app/.venv

USER topicast
WORKDIR /app
VOLUME ["/data"]
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os,urllib.request,sys; \
url=f\"http://127.0.0.1:{os.environ.get('TOPICAST_PORT','8080')}/healthz\"; \
sys.exit(0 if urllib.request.urlopen(url, timeout=3).status == 200 else 1)"

ENTRYPOINT ["topicast"]
CMD ["serve"]
