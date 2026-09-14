from __future__ import annotations

from pathlib import Path

from scripts.check_frontend_architecture import (
    FORBIDDEN_PACKAGES,
    FORBIDDEN_SOURCE_TOKENS,
)
from scripts.check_licenses import violations

from tests.foundation.test_boundaries import imported_modules


def test_domain_import_parser_detects_outer_layer_import(tmp_path: Path) -> None:
    sample = tmp_path / "invalid_domain.py"
    sample.write_text("from fastapi import FastAPI\n", encoding="utf-8")

    assert "fastapi" in imported_modules(sample)


def test_license_guard_fails_closed_for_unknown_and_missing_metadata() -> None:
    failures = violations(
        "fixture",
        {"unknown": "GPL-3.0-only", "missing": "", "allowed": "MIT"},
        {"MIT"},
    )

    assert failures == [
        "fixture:missing: missing license metadata",
        "fixture:unknown: unapproved license 'GPL-3.0-only'",
    ]


def test_frontend_guardrails_cover_disallowed_runtime_paths() -> None:
    assert "@radix-ui/" in FORBIDDEN_SOURCE_TOKENS
    assert "createServerFn" in FORBIDDEN_SOURCE_TOKENS
    assert {"@prisma/client", "drizzle-orm", "pg", "postgres"} <= FORBIDDEN_PACKAGES
