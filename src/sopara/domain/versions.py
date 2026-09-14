from __future__ import annotations

import re
from dataclasses import dataclass

from sopara.domain.canonical import canonical_sha256
from sopara.domain.errors import DomainError

SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class VersionRef:
    component: str
    version: str
    sha256: str

    def __post_init__(self) -> None:
        if not self.component.strip() or not self.version.strip():
            raise DomainError("component and version are required")
        if not SHA256_PATTERN.fullmatch(self.sha256):
            raise DomainError("version digest must be a lowercase SHA-256")


@dataclass(frozen=True, slots=True)
class RuntimeManifest:
    manifest_version: str
    dataset: VersionRef
    calendar: VersionRef
    feature: VersionRef
    strategy: VersionRef
    simulator: VersionRef
    cost_model: VersionRef
    risk_policy: VersionRef
    input_sha256: tuple[str, ...]
    seed: int

    def __post_init__(self) -> None:
        if not self.manifest_version:
            raise DomainError("manifest version is required")
        if self.seed < 0:
            raise DomainError("seed cannot be negative")
        if not self.input_sha256 or any(
            not SHA256_PATTERN.fullmatch(digest) for digest in self.input_sha256
        ):
            raise DomainError("manifest inputs require lowercase SHA-256 digests")

    @property
    def fingerprint(self) -> str:
        return canonical_sha256(self)
