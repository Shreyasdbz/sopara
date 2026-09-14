from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from sopara.application.ports import MarketObjectExpectation, ObjectNotFoundError, ObjectStore


class MismatchKind(StrEnum):
    MISSING_OBJECT = "MISSING_OBJECT"
    GENERATION = "GENERATION"
    METAGENERATION = "METAGENERATION"
    SIZE = "SIZE"
    CRC32C = "CRC32C"
    SHA256 = "SHA256"
    EVIDENCE_CLASS = "EVIDENCE_CLASS"
    UNREFERENCED_OBJECT = "UNREFERENCED_OBJECT"


@dataclass(frozen=True, slots=True)
class EvidenceMismatch:
    object_key: str
    kind: MismatchKind
    expected: str
    actual: str | None


@dataclass(frozen=True, slots=True)
class EvidenceReconciliation:
    checked_objects: int
    mismatches: tuple[EvidenceMismatch, ...]

    @property
    def accepted(self) -> bool:
        return not self.mismatches


def reconcile_objects(
    store: ObjectStore,
    expectations: Iterable[MarketObjectExpectation],
    *,
    prefix: str = "objects/",
) -> EvidenceReconciliation:
    mismatches: list[EvidenceMismatch] = []
    count = 0
    expected_items = tuple(expectations)
    expected_keys = {item.object_key for item in expected_items}
    for expected in expected_items:
        count += 1
        try:
            actual = store.stat(expected.object_key)
        except ObjectNotFoundError:
            mismatches.append(
                EvidenceMismatch(expected.object_key, MismatchKind.MISSING_OBJECT, "present", None)
            )
            continue
        comparisons = (
            (MismatchKind.GENERATION, expected.generation, actual.generation),
            (MismatchKind.METAGENERATION, expected.metageneration, actual.metageneration),
            (MismatchKind.SIZE, expected.size, actual.size),
            (MismatchKind.CRC32C, expected.crc32c, actual.crc32c),
            (MismatchKind.SHA256, expected.sha256, actual.sha256),
            (
                MismatchKind.EVIDENCE_CLASS,
                expected.evidence_class.value,
                actual.metadata.get("evidence_class"),
            ),
        )
        for kind, expected_value, actual_value in comparisons:
            if expected_value != actual_value:
                mismatches.append(
                    EvidenceMismatch(
                        expected.object_key,
                        kind,
                        str(expected_value),
                        None if actual_value is None else str(actual_value),
                    )
                )
    mismatches.extend(
        EvidenceMismatch(
            actual.key,
            MismatchKind.UNREFERENCED_OBJECT,
            "SQL market_object row",
            "object exists without SQL record",
        )
        for actual in store.list(prefix)
        if actual.key not in expected_keys
    )
    return EvidenceReconciliation(count, tuple(mismatches))
