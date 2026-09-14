from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from sopara.domain.errors import DomainError
from sopara.domain.evidence import EvidenceSelection
from sopara.domain.instruments import ContractId, Direction, Money, Price, Product
from sopara.domain.market import BookState, SourceWatermark, StreamHealth
from sopara.domain.reason_codes import ReasonCode
from sopara.domain.states import OperabilityState, ProposalState


class StrategyOutcome(StrEnum):
    PROPOSE = "PROPOSE"
    NO_TRADE = "NO_TRADE"
    INELIGIBLE = "INELIGIBLE"


@dataclass(frozen=True, slots=True)
class Proposal:
    proposal_id: str
    direction: Direction
    quantity: int
    reference_price: Price
    valid_from_ns: int
    valid_through_ns: int
    stop_policy: str
    exit_policy: str
    maximum_holding_ns: int
    feature_snapshot_id: str
    es_watermark: SourceWatermark
    nes_watermark: SourceWatermark
    evidence: EvidenceSelection

    def __post_init__(self) -> None:
        if not self.proposal_id or not self.stop_policy or not self.exit_policy:
            raise DomainError("proposal identity, stop policy, and exit policy are required")
        if not self.feature_snapshot_id:
            raise DomainError("proposal feature snapshot is required")
        if self.quantity <= 0 or self.maximum_holding_ns <= 0:
            raise DomainError("proposal quantity and holding time must be positive")
        if self.valid_from_ns < 0 or self.valid_through_ns <= self.valid_from_ns:
            raise DomainError("proposal validity interval is invalid")


@dataclass(frozen=True, slots=True)
class StrategyEvaluation:
    outcome: StrategyOutcome
    reason_codes: tuple[ReasonCode, ...]
    proposal: Proposal | None = None

    def __post_init__(self) -> None:
        if self.outcome is StrategyOutcome.PROPOSE and self.proposal is None:
            raise DomainError("PROPOSE requires a proposal")
        if self.outcome is not StrategyOutcome.PROPOSE and self.proposal is not None:
            raise DomainError("only PROPOSE may include a proposal")
        if self.outcome is not StrategyOutcome.PROPOSE and not self.reason_codes:
            raise DomainError("abstention and ineligibility require reason codes")


@dataclass(frozen=True, slots=True)
class EligibilityContext:
    lease_current: bool | None
    operability: OperabilityState | None
    entitlement_permitted: bool | None
    within_approved_window: bool | None
    source_contract: ContractId | None
    execution_contract: ContractId | None
    es_health: StreamHealth | None
    nes_health: StreamHealth | None
    nes_book: BookState | None
    evidence_class_permitted: bool | None
    now_ns: int
    duplicate_intent: bool | None
    current_position: int | None
    maximum_position: int | None
    within_loss_limit: bool | None
    exit_feasible: bool | None


@dataclass(frozen=True, slots=True)
class EvaluationStep:
    check: str
    passed: bool
    reason: ReasonCode | None = None


@dataclass(frozen=True, slots=True)
class EligibilityDecision:
    outcome: ProposalState
    steps: tuple[EvaluationStep, ...]

    @property
    def reason_codes(self) -> tuple[ReasonCode, ...]:
        return tuple(step.reason for step in self.steps if step.reason is not None)


def _required_boolean(
    steps: list[EvaluationStep], name: str, value: bool | None, failure: ReasonCode
) -> bool:
    if value is None:
        steps.append(EvaluationStep(name, False, ReasonCode.MISSING_REQUIRED_STATE))
        return False
    steps.append(EvaluationStep(name, value, None if value else failure))
    return value


def evaluate_eligibility(proposal: Proposal, context: EligibilityContext) -> EligibilityDecision:
    steps: list[EvaluationStep] = []

    if not _required_boolean(
        steps, "current_fencing_lease", context.lease_current, ReasonCode.LEASE_MISSING_OR_STALE
    ):
        return EligibilityDecision(ProposalState.INELIGIBLE, tuple(steps))
    healthy = context.operability is OperabilityState.HEALTHY
    steps.append(
        EvaluationStep(
            "operability",
            healthy,
            None
            if healthy
            else (
                ReasonCode.MISSING_REQUIRED_STATE
                if context.operability is None
                else ReasonCode.OPERABILITY_NOT_HEALTHY
            ),
        )
    )
    if not healthy:
        return EligibilityDecision(ProposalState.INELIGIBLE, tuple(steps))
    if not _required_boolean(
        steps, "entitlement", context.entitlement_permitted, ReasonCode.ENTITLEMENT_DENIED
    ):
        return EligibilityDecision(ProposalState.INELIGIBLE, tuple(steps))
    if not _required_boolean(
        steps,
        "approved_window",
        context.within_approved_window,
        ReasonCode.OUTSIDE_APPROVED_WINDOW,
    ):
        return EligibilityDecision(ProposalState.INELIGIBLE, tuple(steps))

    contracts_valid = (
        context.source_contract is not None
        and context.source_contract.product is Product.ES
        and context.execution_contract is not None
        and context.execution_contract.product is Product.NES
    )
    steps.append(
        EvaluationStep(
            "explicit_contracts",
            contracts_valid,
            None if contracts_valid else ReasonCode.EXPLICIT_CONTRACT_REQUIRED,
        )
    )
    if not contracts_valid:
        return EligibilityDecision(ProposalState.INELIGIBLE, tuple(steps))

    for name, health, stale, gap in (
        ("es_health", context.es_health, ReasonCode.ES_STALE, ReasonCode.ES_SEQUENCE_GAP),
        ("nes_health", context.nes_health, ReasonCode.NES_STALE, ReasonCode.NES_SEQUENCE_GAP),
    ):
        passed = health is StreamHealth.HEALTHY
        reason = None
        if not passed:
            reason = (
                ReasonCode.MISSING_REQUIRED_STATE
                if health is None
                else gap
                if health is StreamHealth.GAPPED
                else stale
            )
        steps.append(EvaluationStep(name, passed, reason))
        if not passed:
            return EligibilityDecision(ProposalState.INELIGIBLE, tuple(steps))

    if context.nes_book is None:
        steps.append(EvaluationStep("nes_book", False, ReasonCode.MISSING_REQUIRED_STATE))
        return EligibilityDecision(ProposalState.INELIGIBLE, tuple(steps))
    book_reason = context.nes_book.blocking_reason()
    steps.append(EvaluationStep("nes_book", book_reason is None, book_reason))
    if book_reason is not None:
        return EligibilityDecision(ProposalState.INELIGIBLE, tuple(steps))

    if not _required_boolean(
        steps,
        "evidence_class",
        context.evidence_class_permitted,
        ReasonCode.EVIDENCE_CLASS_INELIGIBLE,
    ):
        return EligibilityDecision(ProposalState.INELIGIBLE, tuple(steps))

    not_expired = proposal.valid_from_ns <= context.now_ns <= proposal.valid_through_ns
    steps.append(
        EvaluationStep(
            "proposal_expiry", not_expired, None if not_expired else ReasonCode.PROPOSAL_EXPIRED
        )
    )
    if not not_expired:
        return EligibilityDecision(ProposalState.EXPIRED, tuple(steps))

    execution_contract = context.execution_contract
    if execution_contract is None:
        raise DomainError("validated execution contract is unexpectedly absent")
    on_grid = execution_contract.spec.is_on_grid(proposal.reference_price)
    steps.append(
        EvaluationStep("nes_tick_grid", on_grid, None if on_grid else ReasonCode.NES_OFF_GRID)
    )
    if not on_grid:
        return EligibilityDecision(ProposalState.INVALID, tuple(steps))

    no_duplicate = context.duplicate_intent is False
    steps.append(
        EvaluationStep(
            "duplicate_intent",
            no_duplicate,
            None
            if no_duplicate
            else (
                ReasonCode.MISSING_REQUIRED_STATE
                if context.duplicate_intent is None
                else ReasonCode.DUPLICATE_INTENT
            ),
        )
    )
    if not no_duplicate:
        return EligibilityDecision(ProposalState.INELIGIBLE, tuple(steps))

    current_position = context.current_position
    maximum_position = context.maximum_position
    within_position = False
    if current_position is not None and maximum_position is not None:
        within_position = (
            abs(current_position + proposal.direction.sign * proposal.quantity) <= maximum_position
        )
    position_known = current_position is not None and maximum_position is not None
    steps.append(
        EvaluationStep(
            "position_limit",
            within_position,
            None
            if within_position
            else ReasonCode.POSITION_LIMIT
            if position_known
            else ReasonCode.MISSING_REQUIRED_STATE,
        )
    )
    if not within_position:
        return EligibilityDecision(ProposalState.RISK_REJECTED, tuple(steps))

    if not _required_boolean(steps, "loss_limit", context.within_loss_limit, ReasonCode.LOSS_LIMIT):
        return EligibilityDecision(ProposalState.RISK_REJECTED, tuple(steps))
    if not _required_boolean(
        steps, "exit_feasibility", context.exit_feasible, ReasonCode.EXIT_INFEASIBLE
    ):
        return EligibilityDecision(ProposalState.RISK_REJECTED, tuple(steps))
    return EligibilityDecision(ProposalState.SIM_ACCEPTED, tuple(steps))


@dataclass(frozen=True, slots=True)
class RiskDecision:
    accepted: bool
    requested_quantity: int
    allowed_quantity: int
    projected_loss: Money
    reason_codes: tuple[ReasonCode, ...]
