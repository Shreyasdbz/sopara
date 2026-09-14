from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR
from enum import StrEnum

from sopara.domain.decisions import EligibilityDecision, Proposal
from sopara.domain.errors import DomainError
from sopara.domain.evidence import EvidenceSelection
from sopara.domain.instruments import ContractId, Direction, Money, Price, Product
from sopara.domain.market import BookState
from sopara.domain.reason_codes import ReasonCode
from sopara.domain.states import OrderState, ProposalState
from sopara.domain.versions import VersionRef


class TickRounding(StrEnum):
    DOWN = "DOWN"
    UP = "UP"


class TriggerKind(StrEnum):
    STOP = "STOP"
    TARGET = "TARGET"


@dataclass(frozen=True, slots=True)
class PriceAlignment:
    requested: Price
    aligned: Price
    rounding: TickRounding

    @property
    def transformed(self) -> bool:
        return self.requested != self.aligned


def align_nes_price(price: Price, rounding: TickRounding) -> PriceAlignment:
    quotient = price.value / NES_CONTRACT_TICK
    mode = ROUND_FLOOR if rounding is TickRounding.DOWN else ROUND_CEILING
    ticks = int(quotient.to_integral_value(rounding=mode))
    aligned = Price(NES_CONTRACT_TICK * ticks)
    return PriceAlignment(price, aligned, rounding)


NES_CONTRACT_TICK = Price.parse("0.5").value


def resolve_gap_trigger(
    *, direction: Direction, kind: TriggerKind, trigger: Price, first_trade: Price
) -> Price:
    if kind is TriggerKind.STOP:
        crossed = (
            first_trade.value <= trigger.value
            if direction is Direction.LONG
            else first_trade.value >= trigger.value
        )
        if not crossed:
            raise DomainError("first trade did not cross the stop")
        return first_trade
    reached = (
        first_trade.value >= trigger.value
        if direction is Direction.LONG
        else first_trade.value <= trigger.value
    )
    if not reached:
        raise DomainError("first trade did not reach the target")
    return trigger


@dataclass(frozen=True, slots=True)
class SimulatedOrder:
    order_id: str
    proposal_id: str
    contract: ContractId
    direction: Direction
    quantity: int
    limit_price: Price
    state: OrderState

    def __post_init__(self) -> None:
        if self.contract.product is not Product.NES:
            raise DomainError("simulated execution orders require an NES contract")
        if self.quantity <= 0 or not self.contract.spec.is_on_grid(self.limit_price):
            raise DomainError("simulated order quantity and NES price must be valid")


def create_simulated_order(
    order_id: str,
    proposal: Proposal,
    execution_contract: ContractId,
    decision: EligibilityDecision,
) -> SimulatedOrder:
    if decision.outcome is not ProposalState.SIM_ACCEPTED:
        raise DomainError(
            "an order cannot be created before every eligibility and risk gate passes"
        )
    return SimulatedOrder(
        order_id=order_id,
        proposal_id=proposal.proposal_id,
        contract=execution_contract,
        direction=proposal.direction,
        quantity=proposal.quantity,
        limit_price=proposal.reference_price,
        state=OrderState.CREATED,
    )


@dataclass(frozen=True, slots=True)
class FillAssumptions:
    simulator_version: VersionRef
    cost_model_version: VersionRef
    latency_ns: int
    queue_ahead: int
    slippage_ticks: int
    passive_requires_trade_through: bool

    def __post_init__(self) -> None:
        if min(self.latency_ns, self.queue_ahead, self.slippage_ticks) < 0:
            raise DomainError("fill assumptions cannot be negative")


@dataclass(frozen=True, slots=True)
class SimulatedFill:
    fill_id: str
    order_id: str
    contract: ContractId
    price: Price
    quantity: int
    occurred_at_ns: int
    evidence: EvidenceSelection
    assumptions: FillAssumptions

    def __post_init__(self) -> None:
        if self.contract.product is not Product.NES or not self.contract.spec.is_on_grid(
            self.price
        ):
            raise DomainError("fills require an on-grid NES execution price")
        if self.quantity <= 0 or self.occurred_at_ns < 0:
            raise DomainError("fill quantity and time are invalid")


def passive_fill_reason(
    book: BookState,
    *,
    limit_price: Price,
    touched: bool,
    traded_through: bool,
    assumptions: FillAssumptions,
) -> ReasonCode | None:
    blocked = book.blocking_reason()
    if blocked is not None:
        return blocked
    if not book.contract.spec.is_on_grid(limit_price):
        return ReasonCode.NES_OFF_GRID
    if not touched:
        return ReasonCode.PASSIVE_TOUCH_INSUFFICIENT
    if assumptions.passive_requires_trade_through and not traded_through:
        return ReasonCode.PASSIVE_TOUCH_INSUFFICIENT
    return None


@dataclass(frozen=True, slots=True)
class Position:
    contract: ContractId
    quantity: int
    average_price: Price | None

    def __post_init__(self) -> None:
        if self.contract.product is not Product.NES:
            raise DomainError("positions require an NES contract")
        if self.quantity == 0 and self.average_price is not None:
            raise DomainError("flat positions cannot retain an average price")
        if self.quantity != 0 and self.average_price is None:
            raise DomainError("open positions require an average price")


@dataclass(frozen=True, slots=True)
class PnlBreakdown:
    gross: Money
    spread: Money
    latency: Money
    slippage: Money
    exchange_fees: Money
    broker_fees: Money
    data_allocation: Money
    inference_allocation: Money

    def __post_init__(self) -> None:
        costs = (
            self.spread,
            self.latency,
            self.slippage,
            self.exchange_fees,
            self.broker_fees,
            self.data_allocation,
            self.inference_allocation,
        )
        if any(cost.value < 0 for cost in costs):
            raise DomainError("P&L cost components cannot be negative")

    @property
    def net(self) -> Money:
        costs = (
            self.spread
            + self.latency
            + self.slippage
            + self.exchange_fees
            + self.broker_fees
            + self.data_allocation
            + self.inference_allocation
        )
        return self.gross - costs

    @property
    def research_allocation(self) -> Money:
        return self.data_allocation + self.inference_allocation
