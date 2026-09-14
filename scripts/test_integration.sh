#!/bin/sh
set -eu

DOCKER_BIN=${DOCKER_BIN:-docker}
UV_BIN=${UV_BIN:-uv}
POSTGRES_IMAGE=${POSTGRES_IMAGE:-postgres:18.6-trixie@sha256:4ef4dbc939d61acea57712655ddb4b4ab27419c913f94cca0cd57cb3ea3c2280}
CONTAINER_NAME="sopara-wp2-postgres-$$"

cleanup() {
  "$DOCKER_BIN" rm --force "$CONTAINER_NAME" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

"$DOCKER_BIN" run --detach --rm \
  --name "$CONTAINER_NAME" \
  --env POSTGRES_DB=sopara \
  --env POSTGRES_USER=sopara \
  --env POSTGRES_PASSWORD=sopara-local-only \
  --publish 127.0.0.1::5432 \
  "$POSTGRES_IMAGE" >/dev/null

attempt=0
until "$DOCKER_BIN" exec "$CONTAINER_NAME" pg_isready --username sopara --dbname sopara >/dev/null 2>&1; do
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 60 ]; then
    "$DOCKER_BIN" logs "$CONTAINER_NAME"
    exit 1
  fi
  sleep 1
done

PORT=$("$DOCKER_BIN" port "$CONTAINER_NAME" 5432/tcp | sed 's/.*://')
SOPARA_DATABASE_URL="postgresql+psycopg://sopara:sopara-local-only@127.0.0.1:${PORT}/sopara"
export SOPARA_DATABASE_URL
export SOPARA_TEST_ADMIN_URL="postgresql://sopara:sopara-local-only@127.0.0.1:${PORT}/sopara"

"$UV_BIN" run --locked alembic upgrade head
"$UV_BIN" run --locked pytest tests/integration
