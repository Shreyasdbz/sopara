from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from sopara.domain.events import DomainEvent
from sopara.domain.reason_codes import ReasonCode
from sopara.domain.states import DATASET_TRANSITIONS, DatasetState, transition

ROOT = Path(__file__).resolve().parents[2]


def load_object(path: Path) -> dict[str, object]:
    return cast("dict[str, object]", json.loads(path.read_text(encoding="utf-8")))


def test_reason_code_schema_matches_the_runtime_catalogue() -> None:
    schema = load_object(ROOT / "contracts" / "reason-codes" / "v1.schema.json")
    declared = cast("list[str]", schema["enum"])
    assert declared == sorted(code.value for code in ReasonCode)


def test_domain_event_serialization_matches_v1_fixture() -> None:
    item = transition(
        DatasetState.DISCOVERED,
        DatasetState.INGESTING,
        DATASET_TRANSITIONS,
        aggregate_id="dataset-1",
        sequence=1,
        occurred_at_ns=1,
        actor="owner",
        reason=ReasonCode.INVALID_SCHEMA,
    )
    event = DomainEvent.from_transition("event-1", item)
    fixture = load_object(ROOT / "fixtures" / "contracts" / "domain-event-v1.json")
    assert json.loads(event.to_json()) == fixture


def test_event_schema_requires_every_serialized_event_field() -> None:
    schema = load_object(ROOT / "contracts" / "events" / "v1.schema.json")
    required = cast("list[str]", schema["required"])
    fixture = load_object(ROOT / "fixtures" / "contracts" / "domain-event-v1.json")
    assert set(required) == set(fixture)
