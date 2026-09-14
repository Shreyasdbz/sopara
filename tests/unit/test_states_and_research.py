from __future__ import annotations

from dataclasses import fields
from datetime import date
from enum import StrEnum

import pytest
from domain_helpers import DIGEST, version

from sopara.domain.canonical import canonical_sha256, stable_idempotency_key
from sopara.domain.errors import DomainError, InvalidTransitionError
from sopara.domain.evidence import (
    EntitlementSnapshot,
    EvidenceClass,
    EvidenceSelection,
    PermittedUse,
)
from sopara.domain.execution import SimulatedOrder
from sopara.domain.reason_codes import ReasonCode
from sopara.domain.research import (
    CounterfactualPlan,
    DatasetPackage,
    DateInterval,
    DecisionAuthority,
    DecisionRecord,
    ExperimentDefinition,
)
from sopara.domain.states import (
    DATASET_TRANSITIONS,
    EXPERIMENT_TRANSITIONS,
    INCIDENT_TRANSITIONS,
    LIVE_SESSION_TRANSITIONS,
    OPERABILITY_TRANSITIONS,
    ORDER_TRANSITIONS,
    RECONCILIATION_TRANSITIONS,
    RECOVERY_TRANSITIONS,
    STRATEGY_TRANSITIONS,
    WINDOW_TRANSITIONS,
    DatasetState,
    StateTransition,
    fold_transitions,
    transition,
)
from sopara.domain.versions import RuntimeManifest

ALL_RULES = (
    DATASET_TRANSITIONS,
    EXPERIMENT_TRANSITIONS,
    STRATEGY_TRANSITIONS,
    OPERABILITY_TRANSITIONS,
    ORDER_TRANSITIONS,
    LIVE_SESSION_TRANSITIONS,
    WINDOW_TRANSITIONS,
    RECONCILIATION_TRANSITIONS,
    RECOVERY_TRANSITIONS,
    INCIDENT_TRANSITIONS,
)


@pytest.mark.parametrize(
    ("current", "target", "rules"),
    [
        (current, target, rules)
        for rules in ALL_RULES
        for current, targets in rules.items()
        for target in targets
    ],
)
def test_every_declared_state_transition_is_legal(
    current: StrEnum, target: StrEnum, rules: dict[StrEnum, frozenset[StrEnum]]
) -> None:
    item = transition(
        current,
        target,
        rules,
        aggregate_id="aggregate-1",
        sequence=1,
        occurred_at_ns=1,
        actor="owner",
        reason=ReasonCode.INVALID_SCHEMA,
    )
    assert item.from_state != item.to_state


def test_undeclared_transition_is_rejected() -> None:
    with pytest.raises(InvalidTransitionError, match=ReasonCode.INVALID_TRANSITION):
        transition(
            DatasetState.DISCOVERED,
            DatasetState.READY,
            DATASET_TRANSITIONS,
            aggregate_id="dataset-1",
            sequence=1,
            occurred_at_ns=1,
            actor="owner",
            reason=ReasonCode.INVALID_SCHEMA,
        )


def test_ledger_fold_requires_contiguous_sequence_and_state() -> None:
    history = (
        StateTransition(
            "x", "DatasetState", 1, "DISCOVERED", "INGESTING", 1, "owner", ReasonCode.INVALID_SCHEMA
        ),
        StateTransition(
            "x", "DatasetState", 2, "INGESTING", "VALIDATING", 2, "owner", ReasonCode.INVALID_SCHEMA
        ),
    )
    assert fold_transitions("DISCOVERED", history) == "VALIDATING"
    with pytest.raises(DomainError, match="not contiguous"):
        fold_transitions("DISCOVERED", (history[1],))


def manifest() -> RuntimeManifest:
    return RuntimeManifest(
        "manifest-v1",
        version("dataset"),
        version("calendar"),
        version("feature"),
        version("strategy"),
        version("simulator"),
        version("cost"),
        version("risk"),
        (DIGEST,),
        7,
    )


def test_manifest_and_idempotency_keys_are_deterministic() -> None:
    assert manifest().fingerprint == manifest().fingerprint
    assert stable_idempotency_key("run", manifest()) == stable_idempotency_key("run", manifest())
    assert canonical_sha256(("a", "b")) != canonical_sha256(("b", "a"))


def test_experiment_rejects_leakage_and_has_immutable_identity() -> None:
    selection = EvidenceSelection(EvidenceClass.OBSERVED_NES, date(2026, 9, 1))
    counterfactuals = CounterfactualPlan(("NO_TRADE",), 1, 2)
    kwargs = {
        "experiment_id": "experiment-1",
        "parent_experiment_id": None,
        "hypothesis": "Observed NES net expectancy exceeds zero.",
        "rejection_criterion": "Reject when the confidence interval includes zero.",
        "manifest": manifest(),
        "evidence": selection,
        "training": DateInterval(date(2026, 9, 1), date(2026, 9, 5)),
        "validation": DateInterval(date(2026, 9, 6), date(2026, 9, 10)),
        "holdout": DateInterval(date(2026, 9, 11), date(2026, 9, 15)),
        "counterfactuals": counterfactuals,
    }
    experiment = ExperimentDefinition(**kwargs)  # type: ignore[arg-type]
    assert experiment.immutable_identity == ExperimentDefinition(**kwargs).immutable_identity  # type: ignore[arg-type]
    kwargs["holdout"] = DateInterval(date(2026, 9, 5), date(2026, 9, 15))
    with pytest.raises(DomainError, match="overlaps"):
        ExperimentDefinition(**kwargs)  # type: ignore[arg-type]


def test_model_and_counterfactual_authority_cannot_mutate_canonical_state() -> None:
    canonical = DecisionRecord("decision-1", DecisionAuthority.CANONICAL, None, None)
    model = DecisionRecord("model-1", DecisionAuthority.MODEL_REVIEW, "decision-1", None)
    alternate = DecisionRecord(
        "counterfactual-1", DecisionAuthority.COUNTERFACTUAL, "decision-1", "b" * 64
    )
    assert canonical.may_mutate_canonical_state
    assert not model.may_mutate_canonical_state
    assert not alternate.may_mutate_canonical_state


def test_entitlement_is_effective_period_and_use_specific() -> None:
    entitlement = EntitlementSnapshot(
        "entitlement-1",
        "source-1",
        "owner",
        date(2026, 9, 1),
        date(2026, 9, 30),
        frozenset({PermittedUse.HISTORICAL_REPLAY}),
    )
    assert entitlement.permits(PermittedUse.HISTORICAL_REPLAY, date(2026, 9, 15))
    assert not entitlement.permits(PermittedUse.EXPORT, date(2026, 9, 15))

    dataset = DatasetPackage(
        "dataset-1",
        "licensed-source",
        "source-contact",
        version("dataset"),
        DatasetState.READY,
        DateInterval(date(2026, 9, 1), date(2026, 9, 30)),
        entitlement,
    )
    assert dataset.state is DatasetState.READY


def test_simulated_order_cannot_express_a_broker_destination_or_real_order() -> None:
    names = {field.name for field in fields(SimulatedOrder)}
    assert "destination" not in names
    assert "broker" not in names
    assert "real_order" not in names
