from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from sopara.domain.errors import DomainError
from sopara.domain.evidence import EvidenceClass


class QualificationStatus(StrEnum):
    COUNTED = "COUNTED"
    INVALID = "INVALID"
    SKIPPED = "SKIPPED"
    EXCHANGE_CLOSED = "EXCHANGE_CLOSED"


class ResearchConclusion(StrEnum):
    SUPPORTED_FOR_MORE_TESTING = "SUPPORTED_FOR_MORE_TESTING"
    FALSIFIED = "FALSIFIED"
    INCONCLUSIVE = "INCONCLUSIVE"
    INVALID_EVIDENCE = "INVALID_EVIDENCE"
    EVIDENCE_READY_FOR_FUTURE_SHADOW_REVIEW = "EVIDENCE_READY_FOR_FUTURE_SHADOW_REVIEW"


@dataclass(frozen=True, slots=True)
class QualificationDay:
    trade_date: date
    eligible_trade_date: bool
    status: QualificationStatus
    evidence_class: EvidenceClass
    both_windows_reconciled: bool
    manifest_consistent: bool
    unresolved_critical_incident: bool

    @property
    def counts(self) -> bool:
        return (
            self.eligible_trade_date
            and self.status is QualificationStatus.COUNTED
            and self.evidence_class is EvidenceClass.OBSERVED_NES
            and self.both_windows_reconciled
            and self.manifest_consistent
            and not self.unresolved_critical_incident
        )


def qualification_streak(days: tuple[QualificationDay, ...]) -> int:
    dates = [day.trade_date for day in days]
    if dates != sorted(dates) or len(dates) != len(set(dates)):
        raise DomainError("qualification days must be unique and ordered")
    streak = 0
    for day in days:
        if day.status is QualificationStatus.EXCHANGE_CLOSED and not day.eligible_trade_date:
            continue
        if day.counts:
            streak += 1
        elif day.eligible_trade_date:
            streak = 0
    return streak
