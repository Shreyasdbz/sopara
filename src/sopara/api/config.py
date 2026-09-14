from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ControlPlaneConfig:
    database_url: str
    iap_audience: str
    owner_email: str
    public_origin: str
    csrf_secret: bytes
    build_revision: str = "development"
    build_image_digest: str = "unbuilt"
    api_schema_version: int = 1
    recent_auth_seconds: int = 900
    csrf_max_age_seconds: int = 900
    cursor_secret: bytes = b"local-test-cursor-secret-32-bytes"
    static_root: Path = Path("/app/static")
    secure_cookies: bool = True
    sse_poll_seconds: float = 1.0
    sse_heartbeat_seconds: float = 15.0
    sse_connection_seconds: float = 3_240.0

    def __post_init__(self) -> None:
        if len(self.csrf_secret) < 32 or len(self.cursor_secret) < 32:
            raise ValueError("CSRF and cursor secrets must each be at least 32 bytes")
        if self.api_schema_version <= 0:
            raise ValueError("API schema version must be positive")
        if (
            min(
                self.recent_auth_seconds,
                self.csrf_max_age_seconds,
                self.sse_poll_seconds,
                self.sse_heartbeat_seconds,
                self.sse_connection_seconds,
            )
            <= 0
        ):
            raise ValueError("security ages and SSE intervals must be positive")

    @classmethod
    def from_environment(cls) -> ControlPlaneConfig:
        required = {
            name: os.environ.get(name)
            for name in (
                "SOPARA_DATABASE_URL",
                "SOPARA_IAP_AUDIENCE",
                "SOPARA_OWNER_EMAIL",
                "SOPARA_PUBLIC_ORIGIN",
                "SOPARA_CSRF_SECRET",
                "SOPARA_CURSOR_SECRET",
            )
        }
        missing = sorted(name for name, value in required.items() if not value)
        if missing:
            raise RuntimeError(f"missing required environment variables: {', '.join(missing)}")
        return cls(
            database_url=str(required["SOPARA_DATABASE_URL"]),
            iap_audience=str(required["SOPARA_IAP_AUDIENCE"]),
            owner_email=str(required["SOPARA_OWNER_EMAIL"]),
            public_origin=str(required["SOPARA_PUBLIC_ORIGIN"]).rstrip("/"),
            csrf_secret=str(required["SOPARA_CSRF_SECRET"]).encode(),
            cursor_secret=str(required["SOPARA_CURSOR_SECRET"]).encode(),
            build_revision=os.environ.get("SOPARA_BUILD_REVISION", "development"),
            build_image_digest=os.environ.get("SOPARA_BUILD_IMAGE_DIGEST", "unbuilt"),
            static_root=Path(os.environ.get("SOPARA_STATIC_ROOT", "/app/static")),
        )
