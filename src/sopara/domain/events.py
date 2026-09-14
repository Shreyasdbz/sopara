from __future__ import annotations

from dataclasses import dataclass

from sopara.domain.canonical import canonical_json
from sopara.domain.errors import DomainError
from sopara.domain.reason_codes import ReasonCode
from sopara.domain.states import StateTransition

DOMAIN_EVENT_SCHEMA_VERSION = "sopara.domain-event.v1"


@dataclass(frozen=True, slots=True)
class DomainEvent:
    schema_version: str
    event_id: str
    aggregate_id: str
    aggregate_type: str
    sequence: int
    event_type: str
    occurred_at_ns: int
    actor: str
    reason_codes: tuple[ReasonCode, ...]
    payload: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        if self.schema_version != DOMAIN_EVENT_SCHEMA_VERSION:
            raise DomainError("unsupported domain event schema")
        if not self.event_id or not self.aggregate_id or not self.event_type or not self.actor:
            raise DomainError("domain event identity and attribution are required")
        if self.sequence <= 0 or self.occurred_at_ns < 0:
            raise DomainError("event sequence must be positive and time cannot be negative")
        keys = [key for key, _ in self.payload]
        if keys != sorted(keys) or len(keys) != len(set(keys)):
            raise DomainError("event payload keys must be unique and sorted")

    @classmethod
    def from_transition(cls, event_id: str, item: StateTransition) -> DomainEvent:
        return cls(
            schema_version=DOMAIN_EVENT_SCHEMA_VERSION,
            event_id=event_id,
            aggregate_id=item.aggregate_id,
            aggregate_type=item.aggregate_type,
            sequence=item.sequence,
            event_type=f"{item.aggregate_type}.state_changed",
            occurred_at_ns=item.occurred_at_ns,
            actor=item.actor,
            reason_codes=(item.reason,),
            payload=(("from_state", item.from_state), ("to_state", item.to_state)),
        )

    def to_json(self) -> str:
        return canonical_json(self)
