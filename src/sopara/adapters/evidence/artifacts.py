# pyright: reportMissingTypeStubs=false
# pyright: reportUnknownArgumentType=false
# pyright: reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false

from __future__ import annotations

import hashlib
import io
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

import pyarrow as pa
import pyarrow.parquet as pq

from sopara.application.ports import ObjectStore, StoredObject
from sopara.domain.canonical import canonical_json
from sopara.domain.evidence import EvidenceClass

MARKET_EVENT_SCHEMA_ID = "sopara.market-event.parquet.v1"
MARKET_EVENT_SCHEMA = pa.schema(
    [
        pa.field("source_id", pa.string(), nullable=False),
        pa.field("channel_id", pa.string(), nullable=False),
        pa.field("source_session_id", pa.string(), nullable=False),
        pa.field("source_sequence", pa.int64(), nullable=False),
        pa.field("message_index", pa.int32(), nullable=False),
        pa.field("contract_id", pa.string(), nullable=False),
        pa.field("instrument", pa.string(), nullable=False),
        pa.field("price_ticks", pa.int64(), nullable=False),
        pa.field("quantity", pa.int64(), nullable=False),
        pa.field("source_time_ns", pa.int64(), nullable=False),
        pa.field("receive_time_ns", pa.int64(), nullable=False),
        pa.field("evidence_class", pa.string(), nullable=False),
    ],
    metadata={
        b"schema_id": MARKET_EVENT_SCHEMA_ID.encode("ascii"),
        b"compression": b"zstd",
    },
)


@dataclass(frozen=True, slots=True)
class SealedArtifact:
    stored: StoredObject
    schema_id: str
    evidence_class: EvidenceClass
    row_count: int


@dataclass(frozen=True, slots=True)
class ManifestEntry:
    key: str
    generation: int
    metageneration: int
    size: int
    crc32c: str
    sha256: str
    schema_id: str
    evidence_class: str
    row_count: int


def encode_market_events(
    rows: Iterable[Mapping[str, object]], evidence_class: EvidenceClass
) -> tuple[bytes, int]:
    materialized = []
    for row in rows:
        copied = dict(row)
        supplied_class = copied.get("evidence_class", evidence_class.value)
        if supplied_class != evidence_class.value:
            raise ValueError("row evidence class does not match the artifact evidence class")
        copied["evidence_class"] = evidence_class.value
        materialized.append(copied)
    table = pa.Table.from_pylist(materialized, schema=MARKET_EVENT_SCHEMA)
    if any(column.null_count for column in table.columns):
        raise ValueError("market evidence cannot contain null fields")
    buffer = io.BytesIO()
    pq.write_table(
        table,
        buffer,
        compression="zstd",
        version="2.6",
        write_statistics=True,
        use_dictionary=True,
    )
    payload = buffer.getvalue()
    pq.read_metadata(io.BytesIO(payload))
    return payload, table.num_rows


def seal_market_artifact(
    store: ObjectStore,
    *,
    prefix: str,
    trade_date: str,
    instrument: str,
    evidence_class: EvidenceClass,
    rows: Iterable[Mapping[str, object]],
) -> SealedArtifact:
    payload, row_count = encode_market_events(rows, evidence_class)
    digest = hashlib.sha256(payload).hexdigest()
    key = (
        f"{prefix}/schema=v1/evidence={evidence_class.value}/"
        f"trade_date={trade_date}/instrument={instrument}/{digest}.parquet"
    )
    stored = store.put_if_absent(
        key=key,
        data=payload,
        content_type="application/vnd.apache.parquet",
        metadata={
            "evidence_class": evidence_class.value,
            "schema_id": MARKET_EVENT_SCHEMA_ID,
            "sha256": digest,
            "row_count": str(row_count),
        },
    )
    if stored.sha256 != digest:
        raise ValueError("content-addressed object checksum mismatch")
    return SealedArtifact(stored, MARKET_EVENT_SCHEMA_ID, evidence_class, row_count)


def commit_manifest(
    store: ObjectStore,
    *,
    dataset_id: str,
    evidence_class: EvidenceClass,
    artifacts: Iterable[SealedArtifact],
) -> StoredObject:
    sealed = tuple(artifacts)
    for item in sealed:
        if item.evidence_class is not evidence_class:
            raise ValueError("manifest cannot mix evidence classes")
        actual = store.stat(item.stored.key)
        if actual.generation != item.stored.generation or actual.sha256 != item.stored.sha256:
            raise ValueError("manifest member no longer matches its sealed object")
    entries = [
        ManifestEntry(
            key=item.stored.key,
            generation=item.stored.generation,
            metageneration=item.stored.metageneration,
            size=item.stored.size,
            crc32c=item.stored.crc32c,
            sha256=item.stored.sha256,
            schema_id=item.schema_id,
            evidence_class=item.evidence_class.value,
            row_count=item.row_count,
        )
        for item in sealed
    ]
    payload = canonical_json(
        {
            "schema_version": "sopara.dataset-manifest.v1",
            "dataset_id": dataset_id,
            "evidence_class": evidence_class.value,
            "objects": entries,
        }
    ).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    key = f"manifests/datasets/{dataset_id}/{digest}.json"
    return store.put_if_absent(
        key=key,
        data=payload,
        content_type="application/json",
        metadata={
            "evidence_class": evidence_class.value,
            "schema_id": "sopara.dataset-manifest.v1",
            "sha256": digest,
            "object_count": str(len(entries)),
        },
    )
