from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from sopara.domain.errors import DomainError
from sopara.domain.reason_codes import ReasonCode

NES_LAUNCH_DATE = date(2026, 8, 24)


class EvidenceClass(StrEnum):
    OBSERVED_NES = "OBSERVED_NES"
    SYNTHETIC_NES_PROXY = "SYNTHETIC_NES_PROXY"
    NO_EXECUTION_EVIDENCE = "NO_EXECUTION_EVIDENCE"


class EvidenceStatus(StrEnum):
    VERIFIED_FACT = "VERIFIED_FACT"
    CONFIGURED_ASSUMPTION = "CONFIGURED_ASSUMPTION"
    INFERRED_METRIC = "INFERRED_METRIC"
    UNRESOLVED_HYPOTHESIS = "UNRESOLVED_HYPOTHESIS"


class PermittedUse(StrEnum):
    HISTORICAL_REPLAY = "HISTORICAL_REPLAY"
    LIVE_SIMULATION = "LIVE_SIMULATION"
    EXPORT = "EXPORT"
    MODEL_REVIEW = "MODEL_REVIEW"


@dataclass(frozen=True, slots=True)
class EvidenceSelection:
    evidence_class: EvidenceClass
    first_trade_date: date
    proxy_opt_in: bool = False

    def __post_init__(self) -> None:
        if (
            self.evidence_class is EvidenceClass.OBSERVED_NES
            and self.first_trade_date < NES_LAUNCH_DATE
        ):
            raise DomainError(ReasonCode.OBSERVED_NES_PRE_LAUNCH)
        if self.evidence_class is EvidenceClass.SYNTHETIC_NES_PROXY and not self.proxy_opt_in:
            raise DomainError(ReasonCode.PROXY_OPT_IN_REQUIRED)

    @property
    def qualifies_as_observed_nes(self) -> bool:
        return self.evidence_class is EvidenceClass.OBSERVED_NES


@dataclass(frozen=True, slots=True)
class EntitlementSnapshot:
    snapshot_id: str
    source_id: str
    owner: str
    effective_from: date
    effective_through: date
    permitted_uses: frozenset[PermittedUse]

    def __post_init__(self) -> None:
        if not self.snapshot_id or not self.source_id or not self.owner:
            raise DomainError("entitlement identity, source, and owner are required")
        if self.effective_through < self.effective_from:
            raise DomainError("entitlement effective period is inverted")

    def permits(self, use: PermittedUse, trade_date: date) -> bool:
        return (
            self.effective_from <= trade_date <= self.effective_through
            and use in self.permitted_uses
        )
