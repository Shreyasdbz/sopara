from __future__ import annotations

from datetime import date

from sopara.domain.decisions import EligibilityContext, Proposal
from sopara.domain.evidence import EvidenceClass, EvidenceSelection
from sopara.domain.instruments import ContractId, Direction, Price, Product
from sopara.domain.market import BookState, Completeness, SourceWatermark, StreamHealth
from sopara.domain.states import OperabilityState
from sopara.domain.versions import VersionRef

DIGEST = "a" * 64
ES_CONTRACT = ContractId(Product.ES, date(2026, 9, 18))
NES_CONTRACT = ContractId(Product.NES, date(2026, 9, 18))


def version(component: str, digest: str = DIGEST) -> VersionRef:
    return VersionRef(component, "1.0.0", digest)


def watermark(source: str) -> SourceWatermark:
    return SourceWatermark(source, "prices", "session-1", 10, 100, 101, Completeness.CONTIGUOUS)


def book(**overrides: object) -> BookState:
    values: dict[str, object] = {
        "contract": NES_CONTRACT,
        "bid": Price.parse("5999.5"),
        "ask": Price.parse("6000.0"),
        "bid_size": 5,
        "ask_size": 5,
        "last_trade_size": 1,
    }
    values.update(overrides)
    return BookState(**values)  # type: ignore[arg-type]


def proposal(**overrides: object) -> Proposal:
    values: dict[str, object] = {
        "proposal_id": "proposal-1",
        "direction": Direction.LONG,
        "quantity": 1,
        "reference_price": Price.parse("6000.0"),
        "valid_from_ns": 100,
        "valid_through_ns": 200,
        "stop_policy": "stop-v1",
        "exit_policy": "exit-v1",
        "maximum_holding_ns": 50,
        "feature_snapshot_id": "feature-1",
        "es_watermark": watermark("es-source"),
        "nes_watermark": watermark("nes-source"),
        "evidence": EvidenceSelection(EvidenceClass.OBSERVED_NES, date(2026, 9, 10)),
    }
    values.update(overrides)
    return Proposal(**values)  # type: ignore[arg-type]


def eligibility_context(**overrides: object) -> EligibilityContext:
    values: dict[str, object] = {
        "lease_current": True,
        "operability": OperabilityState.HEALTHY,
        "entitlement_permitted": True,
        "within_approved_window": True,
        "source_contract": ES_CONTRACT,
        "execution_contract": NES_CONTRACT,
        "es_health": StreamHealth.HEALTHY,
        "nes_health": StreamHealth.HEALTHY,
        "nes_book": book(),
        "evidence_class_permitted": True,
        "now_ns": 150,
        "duplicate_intent": False,
        "current_position": 0,
        "maximum_position": 1,
        "within_loss_limit": True,
        "exit_feasible": True,
    }
    values.update(overrides)
    return EligibilityContext(**values)  # type: ignore[arg-type]
