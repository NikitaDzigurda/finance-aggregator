FROM python:3.13-slim-trixie

RUN pip install --no-cache-dir uv==0.11.32

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --locked --no-dev --no-install-project

COPY apps ./apps
COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./
COPY scripts/api-entrypoint.sh ./scripts/api-entrypoint.sh

RUN uv sync --locked --no-dev --no-editable \
    && chmod +x ./scripts/api-entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["./scripts/api-entrypoint.sh"]
