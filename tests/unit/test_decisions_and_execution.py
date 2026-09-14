from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest
from domain_helpers import NES_CONTRACT, book, eligibility_context, proposal, version

from sopara.domain.decisions import StrategyEvaluation, StrategyOutcome, evaluate_eligibility
from sopara.domain.errors import DomainError
from sopara.domain.evidence import EvidenceClass, EvidenceSelection
from sopara.domain.execution import (
    FillAssumptions,
    PnlBreakdown,
    SimulatedFill,
    TickRounding,
    TriggerKind,
    align_nes_price,
    create_simulated_order,
    passive_fill_reason,
    resolve_gap_trigger,
)
from sopara.domain.instruments import Direction, Money, Price
from sopara.domain.market import StreamHealth
from sopara.domain.reason_codes import ReasonCode
from sopara.domain.states import OperabilityState, OrderState, ProposalState


def assumptions(*, requires_trade_through: bool = True) -> FillAssumptions:
    return FillAssumptions(
        simulator_version=version("simulator"),
        cost_model_version=version("cost"),
        latency_ns=1,
        queue_ahead=0,
        slippage_ticks=0,
        passive_requires_trade_through=requires_trade_through,
    )


def test_strategy_result_is_exactly_one_outcome() -> None:
    item = proposal()
    assert StrategyEvaluation(StrategyOutcome.PROPOSE, (), item).proposal is item
    with pytest.raises(DomainError, match="requires a proposal"):
        StrategyEvaluation(StrategyOutcome.PROPOSE, ())
    with pytest.raises(DomainError, match="only PROPOSE"):
        StrategyEvaluation(StrategyOutcome.NO_TRADE, (ReasonCode.OUTSIDE_APPROVED_WINDOW,), item)


def test_all_ordered_gates_pass_before_order_creation() -> None:
    item = proposal()
    decision = evaluate_eligibility(item, eligibility_context())
    assert decision.outcome is ProposalState.SIM_ACCEPTED
    assert [step.check for step in decision.steps] == [
        "current_fencing_lease",
        "operability",
        "entitlement",
        "approved_window",
        "explicit_contracts",
        "es_health",
        "nes_health",
        "nes_book",
        "evidence_class",
        "proposal_expiry",
        "nes_tick_grid",
        "duplicate_intent",
        "position_limit",
        "loss_limit",
        "exit_feasibility",
    ]
    order = create_simulated_order("order-1", item, NES_CONTRACT, decision)
    assert order.state is OrderState.CREATED


@pytest.mark.parametrize(
    ("overrides", "outcome", "reason"),
    [
        ({"lease_current": False}, ProposalState.INELIGIBLE, ReasonCode.LEASE_MISSING_OR_STALE),
        (
            {"operability": OperabilityState.DEGRADED},
            ProposalState.INELIGIBLE,
            ReasonCode.OPERABILITY_NOT_HEALTHY,
        ),
        (
            {"entitlement_permitted": None},
            ProposalState.INELIGIBLE,
            ReasonCode.MISSING_REQUIRED_STATE,
        ),
        ({"es_health": StreamHealth.GAPPED}, ProposalState.INELIGIBLE, ReasonCode.ES_SEQUENCE_GAP),
        ({"nes_health": StreamHealth.STALE}, ProposalState.INELIGIBLE, ReasonCode.NES_STALE),
        ({"duplicate_intent": True}, ProposalState.INELIGIBLE, ReasonCode.DUPLICATE_INTENT),
        ({"current_position": 1}, ProposalState.RISK_REJECTED, ReasonCode.POSITION_LIMIT),
        ({"within_loss_limit": False}, ProposalState.RISK_REJECTED, ReasonCode.LOSS_LIMIT),
        ({"exit_feasible": False}, ProposalState.RISK_REJECTED, ReasonCode.EXIT_INFEASIBLE),
    ],
)
def test_eligibility_fails_closed_at_first_failed_gate(
    overrides: dict[str, object], outcome: ProposalState, reason: ReasonCode
) -> None:
    decision = evaluate_eligibility(proposal(), eligibility_context(**overrides))
    assert decision.outcome is outcome
    assert decision.reason_codes[-1] is reason
    assert not decision.steps[-1].passed
    with pytest.raises(DomainError, match="before every eligibility"):
        create_simulated_order("order-1", proposal(), NES_CONTRACT, decision)


def test_es_valid_but_nes_off_grid_proposal_is_invalid() -> None:
    item = proposal(reference_price=Price.parse("6000.25"))
    decision = evaluate_eligibility(item, eligibility_context())
    assert decision.outcome is ProposalState.INVALID
    assert decision.reason_codes[-1] is ReasonCode.NES_OFF_GRID


def test_expired_proposal_is_not_an_order() -> None:
    decision = evaluate_eligibility(proposal(), eligibility_context(now_ns=201))
    assert decision.outcome is ProposalState.EXPIRED


def test_passive_touch_is_not_automatically_a_fill() -> None:
    reason = passive_fill_reason(
        book(),
        limit_price=Price.parse("6000.0"),
        touched=True,
        traded_through=False,
        assumptions=assumptions(),
    )
    assert reason is ReasonCode.PASSIVE_TOUCH_INSUFFICIENT
    assert (
        passive_fill_reason(
            book(),
            limit_price=Price.parse("6000.0"),
            touched=True,
            traded_through=True,
            assumptions=assumptions(),
        )
        is None
    )


def test_fill_carries_nes_evidence_and_versioned_assumptions() -> None:
    fill = SimulatedFill(
        "fill-1",
        "order-1",
        NES_CONTRACT,
        Price.parse("6000.0"),
        1,
        150,
        EvidenceSelection(EvidenceClass.OBSERVED_NES, date(2026, 9, 10)),
        assumptions(),
    )
    assert fill.contract is NES_CONTRACT
    with pytest.raises(DomainError, match="NES execution price"):
        replace(fill, price=Price.parse("6000.25"))


def test_off_grid_stop_or_target_alignment_is_explicit() -> None:
    down = align_nes_price(Price.parse("5999.75"), TickRounding.DOWN)
    up = align_nes_price(Price.parse("5999.75"), TickRounding.UP)
    assert down.aligned == Price.parse("5999.5")
    assert up.aligned == Price.parse("6000.0")
    assert down.transformed
    assert up.transformed


def test_gap_through_stop_uses_first_trade_but_target_remains_conservative() -> None:
    stop_fill = resolve_gap_trigger(
        direction=Direction.LONG,
        kind=TriggerKind.STOP,
        trigger=Price.parse("5999.5"),
        first_trade=Price.parse("5998.0"),
    )
    target_fill = resolve_gap_trigger(
        direction=Direction.LONG,
        kind=TriggerKind.TARGET,
        trigger=Price.parse("6001.0"),
        first_trade=Price.parse("6002.0"),
    )
    assert stop_fill == Price.parse("5998.0")
    assert target_fill == Price.parse("6001.0")


def test_pnl_keeps_cost_components_separate() -> None:
    pnl = PnlBreakdown(
        gross=Money.parse("1.00"),
        spread=Money.parse("0.25"),
        latency=Money.parse("0.05"),
        slippage=Money.parse("0.25"),
        exchange_fees=Money.parse("0.05"),
        broker_fees=Money.parse("0.05"),
        data_allocation=Money.parse("0.10"),
        inference_allocation=Money.parse("0.00"),
    )
    assert pnl.net == Money.parse("0.25")
    assert pnl.research_allocation == Money.parse("0.10")
