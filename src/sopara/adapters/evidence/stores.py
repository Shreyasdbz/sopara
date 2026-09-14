# pyright: reportMissingTypeStubs=false
# pyright: reportUnknownArgumentType=false
# pyright: reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import cast

import google_crc32c
from google.api_core.exceptions import NotFound, PreconditionFailed
from google.cloud import storage

from sopara.application.ports import ObjectNotFoundError, StoredObject
from sopara.domain.evidence import EvidenceClass


def _crc32c_fallback(data: bytes) -> str:
    return base64.b64encode(google_crc32c.Checksum(data).digest()).decode("ascii")


class MemoryObjectStore:
    def __init__(self, evidence_class: EvidenceClass) -> None:
        if evidence_class is not EvidenceClass.SYNTHETIC_NES_PROXY:
            raise ValueError("memory object stores are restricted to synthetic proxy evidence")
        self._objects: dict[str, tuple[bytes, StoredObject]] = {}
        self._next_generation = 1

    def put_if_absent(
        self,
        *,
        key: str,
        data: bytes,
        content_type: str,
        metadata: Mapping[str, str],
    ) -> StoredObject:
        _require_synthetic_metadata(metadata)
        existing = self._objects.get(key)
        if existing is not None:
            if existing[0] != data:
                raise ValueError("object key collision has different content")
            return _replace_created(existing[1], created=False)
        sha256 = hashlib.sha256(data).hexdigest()
        item = StoredObject(
            key=key,
            generation=self._next_generation,
            metageneration=1,
            size=len(data),
            crc32c=_crc32c_fallback(data),
            sha256=sha256,
            content_type=content_type,
            metadata=dict(metadata),
            created=True,
        )
        self._next_generation += 1
        self._objects[key] = (data, item)
        return item

    def stat(self, key: str) -> StoredObject:
        try:
            return _replace_created(self._objects[key][1], created=False)
        except KeyError as error:
            raise ObjectNotFoundError(key) from error

    def read(self, key: str) -> bytes:
        try:
            return self._objects[key][0]
        except KeyError as error:
            raise ObjectNotFoundError(key) from error

    def list(self, prefix: str) -> tuple[StoredObject, ...]:
        return tuple(
            _replace_created(item, created=False)
            for key, (_, item) in sorted(self._objects.items())
            if key.startswith(prefix)
        )


class SyntheticFilesystemObjectStore:
    def __init__(self, root: Path, evidence_class: EvidenceClass) -> None:
        if evidence_class is not EvidenceClass.SYNTHETIC_NES_PROXY:
            raise ValueError("filesystem object stores are restricted to synthetic proxy evidence")
        self._root = root.resolve()
        self._metadata_root = self._root / ".metadata"
        self._next_generation = 1
        self._root.mkdir(parents=True, exist_ok=True)
        self._metadata_root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        candidate = (self._root / key).resolve()
        if not candidate.is_relative_to(self._root) or candidate == self._root:
            raise ValueError("object key escapes the synthetic store root")
        return candidate

    def _metadata_path(self, key: str) -> Path:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return self._metadata_root / f"{digest}.json"

    def put_if_absent(
        self,
        *,
        key: str,
        data: bytes,
        content_type: str,
        metadata: Mapping[str, str],
    ) -> StoredObject:
        _require_synthetic_metadata(metadata)
        target = self._path(key)
        if target.exists():
            if target.read_bytes() != data:
                raise ValueError("object key collision has different content")
            return self.stat(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.partial")
        temporary.write_bytes(data)
        temporary.replace(target)
        item = StoredObject(
            key=key,
            generation=self._next_generation,
            metageneration=1,
            size=len(data),
            crc32c=_crc32c_fallback(data),
            sha256=hashlib.sha256(data).hexdigest(),
            content_type=content_type,
            metadata=dict(metadata),
            created=True,
        )
        self._next_generation += 1
        self._metadata_path(key).write_text(
            json.dumps(_stored_to_dict(item), sort_keys=True), encoding="utf-8"
        )
        return item

    def stat(self, key: str) -> StoredObject:
        target = self._path(key)
        metadata_path = self._metadata_path(key)
        if not target.exists() or not metadata_path.exists():
            raise ObjectNotFoundError(key)
        raw = json.loads(metadata_path.read_text(encoding="utf-8"))
        return StoredObject(
            key=raw["key"],
            generation=raw["generation"],
            metageneration=raw["metageneration"],
            size=raw["size"],
            crc32c=raw["crc32c"],
            sha256=raw["sha256"],
            content_type=raw["content_type"],
            metadata=raw["metadata"],
            created=False,
        )

    def read(self, key: str) -> bytes:
        try:
            return self._path(key).read_bytes()
        except FileNotFoundError as error:
            raise ObjectNotFoundError(key) from error

    def list(self, prefix: str) -> tuple[StoredObject, ...]:
        listed: list[StoredObject] = []
        for path in sorted(self._metadata_root.glob("*.json")):
            raw = json.loads(path.read_text(encoding="utf-8"))
            key = raw["key"]
            if isinstance(key, str) and key.startswith(prefix):
                listed.append(self.stat(key))
        return tuple(listed)


class GCSObjectStore:
    def __init__(self, bucket: storage.Bucket) -> None:
        self._bucket = bucket

    def put_if_absent(
        self,
        *,
        key: str,
        data: bytes,
        content_type: str,
        metadata: Mapping[str, str],
    ) -> StoredObject:
        if metadata.get("evidence_class") not in {item.value for item in EvidenceClass}:
            raise ValueError("GCS objects require an explicit valid evidence class")
        digest = hashlib.sha256(data).hexdigest()
        blob = self._bucket.blob(key)
        blob.metadata = {**metadata, "sha256": digest}
        created = True
        try:
            blob.upload_from_string(
                data,
                content_type=content_type,
                if_generation_match=0,
                checksum="auto",
            )
        except PreconditionFailed:
            created = False
            blob.reload()
        item = _stored_from_blob(blob, created=created)
        if item.size != len(data) or item.sha256 != digest:
            raise ValueError("GCS object collision does not match the proposed content")
        return item

    def stat(self, key: str) -> StoredObject:
        blob = self._bucket.blob(key)
        try:
            blob.reload()
        except NotFound as error:
            raise ObjectNotFoundError(key) from error
        return _stored_from_blob(blob, created=False)

    def read(self, key: str) -> bytes:
        blob = self._bucket.blob(key)
        try:
            return blob.download_as_bytes(checksum="auto")
        except NotFound as error:
            raise ObjectNotFoundError(key) from error

    def list(self, prefix: str) -> tuple[StoredObject, ...]:
        return tuple(
            _stored_from_blob(blob, created=False)
            for blob in self._bucket.list_blobs(prefix=prefix)
        )


def _stored_from_blob(blob: storage.Blob, *, created: bool) -> StoredObject:
    metadata = cast(dict[str, str], dict(blob.metadata or {}))
    digest = metadata.get("sha256")
    if digest is None:
        raise ValueError("GCS object is missing canonical SHA-256 metadata")
    if blob.generation is None or blob.metageneration is None or blob.size is None:
        raise ValueError("GCS object metadata is incomplete")
    if blob.crc32c is None or blob.content_type is None:
        raise ValueError("GCS object integrity metadata is incomplete")
    return StoredObject(
        key=cast(str, blob.name),
        generation=int(blob.generation),
        metageneration=int(blob.metageneration),
        size=int(blob.size),
        crc32c=blob.crc32c,
        sha256=digest,
        content_type=blob.content_type,
        metadata=metadata,
        created=created,
    )


def _replace_created(item: StoredObject, *, created: bool) -> StoredObject:
    return StoredObject(
        key=item.key,
        generation=item.generation,
        metageneration=item.metageneration,
        size=item.size,
        crc32c=item.crc32c,
        sha256=item.sha256,
        content_type=item.content_type,
        metadata=item.metadata,
        created=created,
    )


def _stored_to_dict(item: StoredObject) -> dict[str, object]:
    return {
        "key": item.key,
        "generation": item.generation,
        "metageneration": item.metageneration,
        "size": item.size,
        "crc32c": item.crc32c,
        "sha256": item.sha256,
        "content_type": item.content_type,
        "metadata": dict(item.metadata),
    }


def _require_synthetic_metadata(metadata: Mapping[str, str]) -> None:
    if metadata.get("evidence_class") != EvidenceClass.SYNTHETIC_NES_PROXY.value:
        raise ValueError("local object stores accept synthetic proxy evidence only")
