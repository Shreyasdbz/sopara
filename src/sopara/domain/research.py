from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from sopara.domain.canonical import canonical_sha256
from sopara.domain.errors import DomainError
from sopara.domain.evidence import EntitlementSnapshot, EvidenceSelection
from sopara.domain.states import DatasetState
from sopara.domain.versions import SHA256_PATTERN, RuntimeManifest, VersionRef


@dataclass(frozen=True, slots=True)
class DateInterval:
    start: date
    end: date

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise DomainError("date interval is inverted")

    def overlaps(self, other: DateInterval) -> bool:
        return self.start <= other.end and other.start <= self.end


@dataclass(frozen=True, slots=True)
class DatasetPackage:
    dataset_id: str
    source_name: str
    source_contact: str
    version: VersionRef
    state: DatasetState
    coverage: DateInterval
    entitlement: EntitlementSnapshot

    def __post_init__(self) -> None:
        if not self.dataset_id or not self.source_name or not self.source_contact:
            raise DomainError("dataset registry fields are required")
        if self.state is DatasetState.READY and not self.entitlement.permitted_uses:
            raise DomainError("a ready dataset requires at least one permitted use")


@dataclass(frozen=True, slots=True)
class CounterfactualPlan:
    names: tuple[str, ...]
    declared_at_ns: int
    outcome_horizon_ns: int

    def __post_init__(self) -> None:
        if not self.names or any(not name.strip() for name in self.names):
            raise DomainError("counterfactual names are required")
        if len(set(self.names)) != len(self.names):
            raise DomainError("counterfactual names must be unique")
        if self.declared_at_ns < 0 or self.outcome_horizon_ns <= self.declared_at_ns:
            raise DomainError("counterfactual declaration times are invalid")

    @property
    def set_hash(self) -> str:
        return canonical_sha256(tuple(sorted(self.names)))


@dataclass(frozen=True, slots=True)
class ExperimentDefinition:
    experiment_id: str
    parent_experiment_id: str | None
    hypothesis: str
    rejection_criterion: str
    manifest: RuntimeManifest
    evidence: EvidenceSelection
    training: DateInterval
    validation: DateInterval
    holdout: DateInterval
    counterfactuals: CounterfactualPlan

    def __post_init__(self) -> None:
        if (
            not self.experiment_id
            or not self.hypothesis.strip()
            or not self.rejection_criterion.strip()
        ):
            raise DomainError(
                "experiment identity, hypothesis, and rejection criterion are required"
            )
        if self.training.overlaps(self.validation) or self.training.overlaps(self.holdout):
            raise DomainError("training evidence overlaps validation or holdout")
        if self.validation.overlaps(self.holdout):
            raise DomainError("validation evidence overlaps holdout")

    @property
    def immutable_identity(self) -> str:
        return canonical_sha256(self)


class DecisionAuthority(StrEnum):
    CANONICAL = "CANONICAL"
    COUNTERFACTUAL = "COUNTERFACTUAL"
    MODEL_REVIEW = "MODEL_REVIEW"


@dataclass(frozen=True, slots=True)
class DecisionRecord:
    decision_id: str
    authority: DecisionAuthority
    canonical_decision_id: str | None
    predeclared_set_hash: str | None

    def __post_init__(self) -> None:
        if not self.decision_id:
            raise DomainError("decision identity is required")
        if self.authority is DecisionAuthority.CANONICAL:
            if self.canonical_decision_id is not None or self.predeclared_set_hash is not None:
                raise DomainError("canonical decisions cannot point to alternate authority")
        elif not self.canonical_decision_id:
            raise DomainError("alternate decisions must reference a canonical decision")
        if self.predeclared_set_hash is not None and not SHA256_PATTERN.fullmatch(
            self.predeclared_set_hash
        ):
            raise DomainError("predeclared set identity must be a lowercase SHA-256")

    @property
    def may_mutate_canonical_state(self) -> bool:
        return self.authority is DecisionAuthority.CANONICAL
