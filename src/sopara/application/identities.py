from __future__ import annotations

from uuid import UUID

from sopara.domain.canonical import canonical_sha256


def uuid_from_hash(digest: str) -> UUID:
    return UUID(hex=digest[:32])


def stable_uuid(namespace: str, value: object) -> UUID:
    return uuid_from_hash(canonical_sha256({"namespace": namespace, "value": value}))
