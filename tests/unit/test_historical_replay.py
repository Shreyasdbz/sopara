from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest

from sopara.adapters.replay.serialization import parse_fixture, read_fixture
from sopara.application.historical import default_configuration
from sopara.domain.errors import DomainError
from sopara.domain.replay import ReplayState, advance_replay, finish_replay

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "fixtures" / "replay" / "golden-v1" / "manifest.json"


def fixture_payload() -> dict[str, object]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_golden_replay_has_deterministic_abstention_trade_costs_and_baseline() -> None:
    dataset = read_fixture(FIXTURE)
    configuration = default_configuration()
    state = ReplayState.initial()
    for _ in dataset.events:
        state = advance_replay(dataset, configuration, state)
    first = finish_replay(dataset, configuration, "a" * 64, state, ("b" * 64, "c" * 64))

    repeated = ReplayState.initial()
    for _ in dataset.events:
        repeated = advance_replay(dataset, configuration, repeated)
    second = finish_replay(dataset, configuration, "a" * 64, repeated, ("b" * 64, "c" * 64))

    assert first == second
    assert first.fingerprint == second.fingerprint
    assert [item["outcome"] for item in first.decisions] == [
        "NO_TRADE",
        "PROPOSE",
        "NO_TRADE",
    ]
    assert [item["reason_codes"] for item in first.decisions] == [
        ["MISSING_REQUIRED_STATE"],
        [],
        ["POSITION_LIMIT"],
    ]
    assert first.pnl == {
        "gross": "0.750",
        "spread": "0.125",
        "latency": "0.025",
        "slippage": "0",
        "exchange_fees": "0.04",
        "broker_fees": "0.02",
        "allocated_research_cost": "0.05",
        "data_allocation": "0.05",
        "inference_allocation": "0",
        "net": "0.490",
    }
    assert first.counterfactuals[0]["name"] == "NO_TRADE"
    assert first.counterfactuals[0]["net"] == "0"


@pytest.mark.parametrize(
    "mutation",
    ["incomplete", "off_grid", "out_of_order", "mixed_evidence", "sequence_gap"],
)
def test_invalid_fixture_classes_fail_closed(mutation: str) -> None:
    payload = fixture_payload()
    events = payload["events"]
    assert isinstance(events, list)
    first = cast("dict[str, object]", events[0])
    second = cast("dict[str, object]", events[1])
    assert isinstance(first, dict)
    assert isinstance(second, dict)
    if mutation == "incomplete":
        first.pop("receive_time_ns")
    elif mutation == "off_grid":
        second["bid_ticks"] = 12000.5
    elif mutation == "out_of_order":
        second["source_time_ns"] = 1
    elif mutation == "mixed_evidence":
        second["evidence_class"] = "OBSERVED_NES"
    else:
        first["source_sequence"] = 2
    with pytest.raises((DomainError, TypeError, ValueError)):
        parse_fixture(json.dumps(payload).encode())


def test_corrupt_fixture_fails_closed() -> None:
    with pytest.raises(DomainError, match="valid UTF-8 JSON"):
        parse_fixture(b"{not-json")
