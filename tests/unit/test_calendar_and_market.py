from __future__ import annotations

from datetime import UTC, date, datetime, time

import pytest
from domain_helpers import ES_CONTRACT, NES_CONTRACT, book

from sopara.domain.calendar import CalendarDay, ExchangeCalendar, WindowName
from sopara.domain.errors import DomainError
from sopara.domain.market import (
    BookState,
    Completeness,
    DuplicateDisposition,
    MarketEventIdentity,
    SourceEvent,
    SourceWatermark,
    classify_duplicate,
)
from sopara.domain.reason_codes import ReasonCode


def instant_ns(value: datetime) -> int:
    return int(value.timestamp()) * 1_000_000_000


def calendar(*days: CalendarDay) -> ExchangeCalendar:
    return ExchangeCalendar("calendar-v1", "2026a", days)


def test_approved_windows_midday_dst_and_early_close() -> None:
    winter = CalendarDay(date(2026, 1, 15), -300)
    summer = CalendarDay(date(2026, 6, 15), -240)
    early = CalendarDay(date(2026, 11, 27), -300, early_close=time(14, 0))
    policy = calendar(winter, summer, early)

    assert (
        policy.classify(
            instant_ns(datetime(2026, 1, 15, 14, 45, tzinfo=UTC)), winter.trade_date
        ).window
        is WindowName.AM
    )
    assert (
        policy.classify(
            instant_ns(datetime(2026, 6, 15, 13, 45, tzinfo=UTC)), summer.trade_date
        ).window
        is WindowName.AM
    )
    assert not policy.classify(
        instant_ns(datetime(2026, 1, 15, 17, 30, tzinfo=UTC)), winter.trade_date
    ).eligible
    after_close = policy.classify(
        instant_ns(datetime(2026, 11, 27, 19, 1, tzinfo=UTC)), early.trade_date
    )
    assert after_close.reason is ReasonCode.EARLY_CLOSE


@pytest.mark.parametrize(
    ("day", "reason"),
    [
        (CalendarDay(date(2026, 12, 25), -300, holiday=True), ReasonCode.HOLIDAY),
        (CalendarDay(date(2026, 9, 18), -240, expiry_session=True), ReasonCode.EXPIRY_SESSION),
        (CalendarDay(date(2026, 9, 14), -240, roll_overlap=True), ReasonCode.ROLL_OVERLAP),
    ],
)
def test_calendar_overrides_ordinary_window(day: CalendarDay, reason: ReasonCode) -> None:
    decision = calendar(day).classify(
        instant_ns(datetime.combine(day.trade_date, time(15), tzinfo=UTC)), day.trade_date
    )
    assert decision.reason is reason


def source_event(payload: str = "a" * 64, sequence: int = 11) -> SourceEvent:
    identity = MarketEventIdentity(
        "source", "channel", "session", sequence, 0, NES_CONTRACT.canonical
    )
    return SourceEvent(identity, payload, 100, None, 101, False)


def test_duplicate_identity_distinguishes_identical_and_conflicting_payloads() -> None:
    original = source_event()
    assert classify_duplicate(original, source_event()) is DuplicateDisposition.IDENTICAL
    assert classify_duplicate(original, source_event("b" * 64)) is DuplicateDisposition.CONFLICT


def test_watermarks_cannot_total_order_independent_streams_and_detect_gaps() -> None:
    watermark = SourceWatermark(
        "source", "channel", "session", 10, 90, None, Completeness.CONTIGUOUS
    )
    assert watermark.advance(source_event()).last_contiguous_sequence == 11
    assert watermark.advance(source_event(sequence=12)).completeness is Completeness.GAPPED
    other = SourceEvent(
        MarketEventIdentity("other", "channel", "session", 11, 0, ES_CONTRACT.canonical),
        "a" * 64,
        100,
        101,
        102,
        False,
    )
    with pytest.raises(DomainError, match="independent streams"):
        watermark.advance(other)
    with pytest.raises(DomainError, match="cannot be ordered"):
        source_event().identity.compare_within_stream(other.identity)


def test_identity_order_is_defined_only_within_one_stream() -> None:
    first = source_event(sequence=11).identity
    second = source_event(sequence=12).identity
    assert first.compare_within_stream(second) == -1
    assert second.compare_within_stream(first) == 1


@pytest.mark.parametrize(
    ("state", "reason"),
    [
        ({"bid": None}, ReasonCode.NES_BOOK_EMPTY),
        ({"bid_size": 0}, ReasonCode.NES_BOOK_EMPTY),
        ({"bid": None, "ask": None}, ReasonCode.NES_BOOK_EMPTY),
        ({"bid": book().ask, "ask": book().bid}, ReasonCode.NES_BOOK_CROSSED),
        ({"bid": book().ask}, ReasonCode.NES_BOOK_LOCKED),
        ({"stale": True}, ReasonCode.NES_STALE),
        ({"halted": True}, ReasonCode.MARKET_HALTED),
        ({"price_limited": True}, ReasonCode.PRICE_LIMITED),
        ({"last_trade_size": 0}, ReasonCode.ZERO_VOLUME),
    ],
)
def test_nes_book_failure_modes(state: dict[str, object], reason: ReasonCode) -> None:
    assert book(**state).blocking_reason() is reason


def test_es_book_cannot_be_used_for_execution() -> None:
    invalid = BookState(ES_CONTRACT, None, None, 0, 0, 0)
    with pytest.raises(DomainError, match="NES contract"):
        invalid.blocking_reason()
