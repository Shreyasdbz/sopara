from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from sopara.domain.canonical import stable_idempotency_key
from sopara.domain.errors import DomainError
from sopara.domain.instruments import NES, Direction, Money
from sopara.domain.reason_codes import ReasonCode
from sopara.domain.states import StateTransition, fold_transitions


@given(st.integers(min_value=1, max_value=1_000_000))
def test_nes_tick_price_round_trips(ticks: int) -> None:
    price = NES.price_from_ticks(ticks)
    assert NES.ticks(price) == ticks


@given(
    st.integers(min_value=1, max_value=1_000_000),
    st.integers(min_value=1, max_value=10_000),
    st.integers(min_value=1, max_value=100),
)
def test_long_and_short_pnl_are_exact_opposites(
    entry_ticks: int, movement_ticks: int, quantity: int
) -> None:
    entry = NES.price_from_ticks(entry_ticks)
    exit_price = NES.price_from_ticks(entry_ticks + movement_ticks)
    long = NES.gross_pnl(entry, exit_price, Direction.LONG, quantity)
    short = NES.gross_pnl(entry, exit_price, Direction.SHORT, quantity)
    assert long + short == Money.parse("0")


@given(st.text(min_size=1), st.lists(st.integers(), max_size=10))
def test_idempotency_key_is_stable_and_namespace_scoped(namespace: str, values: list[int]) -> None:
    if not namespace.strip():
        with pytest.raises(DomainError, match="namespace"):
            stable_idempotency_key(namespace, tuple(values))
        return
    first = stable_idempotency_key(namespace, tuple(values))
    assert first == stable_idempotency_key(namespace, tuple(values))
    assert first != stable_idempotency_key(f"other-{namespace}", tuple(values))


@given(st.integers(min_value=1, max_value=100))
def test_ledger_fold_preserves_order(sequence_length: int) -> None:
    history = tuple(
        StateTransition(
            "aggregate",
            "FixtureState",
            sequence,
            str(sequence - 1),
            str(sequence),
            sequence,
            "fixture",
            ReasonCode.INVALID_SCHEMA,
        )
        for sequence in range(1, sequence_length + 1)
    )
    assert fold_transitions("0", history) == str(sequence_length)
