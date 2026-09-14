from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

from sopara.domain.canonical import canonical_sha256
from sopara.domain.decisions import EligibilityContext, Proposal, evaluate_eligibility
from sopara.domain.errors import DomainError
from sopara.domain.evidence import EvidenceClass, EvidenceSelection
from sopara.domain.execution import (
    FillAssumptions,
    PnlBreakdown,
    SimulatedFill,
    create_simulated_order,
)
from sopara.domain.instruments import ContractId, Direction, Money, Product
from sopara.domain.market import BookState, Completeness, SourceWatermark, StreamHealth
from sopara.domain.reason_codes import ReasonCode
from sopara.domain.states import OperabilityState
from sopara.domain.versions import VersionRef


class ReplayEventType(StrEnum):
    ES_TRADE = "ES_TRADE"
    NES_BOOK = "NES_BOOK"


@dataclass(frozen=True, slots=True)
class ReplayEvent:
    global_index: int
    event_type: ReplayEventType
    source_id: str
    channel_id: str
    source_session_id: str
    source_sequence: int
    message_index: int
    contract_id: str
    source_time_ns: int
    receive_time_ns: int
    price_ticks: int | None = None
    quantity: int | None = None
    bid_ticks: int | None = None
    ask_ticks: int | None = None
    bid_size: int | None = None
    ask_size: int | None = None
    last_trade_size: int | None = None

    def __post_init__(self) -> None:
        if (
            min(
                self.global_index,
                self.source_sequence,
                self.message_index,
                self.source_time_ns,
                self.receive_time_ns,
            )
            < 0
        ):
            raise DomainError("replay identity and time fields cannot be negative")
        if not all(
            item.strip()
            for item in (
                self.source_id,
                self.channel_id,
                self.source_session_id,
                self.contract_id,
            )
        ):
            raise DomainError("replay source identity is incomplete")
        if self.receive_time_ns < self.source_time_ns:
            raise DomainError("receive time cannot precede source time")
        if self.event_type is ReplayEventType.ES_TRADE:
            if self.price_ticks is None or self.quantity is None:
                raise DomainError("ES trades require price ticks and quantity")
            if self.price_ticks <= 0 or self.quantity <= 0:
                raise DomainError("ES trade values must be positive")
            if any(
                item is not None
                for item in (
                    self.bid_ticks,
                    self.ask_ticks,
                    self.bid_size,
                    self.ask_size,
                    self.last_trade_size,
                )
            ):
                raise DomainError("ES trades cannot contain NES book fields")
        else:
            book = (
                self.bid_ticks,
                self.ask_ticks,
                self.bid_size,
                self.ask_size,
                self.last_trade_size,
            )
            if any(item is None for item in book):
                raise DomainError("NES books require complete price and size fields")
            if self.price_ticks is not None or self.quantity is not None:
                raise DomainError("NES books cannot contain ES trade fields")
            if min(item for item in book if item is not None) <= 0:
                raise DomainError("NES book values must be positive")
            if (
                self.bid_ticks is not None
                and self.ask_ticks is not None
                and self.bid_ticks >= self.ask_ticks
            ):
                raise DomainError("NES book must be open and uncrossed")

    @property
    def instrument(self) -> Product:
        return Product.ES if self.event_type is ReplayEventType.ES_TRADE else Product.NES


@dataclass(frozen=True, slots=True)
class ReplayDataset:
    schema_version: str
    evidence_class: EvidenceClass
    trade_date: date
    es_contract: ContractId
    nes_contract: ContractId
    events: tuple[ReplayEvent, ...]

    def __post_init__(self) -> None:
        if self.schema_version != "sopara.synthetic-replay-fixture.v1":
            raise DomainError("unsupported replay fixture schema")
        if self.evidence_class is not EvidenceClass.SYNTHETIC_NES_PROXY:
            raise DomainError(ReasonCode.MIXED_EVIDENCE)
        if (
            self.es_contract.product is not Product.ES
            or self.nes_contract.product is not Product.NES
        ):
            raise DomainError("replay requires explicit ES and NES contracts")
        if not self.events:
            raise DomainError("replay fixture is incomplete")
        if tuple(item.global_index for item in self.events) != tuple(range(len(self.events))):
            raise DomainError("replay global indices must be contiguous and ordered")
        if tuple(item.source_time_ns for item in self.events) != tuple(
            sorted(item.source_time_ns for item in self.events)
        ):
            raise DomainError("replay events are out of time order")
        streams: dict[tuple[str, str, str, str], int] = {}
        instruments = {item.instrument for item in self.events}
        for item in self.events:
            expected_contract = (
                self.es_contract.canonical
                if item.instrument is Product.ES
                else self.nes_contract.canonical
            )
            if item.contract_id != expected_contract:
                raise DomainError("event contract does not match fixture contract")
            key = (
                item.source_id,
                item.channel_id,
                item.source_session_id,
                item.contract_id,
            )
            expected = streams.get(key, 0) + 1
            if item.source_sequence != expected:
                raise DomainError("source sequence is incomplete or out of order")
            streams[key] = expected
        if instruments != {Product.ES, Product.NES}:
            raise DomainError("replay fixture requires both ES and NES evidence")

    @property
    def input_hash(self) -> str:
        return canonical_sha256(self)


@dataclass(frozen=True, slots=True)
class ReplayConfiguration:
    threshold_es_ticks: int
    maximum_position: int
    holding_ns: int
    latency_ns: int
    queue_ahead: int
    slippage_ticks: int
    passive_requires_trade_through: bool
    spread_cost_microdollars: int
    latency_cost_microdollars: int
    slippage_cost_microdollars: int
    exchange_fee_microdollars: int
    broker_fee_microdollars: int
    data_allocation_microdollars: int
    inference_allocation_microdollars: int
    simulator_version: VersionRef
    cost_model_version: VersionRef

    def __post_init__(self) -> None:
        numeric = (
            self.threshold_es_ticks,
            self.maximum_position,
            self.holding_ns,
            self.latency_ns,
            self.queue_ahead,
            self.slippage_ticks,
            self.spread_cost_microdollars,
            self.latency_cost_microdollars,
            self.slippage_cost_microdollars,
            self.exchange_fee_microdollars,
            self.broker_fee_microdollars,
            self.data_allocation_microdollars,
            self.inference_allocation_microdollars,
        )
        if (
            min(numeric) < 0
            or min(self.threshold_es_ticks, self.maximum_position, self.holding_ns) == 0
        ):
            raise DomainError("replay configuration values are invalid")

    @property
    def fingerprint(self) -> str:
        return canonical_sha256(self)


@dataclass(frozen=True, slots=True)
class ReplayState:
    next_input_index: int
    logical_time_ns: int
    previous_es_ticks: int | None
    current_es_ticks: int | None
    nes_book: tuple[int, int, int, int, int] | None
    position: int
    entry_ticks: int | None
    decisions: tuple[dict[str, object], ...]
    orders: tuple[dict[str, object], ...]
    fills: tuple[dict[str, object], ...]
    positions: tuple[dict[str, object], ...]

    @classmethod
    def initial(cls) -> ReplayState:
        return cls(0, 0, None, None, None, 0, None, (), (), (), ())

    @property
    def fingerprint(self) -> str:
        return canonical_sha256(self)


@dataclass(frozen=True, slots=True)
class ReplayResult:
    schema_version: str
    dataset_hash: str
    configuration_hash: str
    runtime_manifest_hash: str
    evidence_class: EvidenceClass
    accepted_input_hashes: tuple[str, ...]
    decisions: tuple[dict[str, object], ...]
    orders: tuple[dict[str, object], ...]
    fills: tuple[dict[str, object], ...]
    positions: tuple[dict[str, object], ...]
    pnl: dict[str, str]
    counterfactuals: tuple[dict[str, object], ...]

    @property
    def fingerprint(self) -> str:
        return canonical_sha256(self)


def _watermark(item: ReplayEvent) -> SourceWatermark:
    return SourceWatermark(
        source_id=item.source_id,
        channel_id=item.channel_id,
        source_session_id=item.source_session_id,
        last_contiguous_sequence=item.source_sequence,
        max_exchange_time_ns=item.source_time_ns,
        max_receive_time_ns=item.receive_time_ns,
        completeness=Completeness.CONTIGUOUS,
    )


def _money(microdollars: int) -> Money:
    return Money(Decimal(microdollars) / Decimal(1_000_000))


def advance_replay(
    dataset: ReplayDataset,
    configuration: ReplayConfiguration,
    state: ReplayState,
) -> ReplayState:
    if state.next_input_index >= len(dataset.events):
        raise DomainError("replay is already at the end of its input")
    event = dataset.events[state.next_input_index]
    decisions = list(state.decisions)
    orders = list(state.orders)
    fills = list(state.fills)
    positions = list(state.positions)
    previous_es = state.previous_es_ticks
    current_es = state.current_es_ticks
    book_tuple = state.nes_book
    position = state.position
    entry_ticks = state.entry_ticks

    if event.event_type is ReplayEventType.NES_BOOK:
        bid_ticks = event.bid_ticks
        ask_ticks = event.ask_ticks
        bid_size = event.bid_size
        ask_size = event.ask_size
        last_trade_size = event.last_trade_size
        if (
            bid_ticks is None
            or ask_ticks is None
            or bid_size is None
            or ask_size is None
            or last_trade_size is None
        ):
            raise DomainError("validated NES event lost required book fields")
        book_tuple = (
            bid_ticks,
            ask_ticks,
            bid_size,
            ask_size,
            last_trade_size,
        )
    else:
        if event.price_ticks is None:
            raise DomainError("validated ES event lost its price")
        previous_es, current_es = current_es, event.price_ticks
        reason_codes: tuple[ReasonCode, ...] = ()
        outcome = "NO_TRADE"
        proposal_id: str | None = None
        direction: Direction | None = None
        delta = None if previous_es is None else current_es - previous_es
        if delta is None or book_tuple is None:
            reason_codes = (ReasonCode.MISSING_REQUIRED_STATE,)
        elif position != 0:
            reason_codes = (ReasonCode.POSITION_LIMIT,)
        elif abs(delta) < configuration.threshold_es_ticks:
            reason_codes = (ReasonCode.SIGNAL_BELOW_THRESHOLD,)
        else:
            direction = Direction.LONG if delta > 0 else Direction.SHORT
            bid_ticks, ask_ticks, bid_size, ask_size, last_trade_size = book_tuple
            reference_ticks = ask_ticks if direction is Direction.LONG else bid_ticks
            feature_id = canonical_sha256(
                {
                    "event": event.global_index,
                    "delta_es_ticks": delta,
                    "as_of_ns": event.source_time_ns,
                }
            )
            proposal_id = canonical_sha256(
                {"feature": feature_id, "direction": direction.value, "quantity": 1}
            )
            evidence = EvidenceSelection(
                EvidenceClass.SYNTHETIC_NES_PROXY,
                dataset.trade_date,
                proxy_opt_in=True,
            )
            es_mark = _watermark(event)
            nes_event = next(
                item
                for item in reversed(dataset.events[: event.global_index])
                if item.event_type is ReplayEventType.NES_BOOK
            )
            nes_mark = _watermark(nes_event)
            proposal = Proposal(
                proposal_id=proposal_id,
                direction=direction,
                quantity=1,
                reference_price=dataset.nes_contract.spec.price_from_ticks(reference_ticks),
                valid_from_ns=event.source_time_ns,
                valid_through_ns=event.source_time_ns + configuration.holding_ns,
                stop_policy="SESSION_END",
                exit_policy="TERMINAL_BOOK",
                maximum_holding_ns=configuration.holding_ns,
                feature_snapshot_id=feature_id,
                es_watermark=es_mark,
                nes_watermark=nes_mark,
                evidence=evidence,
            )
            book = BookState(
                contract=dataset.nes_contract,
                bid=dataset.nes_contract.spec.price_from_ticks(bid_ticks),
                ask=dataset.nes_contract.spec.price_from_ticks(ask_ticks),
                bid_size=bid_size,
                ask_size=ask_size,
                last_trade_size=last_trade_size,
            )
            eligibility = evaluate_eligibility(
                proposal,
                EligibilityContext(
                    lease_current=True,
                    operability=OperabilityState.HEALTHY,
                    entitlement_permitted=True,
                    within_approved_window=True,
                    source_contract=dataset.es_contract,
                    execution_contract=dataset.nes_contract,
                    es_health=StreamHealth.HEALTHY,
                    nes_health=StreamHealth.HEALTHY,
                    nes_book=book,
                    evidence_class_permitted=True,
                    now_ns=event.source_time_ns,
                    duplicate_intent=False,
                    current_position=position,
                    maximum_position=configuration.maximum_position,
                    within_loss_limit=True,
                    exit_feasible=True,
                ),
            )
            order_id = canonical_sha256({"proposal_id": proposal_id, "kind": "ENTRY"})
            order = create_simulated_order(order_id, proposal, dataset.nes_contract, eligibility)
            assumptions = FillAssumptions(
                simulator_version=configuration.simulator_version,
                cost_model_version=configuration.cost_model_version,
                latency_ns=configuration.latency_ns,
                queue_ahead=configuration.queue_ahead,
                slippage_ticks=configuration.slippage_ticks,
                passive_requires_trade_through=configuration.passive_requires_trade_through,
            )
            fill_id = canonical_sha256({"order_id": order_id, "kind": "ENTRY"})
            fill = SimulatedFill(
                fill_id=fill_id,
                order_id=order_id,
                contract=dataset.nes_contract,
                price=order.limit_price,
                quantity=1,
                occurred_at_ns=event.source_time_ns + configuration.latency_ns,
                evidence=evidence,
                assumptions=assumptions,
            )
            outcome = "PROPOSE"
            reason_codes = eligibility.reason_codes
            position = direction.sign
            entry_ticks = reference_ticks
            orders.append(
                {
                    "order_id": order.order_id,
                    "proposal_id": proposal_id,
                    "direction": direction.value,
                    "quantity": 1,
                    "limit_ticks": reference_ticks,
                }
            )
            fills.append(
                {
                    "fill_id": fill.fill_id,
                    "order_id": order_id,
                    "kind": "ENTRY",
                    "price_ticks": reference_ticks,
                    "quantity": 1,
                    "occurred_at_ns": fill.occurred_at_ns,
                    "evidence_class": evidence.evidence_class.value,
                    "assumptions": {
                        "simulator_version": configuration.simulator_version.version,
                        "simulator_hash": configuration.simulator_version.sha256,
                        "cost_model_version": configuration.cost_model_version.version,
                        "cost_model_hash": configuration.cost_model_version.sha256,
                        "latency_ns": configuration.latency_ns,
                        "queue_ahead": configuration.queue_ahead,
                        "slippage_ticks": configuration.slippage_ticks,
                        "passive_requires_trade_through": (
                            configuration.passive_requires_trade_through
                        ),
                    },
                }
            )
            positions.append(
                {
                    "after_event": event.global_index,
                    "quantity": position,
                    "price_ticks": entry_ticks,
                }
            )
        decisions.append(
            {
                "decision_id": canonical_sha256(
                    {
                        "dataset": dataset.input_hash,
                        "event": event.global_index,
                        "authority": "CANONICAL",
                    }
                ),
                "event_index": event.global_index,
                "outcome": outcome,
                "proposal_id": proposal_id,
                "direction": None if direction is None else direction.value,
                "feature": {"delta_es_ticks": delta, "as_of_ns": event.source_time_ns},
                "reason_codes": [item.value for item in reason_codes],
            }
        )

    return ReplayState(
        next_input_index=event.global_index + 1,
        logical_time_ns=event.source_time_ns,
        previous_es_ticks=previous_es,
        current_es_ticks=current_es,
        nes_book=book_tuple,
        position=position,
        entry_ticks=entry_ticks,
        decisions=tuple(decisions),
        orders=tuple(orders),
        fills=tuple(fills),
        positions=tuple(positions),
    )


def finish_replay(
    dataset: ReplayDataset,
    configuration: ReplayConfiguration,
    runtime_manifest_hash: str,
    state: ReplayState,
    accepted_input_hashes: tuple[str, ...],
) -> ReplayResult:
    if state.next_input_index != len(dataset.events):
        raise DomainError("replay cannot finish before all bounded input is consumed")
    orders = list(state.orders)
    fills = list(state.fills)
    positions = list(state.positions)
    gross = Money.parse(0)
    if state.position != 0:
        if state.nes_book is None or state.entry_ticks is None:
            raise DomainError("open replay position has no terminal book or entry")
        bid_ticks, ask_ticks, _, _, _ = state.nes_book
        exit_ticks = bid_ticks if state.position > 0 else ask_ticks
        direction = Direction.LONG if state.position > 0 else Direction.SHORT
        exit_order_id = canonical_sha256({"dataset": dataset.input_hash, "kind": "TERMINAL_EXIT"})
        orders.append(
            {
                "order_id": exit_order_id,
                "proposal_id": state.orders[-1]["proposal_id"],
                "direction": (
                    Direction.SHORT.value if direction is Direction.LONG else Direction.LONG.value
                ),
                "quantity": abs(state.position),
                "limit_ticks": exit_ticks,
                "kind": "TERMINAL_EXIT",
            }
        )
        fills.append(
            {
                "fill_id": canonical_sha256({"order_id": exit_order_id, "kind": "EXIT"}),
                "order_id": exit_order_id,
                "kind": "EXIT",
                "price_ticks": exit_ticks,
                "quantity": abs(state.position),
                "occurred_at_ns": state.logical_time_ns + configuration.latency_ns,
                "evidence_class": dataset.evidence_class.value,
                "assumptions": {
                    "simulator_version": configuration.simulator_version.version,
                    "simulator_hash": configuration.simulator_version.sha256,
                    "cost_model_version": configuration.cost_model_version.version,
                    "cost_model_hash": configuration.cost_model_version.sha256,
                    "latency_ns": configuration.latency_ns,
                    "queue_ahead": configuration.queue_ahead,
                    "slippage_ticks": configuration.slippage_ticks,
                    "passive_requires_trade_through": (
                        configuration.passive_requires_trade_through
                    ),
                },
            }
        )
        positions.append({"after_event": len(dataset.events), "quantity": 0, "price_ticks": None})
        gross = dataset.nes_contract.spec.gross_pnl(
            dataset.nes_contract.spec.price_from_ticks(state.entry_ticks),
            dataset.nes_contract.spec.price_from_ticks(exit_ticks),
            direction,
            abs(state.position),
        )
    trade_count = len(fills) // 2
    pnl = PnlBreakdown(
        gross=gross,
        spread=_money(configuration.spread_cost_microdollars * trade_count),
        latency=_money(configuration.latency_cost_microdollars * trade_count),
        slippage=_money(configuration.slippage_cost_microdollars * trade_count),
        exchange_fees=_money(configuration.exchange_fee_microdollars * len(fills)),
        broker_fees=_money(configuration.broker_fee_microdollars * len(fills)),
        data_allocation=_money(configuration.data_allocation_microdollars),
        inference_allocation=_money(configuration.inference_allocation_microdollars),
    )
    pnl_payload = {
        "gross": format(pnl.gross.value, "f"),
        "spread": format(pnl.spread.value, "f"),
        "latency": format(pnl.latency.value, "f"),
        "slippage": format(pnl.slippage.value, "f"),
        "exchange_fees": format(pnl.exchange_fees.value, "f"),
        "broker_fees": format(pnl.broker_fees.value, "f"),
        "allocated_research_cost": format(pnl.research_allocation.value, "f"),
        "data_allocation": format(pnl.data_allocation.value, "f"),
        "inference_allocation": format(pnl.inference_allocation.value, "f"),
        "net": format(pnl.net.value, "f"),
    }
    return ReplayResult(
        schema_version="sopara.replay-result.v1",
        dataset_hash=dataset.input_hash,
        configuration_hash=configuration.fingerprint,
        runtime_manifest_hash=runtime_manifest_hash,
        evidence_class=dataset.evidence_class,
        accepted_input_hashes=accepted_input_hashes,
        decisions=state.decisions,
        orders=tuple(orders),
        fills=tuple(fills),
        positions=tuple(positions),
        pnl=pnl_payload,
        counterfactuals=(
            {
                "name": "NO_TRADE",
                "gross": "0",
                "net": "0",
                "decision_count": len(state.decisions),
                "fill_count": 0,
            },
        ),
    )
