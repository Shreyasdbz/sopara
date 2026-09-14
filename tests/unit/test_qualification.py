from __future__ import annotations

from datetime import date

from sopara.domain.evidence import EvidenceClass
from sopara.domain.qualification import QualificationDay, QualificationStatus, qualification_streak


def day(
    trade_date: date,
    status: QualificationStatus = QualificationStatus.COUNTED,
    evidence: EvidenceClass = EvidenceClass.OBSERVED_NES,
    *,
    eligible: bool = True,
) -> QualificationDay:
    return QualificationDay(trade_date, eligible, status, evidence, True, True, False)


def test_invalid_or_skipped_eligible_day_resets_streak() -> None:
    days = (
        day(date(2026, 9, 1)),
        day(date(2026, 9, 2), QualificationStatus.INVALID),
        day(date(2026, 9, 3)),
        day(date(2026, 9, 4)),
    )
    assert qualification_streak(days) == 2


def test_exchange_closure_does_not_reset_streak() -> None:
    days = (
        day(date(2026, 9, 1)),
        day(date(2026, 9, 2), QualificationStatus.EXCHANGE_CLOSED, eligible=False),
        day(date(2026, 9, 3)),
    )
    assert qualification_streak(days) == 2


def test_proxy_evidence_cannot_count() -> None:
    days = (
        day(date(2026, 9, 1)),
        day(date(2026, 9, 2), evidence=EvidenceClass.SYNTHETIC_NES_PROXY),
    )
    assert qualification_streak(days) == 0
