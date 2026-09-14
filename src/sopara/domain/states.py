from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TypeVar

from sopara.domain.errors import DomainError, InvalidTransitionError
from sopara.domain.reason_codes import ReasonCode


class DatasetState(StrEnum):
    DISCOVERED = "DISCOVERED"
    INGESTING = "INGESTING"
    VALIDATING = "VALIDATING"
    READY = "READY"
    QUARANTINED = "QUARANTINED"
    SUPERSEDED = "SUPERSEDED"


class ExperimentState(StrEnum):
    DRAFT = "DRAFT"
    VALIDATING = "VALIDATING"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    INVALID = "INVALID"
    CANCELLED = "CANCELLED"
    REVIEWED = "REVIEWED"
    EVIDENCE_READY_FOR_FUTURE_SHADOW_REVIEW = "EVIDENCE_READY_FOR_FUTURE_SHADOW_REVIEW"
    REJECTED = "REJECTED"
    INCONCLUSIVE = "INCONCLUSIVE"


class StrategyState(StrEnum):
    DRAFT = "DRAFT"
    REGISTERED = "REGISTERED"
    BACKTESTED = "BACKTESTED"
    WALK_FORWARD_ELIGIBLE = "WALK_FORWARD_ELIGIBLE"
    LIVE_SIM_ELIGIBLE = "LIVE_SIM_ELIGIBLE"
    REJECTED = "REJECTED"
    RETIRED = "RETIRED"


class OperabilityState(StrEnum):
    OFFLINE = "OFFLINE"
    STARTING = "STARTING"
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    HALTING = "HALTING"
    HALTED = "HALTED"
    RECONCILING = "RECONCILING"
    RECOVERY_REVIEW = "RECOVERY_REVIEW"


class OrderState(StrEnum):
    PROPOSED = "PROPOSED"
    EXPIRED = "EXPIRED"
    INVALID = "INVALID"
    RISK_REJECTED = "RISK_REJECTED"
    SIM_ACCEPTED = "SIM_ACCEPTED"
    CREATED = "CREATED"
    OPEN = "OPEN"
    REJECTED = "REJECTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCEL_PENDING = "CANCEL_PENDING"
    CANCELLED = "CANCELLED"
    EXIT_PENDING = "EXIT_PENDING"
    CLOSED = "CLOSED"


class ProposalState(StrEnum):
    GENERATED = "GENERATED"
    INVALID = "INVALID"
    EXPIRED = "EXPIRED"
    INELIGIBLE = "INELIGIBLE"
    RISK_REJECTED = "RISK_REJECTED"
    SIM_ACCEPTED = "SIM_ACCEPTED"


class LiveSessionState(StrEnum):
    SCHEDULED = "SCHEDULED"
    PREFLIGHT = "PREFLIGHT"
    RUNNING = "RUNNING"
    DEGRADED = "DEGRADED"
    HALTING = "HALTING"
    HALTED = "HALTED"
    RECONCILING = "RECONCILING"
    CLOSED = "CLOSED"


class WindowState(StrEnum):
    SCHEDULED = "SCHEDULED"
    PREFLIGHT = "PREFLIGHT"
    RUNNING = "RUNNING"
    DEGRADED = "DEGRADED"
    HALTING = "HALTING"
    HALTED = "HALTED"
    RECONCILING = "RECONCILING"
    CLOSED = "CLOSED"


class ReconciliationState(StrEnum):
    REQUESTED = "REQUESTED"
    RUNNING = "RUNNING"
    MATCHED = "MATCHED"
    MISMATCHED = "MISMATCHED"
    FAILED = "FAILED"


class RecoveryState(StrEnum):
    PENDING_RECONCILIATION = "PENDING_RECONCILIATION"
    PENDING_OWNER_REVIEW = "PENDING_OWNER_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class IncidentState(StrEnum):
    OPEN = "OPEN"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    MITIGATED = "MITIGATED"
    RESOLVED = "RESOLVED"
    REOPENED = "REOPENED"


DATASET_TRANSITIONS = {
    DatasetState.DISCOVERED: frozenset({DatasetState.INGESTING}),
    DatasetState.INGESTING: frozenset({DatasetState.VALIDATING}),
    DatasetState.VALIDATING: frozenset({DatasetState.READY, DatasetState.QUARANTINED}),
    DatasetState.QUARANTINED: frozenset({DatasetState.VALIDATING}),
    DatasetState.READY: frozenset({DatasetState.SUPERSEDED}),
}
EXPERIMENT_TRANSITIONS = {
    ExperimentState.DRAFT: frozenset({ExperimentState.VALIDATING}),
    ExperimentState.VALIDATING: frozenset({ExperimentState.DRAFT, ExperimentState.QUEUED}),
    ExperimentState.QUEUED: frozenset({ExperimentState.RUNNING, ExperimentState.CANCELLED}),
    ExperimentState.RUNNING: frozenset(
        {
            ExperimentState.COMPLETED,
            ExperimentState.FAILED,
            ExperimentState.INVALID,
            ExperimentState.CANCELLED,
        }
    ),
    ExperimentState.COMPLETED: frozenset({ExperimentState.REVIEWED}),
    ExperimentState.REVIEWED: frozenset(
        {
            ExperimentState.EVIDENCE_READY_FOR_FUTURE_SHADOW_REVIEW,
            ExperimentState.REJECTED,
            ExperimentState.INCONCLUSIVE,
        }
    ),
}
STRATEGY_TRANSITIONS = {
    StrategyState.DRAFT: frozenset({StrategyState.REGISTERED}),
    StrategyState.REGISTERED: frozenset({StrategyState.BACKTESTED, StrategyState.REJECTED}),
    StrategyState.BACKTESTED: frozenset(
        {StrategyState.WALK_FORWARD_ELIGIBLE, StrategyState.REJECTED}
    ),
    StrategyState.WALK_FORWARD_ELIGIBLE: frozenset(
        {StrategyState.LIVE_SIM_ELIGIBLE, StrategyState.REJECTED}
    ),
    StrategyState.LIVE_SIM_ELIGIBLE: frozenset({StrategyState.RETIRED}),
}
OPERABILITY_TRANSITIONS = {
    OperabilityState.OFFLINE: frozenset({OperabilityState.STARTING}),
    OperabilityState.STARTING: frozenset({OperabilityState.HEALTHY, OperabilityState.HALTED}),
    OperabilityState.HEALTHY: frozenset({OperabilityState.DEGRADED, OperabilityState.HALTING}),
    OperabilityState.DEGRADED: frozenset({OperabilityState.HEALTHY, OperabilityState.HALTING}),
    OperabilityState.HALTING: frozenset({OperabilityState.HALTED}),
    OperabilityState.HALTED: frozenset({OperabilityState.RECONCILING}),
    OperabilityState.RECONCILING: frozenset(
        {OperabilityState.RECOVERY_REVIEW, OperabilityState.HALTED}
    ),
    OperabilityState.RECOVERY_REVIEW: frozenset(
        {OperabilityState.STARTING, OperabilityState.HALTED}
    ),
}
ORDER_TRANSITIONS = {
    OrderState.PROPOSED: frozenset(
        {
            OrderState.EXPIRED,
            OrderState.INVALID,
            OrderState.RISK_REJECTED,
            OrderState.SIM_ACCEPTED,
        }
    ),
    OrderState.SIM_ACCEPTED: frozenset({OrderState.CREATED}),
    OrderState.CREATED: frozenset({OrderState.OPEN, OrderState.REJECTED}),
    OrderState.OPEN: frozenset(
        {
            OrderState.PARTIALLY_FILLED,
            OrderState.FILLED,
            OrderState.CANCEL_PENDING,
            OrderState.EXPIRED,
        }
    ),
    OrderState.PARTIALLY_FILLED: frozenset(
        {OrderState.FILLED, OrderState.CANCEL_PENDING, OrderState.EXIT_PENDING}
    ),
    OrderState.CANCEL_PENDING: frozenset({OrderState.CANCELLED, OrderState.FILLED}),
    OrderState.FILLED: frozenset({OrderState.EXIT_PENDING}),
    OrderState.EXIT_PENDING: frozenset({OrderState.CLOSED}),
}
LIVE_SESSION_TRANSITIONS = {
    LiveSessionState.SCHEDULED: frozenset({LiveSessionState.PREFLIGHT}),
    LiveSessionState.PREFLIGHT: frozenset({LiveSessionState.RUNNING, LiveSessionState.HALTED}),
    LiveSessionState.RUNNING: frozenset({LiveSessionState.DEGRADED, LiveSessionState.HALTING}),
    LiveSessionState.DEGRADED: frozenset({LiveSessionState.RUNNING, LiveSessionState.HALTING}),
    LiveSessionState.HALTING: frozenset({LiveSessionState.HALTED}),
    LiveSessionState.HALTED: frozenset({LiveSessionState.RECONCILING}),
    LiveSessionState.RECONCILING: frozenset({LiveSessionState.CLOSED, LiveSessionState.HALTED}),
}
WINDOW_TRANSITIONS = {
    WindowState.SCHEDULED: frozenset({WindowState.PREFLIGHT}),
    WindowState.PREFLIGHT: frozenset({WindowState.RUNNING, WindowState.HALTED}),
    WindowState.RUNNING: frozenset({WindowState.DEGRADED, WindowState.HALTING}),
    WindowState.DEGRADED: frozenset({WindowState.RUNNING, WindowState.HALTING}),
    WindowState.HALTING: frozenset({WindowState.HALTED}),
    WindowState.HALTED: frozenset({WindowState.RECONCILING}),
    WindowState.RECONCILING: frozenset({WindowState.CLOSED, WindowState.HALTED}),
}
RECONCILIATION_TRANSITIONS = {
    ReconciliationState.REQUESTED: frozenset({ReconciliationState.RUNNING}),
    ReconciliationState.RUNNING: frozenset(
        {
            ReconciliationState.MATCHED,
            ReconciliationState.MISMATCHED,
            ReconciliationState.FAILED,
        }
    ),
}
RECOVERY_TRANSITIONS = {
    RecoveryState.PENDING_RECONCILIATION: frozenset(
        {RecoveryState.PENDING_OWNER_REVIEW, RecoveryState.REJECTED}
    ),
    RecoveryState.PENDING_OWNER_REVIEW: frozenset({RecoveryState.APPROVED, RecoveryState.REJECTED}),
}
INCIDENT_TRANSITIONS = {
    IncidentState.OPEN: frozenset({IncidentState.ACKNOWLEDGED, IncidentState.MITIGATED}),
    IncidentState.ACKNOWLEDGED: frozenset({IncidentState.MITIGATED, IncidentState.RESOLVED}),
    IncidentState.MITIGATED: frozenset({IncidentState.RESOLVED}),
    IncidentState.RESOLVED: frozenset({IncidentState.REOPENED}),
    IncidentState.REOPENED: frozenset({IncidentState.ACKNOWLEDGED}),
}

StateT = TypeVar("StateT", bound=StrEnum)


@dataclass(frozen=True, slots=True)
class StateTransition:
    aggregate_id: str
    aggregate_type: str
    sequence: int
    from_state: str
    to_state: str
    occurred_at_ns: int
    actor: str
    reason: ReasonCode

    def __post_init__(self) -> None:
        if not self.aggregate_id or not self.aggregate_type or not self.actor:
            raise DomainError("transition identity, type, and actor are required")
        if self.sequence <= 0 or self.occurred_at_ns < 0:
            raise DomainError("transition sequence must be positive and time cannot be negative")


def transition(
    current: StateT,
    target: StateT,
    rules: dict[StateT, frozenset[StateT]],
    *,
    aggregate_id: str,
    sequence: int,
    occurred_at_ns: int,
    actor: str,
    reason: ReasonCode,
) -> StateTransition:
    if target not in rules.get(current, frozenset()):
        raise InvalidTransitionError(f"{current} -> {target}: {ReasonCode.INVALID_TRANSITION}")
    return StateTransition(
        aggregate_id=aggregate_id,
        aggregate_type=type(current).__name__,
        sequence=sequence,
        from_state=current.value,
        to_state=target.value,
        occurred_at_ns=occurred_at_ns,
        actor=actor,
        reason=reason,
    )


def fold_transitions(initial: str, history: tuple[StateTransition, ...]) -> str:
    state = initial
    for expected_sequence, item in enumerate(history, start=1):
        if item.sequence != expected_sequence or item.from_state != state:
            raise DomainError("transition history is not contiguous")
        state = item.to_state
    return state
