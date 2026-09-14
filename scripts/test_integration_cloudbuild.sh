#!/bin/sh
set -eu

DOCKER_BIN=${DOCKER_BIN:-docker}
POSTGRES_IMAGE=${POSTGRES_IMAGE:-postgres:18.6-trixie@sha256:4ef4dbc939d61acea57712655ddb4b4ab27419c913f94cca0cd57cb3ea3c2280}
UV_IMAGE=${UV_IMAGE:-ghcr.io/astral-sh/uv:0.12.11-python3.14-trixie-slim@sha256:967bc7d17bbc9abd6602db848df285dcf7078334d0fa2b00c940935350cc6f7e}
SUFFIX=${BUILD_ID:-$$}
NETWORK="sopara-integration-${SUFFIX}"
POSTGRES_CONTAINER="sopara-postgres-${SUFFIX}"

cleanup() {
  "$DOCKER_BIN" rm --force "$POSTGRES_CONTAINER" >/dev/null 2>&1 || true
  "$DOCKER_BIN" network rm "$NETWORK" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

"$DOCKER_BIN" network create "$NETWORK" >/dev/null
"$DOCKER_BIN" run --detach --rm \
  --name "$POSTGRES_CONTAINER" \
  --network "$NETWORK" \
  --env POSTGRES_DB=sopara \
  --env POSTGRES_USER=sopara \
  --env POSTGRES_PASSWORD=sopara-local-only \
  "$POSTGRES_IMAGE" >/dev/null

attempt=0
until "$DOCKER_BIN" exec "$POSTGRES_CONTAINER" pg_isready --username sopara --dbname sopara >/dev/null 2>&1; do
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 60 ]; then
    "$DOCKER_BIN" logs "$POSTGRES_CONTAINER"
    exit 1
  fi
  sleep 1
done

DATABASE_URL="postgresql+psycopg://sopara:sopara-local-only@${POSTGRES_CONTAINER}:5432/sopara"
ADMIN_URL="postgresql://sopara:sopara-local-only@${POSTGRES_CONTAINER}:5432/sopara"
"$DOCKER_BIN" run --rm \
  --network "$NETWORK" \
  --volume "$PWD:/workspace" \
  --workdir /workspace \
  --env SOPARA_DATABASE_URL="$DATABASE_URL" \
  --env SOPARA_TEST_ADMIN_URL="$ADMIN_URL" \
  --env UV_PROJECT_ENVIRONMENT=/tmp/sopara-cloudbuild-venv \
  --env UV_CACHE_DIR=/tmp/sopara-cloudbuild-cache \
  "$UV_IMAGE" \
  sh -ceu 'uv sync --locked && uv run --locked alembic upgrade head && uv run --locked pytest tests/integration'
