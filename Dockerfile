# syntax=docker/dockerfile:1.18@sha256:dabfc0969b935b2080555ace70ee69a5261af8a8f1b4df97b9e7fbcf6722eddf

FROM oven/bun:1.4.2-alpine@sha256:d888c0ae6c86d7866ff10c5aafdd9077b36aee6455b33dd270fb93c0dd5cef6f AS web-build
WORKDIR /web
COPY web/package.json web/bun.lock ./
RUN bun install --frozen-lockfile
COPY web/ ./
COPY contracts/openapi.json /contracts/openapi.json
COPY scripts/generate_api_types.sh /scripts/generate_api_types.sh
RUN bun run check && bun run build

FROM ghcr.io/astral-sh/uv:0.12.11@sha256:79c6f4776b851471cc73b7d21d0cc834bb94383c292e83640d27eff512864df7 AS uv-bin

FROM python:3.14.7-slim@sha256:cad9a2c871761c413caa6fdd6441c783451e740a48aaeba60ae62a8b53525ef6 AS python-build
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/sopara
WORKDIR /build
COPY --from=uv-bin /uv /uvx /usr/local/bin/
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src/ ./src/
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.14.7-slim@sha256:cad9a2c871761c413caa6fdd6441c783451e740a48aaeba60ae62a8b53525ef6 AS runtime
ENV PATH=/opt/sopara/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY --from=python-build /opt/sopara /opt/sopara
COPY --from=web-build /web/dist/client /app/static
USER 65532:65532
EXPOSE 8080
CMD ["uvicorn", "sopara.api.main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--forwarded-allow-ips", "*"]
