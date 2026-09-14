from __future__ import annotations

import re
from dataclasses import dataclass, replace
from enum import StrEnum

from sopara.domain.errors import DomainError
from sopara.domain.instruments import ContractId, Price, Product
from sopara.domain.reason_codes import ReasonCode


class Completeness(StrEnum):
    CONTIGUOUS = "CONTIGUOUS"
    GAPPED = "GAPPED"
    UNVERIFIED = "UNVERIFIED"


class DuplicateDisposition(StrEnum):
    NEW = "NEW"
    IDENTICAL = "IDENTICAL"
    CONFLICT = "CONFLICT"


class StreamHealth(StrEnum):
    HEALTHY = "HEALTHY"
    STALE = "STALE"
    GAPPED = "GAPPED"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class MarketEventIdentity:
    source_id: str
    channel_id: str
    source_session_id: str
    source_sequence: int
    message_index: int
    contract_id: str

    def __post_init__(self) -> None:
        if not self.source_id or not self.channel_id or not self.source_session_id:
            raise DomainError("complete source identity is required")
        if self.source_sequence < 0 or self.message_index < 0:
            raise DomainError("source sequence and message index cannot be negative")
        if not self.contract_id:
            raise DomainError("explicit contract identity is required")

    @property
    def stream_key(self) -> tuple[str, str, str, str]:
        return (self.source_id, self.channel_id, self.source_session_id, self.contract_id)

    def compare_within_stream(self, other: MarketEventIdentity) -> int:
        if self.stream_key != other.stream_key:
            raise DomainError("independent streams cannot be ordered against each other")
        left = (self.source_sequence, self.message_index)
        right = (other.source_sequence, other.message_index)
        return (left > right) - (left < right)


@dataclass(frozen=True, slots=True)
class SourceEvent:
    identity: MarketEventIdentity
    payload_sha256: str
    exchange_time_ns: int
    receive_time_ns: int | None
    ingestion_time_ns: int
    corrected: bool

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[0-9a-f]{64}", self.payload_sha256):
            raise DomainError("source event payload requires a SHA-256 digest")
        if min(self.exchange_time_ns, self.ingestion_time_ns) < 0:
            raise DomainError("event timestamps cannot be negative")
        if self.receive_time_ns is not None and self.receive_time_ns < 0:
            raise DomainError("receive timestamp cannot be negative")


def classify_duplicate(existing: SourceEvent | None, incoming: SourceEvent) -> DuplicateDisposition:
    if existing is None or existing.identity != incoming.identity:
        return DuplicateDisposition.NEW
    if existing.payload_sha256 == incoming.payload_sha256:
        return DuplicateDisposition.IDENTICAL
    return DuplicateDisposition.CONFLICT


@dataclass(frozen=True, slots=True)
class SourceWatermark:
    source_id: str
    channel_id: str
    source_session_id: str
    last_contiguous_sequence: int
    max_exchange_time_ns: int
    max_receive_time_ns: int | None
    completeness: Completeness

    def __post_init__(self) -> None:
        if self.last_contiguous_sequence < 0 or self.max_exchange_time_ns < 0:
            raise DomainError("watermark values cannot be negative")

    def advance(self, event: SourceEvent) -> SourceWatermark:
        identity = event.identity
        if (identity.source_id, identity.channel_id, identity.source_session_id) != (
            self.source_id,
            self.channel_id,
            self.source_session_id,
        ):
            raise DomainError("watermarks cannot total-order independent streams")
        if identity.source_sequence <= self.last_contiguous_sequence:
            return self
        contiguous = identity.source_sequence == self.last_contiguous_sequence + 1
        receive = self.max_receive_time_ns
        if event.receive_time_ns is not None:
            receive = max(receive or 0, event.receive_time_ns)
        return replace(
            self,
            last_contiguous_sequence=(
                identity.source_sequence if contiguous else self.last_contiguous_sequence
            ),
            max_exchange_time_ns=max(self.max_exchange_time_ns, event.exchange_time_ns),
            max_receive_time_ns=receive,
            completeness=Completeness.CONTIGUOUS if contiguous else Completeness.GAPPED,
        )


@dataclass(frozen=True, slots=True)
class BookState:
    contract: ContractId
    bid: Price | None
    ask: Price | None
    bid_size: int
    ask_size: int
    last_trade_size: int
    stale: bool = False
    halted: bool = False
    price_limited: bool = False

    def blocking_reason(self) -> ReasonCode | None:
        if self.contract.product is not Product.NES:
            raise DomainError("execution book must be an NES contract")
        if self.stale:
            return ReasonCode.NES_STALE
        if self.halted:
            return ReasonCode.MARKET_HALTED
        if self.price_limited:
            return ReasonCode.PRICE_LIMITED
        if self.bid is None or self.ask is None or self.bid_size <= 0 or self.ask_size <= 0:
            return ReasonCode.NES_BOOK_EMPTY
        if self.bid.value > self.ask.value:
            return ReasonCode.NES_BOOK_CROSSED
        if self.bid.value == self.ask.value:
            return ReasonCode.NES_BOOK_LOCKED
        if self.last_trade_size <= 0:
            return ReasonCode.ZERO_VOLUME
        if not self.contract.spec.is_on_grid(self.bid) or not self.contract.spec.is_on_grid(
            self.ask
        ):
            return ReasonCode.NES_OFF_GRID
        return None
