# pyright: reportMissingTypeStubs=false
# pyright: reportUnknownMemberType=false

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from typing import Any, cast

import pyarrow.parquet as pq
import pytest

from sopara.adapters.evidence.artifacts import (
    MARKET_EVENT_SCHEMA_ID,
    commit_manifest,
    encode_market_events,
    seal_market_artifact,
)
from sopara.adapters.evidence.reconciliation import MismatchKind, reconcile_objects
from sopara.adapters.evidence.stores import (
    GCSObjectStore,
    MemoryObjectStore,
    SyntheticFilesystemObjectStore,
)
from sopara.application.ports import MarketObjectExpectation
from sopara.domain.evidence import EvidenceClass


def market_row() -> dict[str, object]:
    return {
        "source_id": "source-1",
        "channel_id": "prices",
        "source_session_id": "session-1",
        "source_sequence": 1,
        "message_index": 0,
        "contract_id": "NES-2026-09-18",
        "instrument": "NES",
        "price_ticks": 12000,
        "quantity": 1,
        "source_time_ns": 1_789_000_000_000_000_000,
        "receive_time_ns": 1_789_000_000_000_000_001,
    }


def test_parquet_schema_compression_and_evidence_class() -> None:
    payload, count = encode_market_events([market_row()], EvidenceClass.SYNTHETIC_NES_PROXY)
    parquet = pq.ParquetFile(io.BytesIO(payload))
    assert count == 1
    assert parquet.schema_arrow.metadata is not None
    assert parquet.schema_arrow.metadata[b"schema_id"] == MARKET_EVENT_SCHEMA_ID.encode()
    assert parquet.metadata.row_group(0).column(0).compression == "ZSTD"
    assert parquet.read().column("evidence_class").to_pylist() == ["SYNTHETIC_NES_PROXY"]


def test_object_and_manifest_commit_are_content_addressed_and_idempotent() -> None:
    store = MemoryObjectStore(EvidenceClass.SYNTHETIC_NES_PROXY)
    artifact = seal_market_artifact(
        store,
        prefix="objects/raw",
        trade_date="2026-09-10",
        instrument="NES",
        evidence_class=EvidenceClass.SYNTHETIC_NES_PROXY,
        rows=[market_row()],
    )
    duplicate = seal_market_artifact(
        store,
        prefix="objects/raw",
        trade_date="2026-09-10",
        instrument="NES",
        evidence_class=EvidenceClass.SYNTHETIC_NES_PROXY,
        rows=[market_row()],
    )
    assert artifact.stored.created is True
    assert duplicate.stored.created is False
    assert duplicate.stored.generation == artifact.stored.generation

    manifest = commit_manifest(
        store,
        dataset_id="dataset-1",
        evidence_class=EvidenceClass.SYNTHETIC_NES_PROXY,
        artifacts=[artifact],
    )
    body = json.loads(store.read(manifest.key))
    assert body["evidence_class"] == "SYNTHETIC_NES_PROXY"
    assert body["objects"][0]["generation"] == artifact.stored.generation
    assert manifest.key.endswith(f"/{hashlib.sha256(store.read(manifest.key)).hexdigest()}.json")


def test_collision_and_reconciliation_mismatch_fail_closed() -> None:
    store = MemoryObjectStore(EvidenceClass.SYNTHETIC_NES_PROXY)
    first = store.put_if_absent(
        key="objects/collision",
        data=b"first",
        content_type="application/octet-stream",
        metadata={"evidence_class": "SYNTHETIC_NES_PROXY"},
    )
    with pytest.raises(ValueError, match="collision"):
        store.put_if_absent(
            key="objects/collision",
            data=b"second",
            content_type="application/octet-stream",
            metadata={"evidence_class": "SYNTHETIC_NES_PROXY"},
        )
    expectation = MarketObjectExpectation(
        object_key=first.key,
        generation=first.generation,
        metageneration=first.metageneration,
        size=first.size,
        crc32c=first.crc32c,
        sha256="0" * 64,
        evidence_class=EvidenceClass.SYNTHETIC_NES_PROXY,
    )
    result = reconcile_objects(store, [expectation])
    assert result.accepted is False
    assert [item.kind for item in result.mismatches] == [MismatchKind.SHA256]


def test_reconciliation_detects_both_sides_of_object_sql_disagreement() -> None:
    store = MemoryObjectStore(EvidenceClass.SYNTHETIC_NES_PROXY)
    orphan = store.put_if_absent(
        key="objects/orphan",
        data=b"sealed-before-sql",
        content_type="application/octet-stream",
        metadata={"evidence_class": "SYNTHETIC_NES_PROXY"},
    )
    orphan_result = reconcile_objects(store, [])
    assert orphan_result.accepted is False
    assert orphan_result.mismatches[0].kind is MismatchKind.UNREFERENCED_OBJECT

    missing = MarketObjectExpectation(
        object_key="objects/missing",
        generation=1,
        metageneration=1,
        size=1,
        crc32c="crc",
        sha256="a" * 64,
        evidence_class=EvidenceClass.SYNTHETIC_NES_PROXY,
    )
    missing_result = reconcile_objects(
        MemoryObjectStore(EvidenceClass.SYNTHETIC_NES_PROXY), [missing]
    )
    assert missing_result.accepted is False
    assert missing_result.mismatches[0].kind is MismatchKind.MISSING_OBJECT
    assert orphan.created is True


def test_filesystem_store_rejects_canonical_evidence(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="synthetic"):
        SyntheticFilesystemObjectStore(tmp_path, EvidenceClass.OBSERVED_NES)
    with pytest.raises(ValueError, match="synthetic"):
        MemoryObjectStore(EvidenceClass.OBSERVED_NES)


class FakeBlob:
    def __init__(self, name: str) -> None:
        self.name = name
        self.metadata: dict[str, str] | None = None
        self.generation: int | None = None
        self.metageneration: int | None = None
        self.size: int | None = None
        self.crc32c: str | None = None
        self.content_type: str | None = None
        self.upload_arguments: dict[str, object] = {}

    def upload_from_string(self, data: bytes, **kwargs: object) -> None:
        self.upload_arguments = kwargs
        self.generation = 7
        self.metageneration = 1
        self.size = len(data)
        self.crc32c = "fixture-crc32c"
        self.content_type = cast(str, kwargs["content_type"])


class FakeBucket:
    def __init__(self) -> None:
        self.created_blob: FakeBlob | None = None

    def blob(self, key: str) -> FakeBlob:
        self.created_blob = FakeBlob(key)
        return self.created_blob


def test_gcs_adapter_requires_create_only_generation_precondition() -> None:
    bucket = FakeBucket()
    store = GCSObjectStore(cast(Any, bucket))
    result = store.put_if_absent(
        key="objects/test",
        data=b"evidence",
        content_type="application/octet-stream",
        metadata={"evidence_class": "SYNTHETIC_NES_PROXY"},
    )
    assert bucket.created_blob is not None
    assert bucket.created_blob.upload_arguments["if_generation_match"] == 0
    assert bucket.created_blob.upload_arguments["checksum"] == "auto"
    assert result.sha256 == hashlib.sha256(b"evidence").hexdigest()
