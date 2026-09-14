#!/bin/sh
set -eu

cd "$(dirname "$0")/../web"

if [ "${1:-}" = "--check" ]; then
  candidate="$(mktemp)"
  trap 'rm -f "$candidate"' EXIT
  bun run openapi-typescript ../contracts/openapi.json -o "$candidate"
  cmp -s "$candidate" src/api/schema.d.ts || {
    echo "web/src/api/schema.d.ts is stale; run bun run api:generate" >&2
    exit 1
  }
  echo "generated TypeScript API schema is current"
  exit 0
fi

mkdir -p src/api
bun run openapi-typescript ../contracts/openapi.json -o src/api/schema.d.ts
