from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Never
from uuid import UUID

from sopara.adapters.database.repositories import CommandReceipt
from sopara.api.app import create_app
from sopara.api.auth import Identity, StaticIdentityValidator
from sopara.api.config import ControlPlaneConfig
from sopara.api.ports import ReadinessRow, ReadPage, StreamWindow

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "contracts" / "openapi.json"


class SchemaOnlyRepository:
    async def ping(self) -> Never:
        raise AssertionError("schema generation must not call adapters")

    async def readiness(self) -> ReadinessRow:
        raise AssertionError("schema generation must not call adapters")

    async def page(self, collection: str, *, after: str | None, limit: int) -> ReadPage:
        raise AssertionError("schema generation must not call adapters")

    async def detail(self, collection: str, resource_id: UUID) -> dict[str, object]:
        raise AssertionError("schema generation must not call adapters")

    async def submit_command(self, **_kwargs: object) -> CommandReceipt:
        raise AssertionError("schema generation must not call adapters")

    async def stream_window(self, *, after: int, limit: int) -> StreamWindow:
        raise AssertionError("schema generation must not call adapters")


def schema_bytes() -> bytes:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    config = ControlPlaneConfig(
        database_url="postgresql+psycopg://schema.invalid/sopara",
        iap_audience="/projects/0/locations/us-central1/services/sopara",
        owner_email="owner@example.invalid",
        public_origin="https://sopara.example.invalid",
        csrf_secret=b"schema-only-csrf-secret-32-bytes-long",
        cursor_secret=b"schema-only-cursor-secret-32-bytes-long",
        static_root=ROOT / "build" / "missing-static",
    )
    app = create_app(
        config,
        identity_validator=StaticIdentityValidator(
            assertion="schema-only", identity=Identity("owner", "hash", now)
        ),
        repository=SchemaOnlyRepository(),
    )
    return (json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n").encode()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    expected = schema_bytes()
    if args.check:
        if not OUTPUT.is_file() or OUTPUT.read_bytes() != expected:
            raise SystemExit("contracts/openapi.json is stale; run scripts/generate_openapi.py")
        print("OpenAPI artifact is current")
        return
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_bytes(expected)
    print(f"wrote {OUTPUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
