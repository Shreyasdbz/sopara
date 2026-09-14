from __future__ import annotations

import hashlib
from collections.abc import Sequence

from sopara.adapters.replay.serialization import REPLAY_EVENT_SCHEMA_ID, encode_events
from sopara.application.ports import ObjectStore, ReplayArtifact, StoredObject
from sopara.domain.canonical import canonical_json
from sopara.domain.evidence import EvidenceClass
from sopara.domain.replay import ReplayEvent, ReplayResult


def seal_replay_events(
    store: ObjectStore,
    *,
    dataset_id: str,
    trade_date: str,
    instrument: str,
    evidence_class: EvidenceClass,
    events: Sequence[ReplayEvent],
) -> ReplayArtifact:
    payload = encode_events(events, evidence_class)
    digest = hashlib.sha256(payload).hexdigest()
    key = (
        f"objects/replay/schema=v1/evidence={evidence_class.value}/"
        f"trade_date={trade_date}/instrument={instrument}/{digest}.parquet"
    )
    stored = store.put_if_absent(
        key=key,
        data=payload,
        content_type="application/vnd.apache.parquet",
        metadata={
            "dataset_id": dataset_id,
            "evidence_class": evidence_class.value,
            "schema_id": REPLAY_EVENT_SCHEMA_ID,
            "sha256": digest,
            "row_count": str(len(events)),
            "instrument": instrument,
        },
    )
    if stored.sha256 != digest:
        raise ValueError("replay event artifact checksum mismatch")
    return ReplayArtifact(instrument, len(events), stored)


def seal_replay_dataset_manifest(
    store: ObjectStore,
    *,
    dataset_id: str,
    fixture_hash: str,
    trade_date: str,
    evidence_class: EvidenceClass,
    artifacts: Sequence[ReplayArtifact],
) -> StoredObject:
    entries: list[dict[str, object]] = []
    for artifact in sorted(artifacts, key=lambda item: item.instrument):
        actual = store.stat(artifact.stored.key)
        if (
            actual.generation != artifact.stored.generation
            or actual.sha256 != artifact.stored.sha256
        ):
            raise ValueError("dataset artifact changed before manifest commit")
        entries.append(
            {
                "instrument": artifact.instrument,
                "row_count": artifact.row_count,
                "key": actual.key,
                "generation": actual.generation,
                "metageneration": actual.metageneration,
                "size": actual.size,
                "crc32c": actual.crc32c,
                "sha256": actual.sha256,
                "schema_id": REPLAY_EVENT_SCHEMA_ID,
                "evidence_class": evidence_class.value,
            }
        )
    payload = canonical_json(
        {
            "schema_version": "sopara.replay-dataset-manifest.v1",
            "dataset_id": dataset_id,
            "fixture_hash": fixture_hash,
            "trade_date": trade_date,
            "evidence_class": evidence_class.value,
            "objects": entries,
        }
    ).encode()
    digest = hashlib.sha256(payload).hexdigest()
    return store.put_if_absent(
        key=f"manifests/replay-datasets/{dataset_id}/{digest}.json",
        data=payload,
        content_type="application/json",
        metadata={
            "evidence_class": evidence_class.value,
            "schema_id": "sopara.replay-dataset-manifest.v1",
            "sha256": digest,
            "object_count": str(len(entries)),
        },
    )


def seal_replay_result(store: ObjectStore, *, run_id: str, result: ReplayResult) -> StoredObject:
    payload = canonical_json(result).encode()
    digest = hashlib.sha256(payload).hexdigest()
    return store.put_if_absent(
        key=f"objects/replay-results/evidence={result.evidence_class.value}/{run_id}/{digest}.json",
        data=payload,
        content_type="application/json",
        metadata={
            "evidence_class": result.evidence_class.value,
            "schema_id": result.schema_version,
            "sha256": digest,
        },
    )


def quarantine_fixture(store: ObjectStore, *, raw: bytes, reason: str) -> StoredObject:
    digest = hashlib.sha256(raw).hexdigest()
    return store.put_if_absent(
        key=f"quarantine/replay-fixtures/{digest}.json",
        data=raw,
        content_type="application/json",
        metadata={
            "evidence_class": EvidenceClass.SYNTHETIC_NES_PROXY.value,
            "schema_id": "sopara.quarantined-replay-fixture.v1",
            "sha256": digest,
            "reason": reason[:256],
        },
    )
