from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, timezone
from enum import StrEnum

from sopara.domain.errors import DomainError
from sopara.domain.reason_codes import ReasonCode


class WindowName(StrEnum):
    AM = "AM"
    PM = "PM"


@dataclass(frozen=True, slots=True)
class LocalWindow:
    name: WindowName
    start: time
    end: time

    def __post_init__(self) -> None:
        if self.end <= self.start:
            raise DomainError("research window must have positive duration")


APPROVED_WINDOWS = (
    LocalWindow(WindowName.AM, time(9, 45), time(12, 0)),
    LocalWindow(WindowName.PM, time(13, 30), time(15, 45)),
)


@dataclass(frozen=True, slots=True)
class CalendarDay:
    trade_date: date
    utc_offset_minutes: int
    holiday: bool = False
    early_close: time | None = None
    expiry_session: bool = False
    roll_overlap: bool = False

    def __post_init__(self) -> None:
        if not -14 * 60 <= self.utc_offset_minutes <= 14 * 60:
            raise DomainError("calendar UTC offset is outside the supported range")


@dataclass(frozen=True, slots=True)
class WindowDecision:
    eligible: bool
    window: WindowName | None
    reason: ReasonCode | None


@dataclass(frozen=True, slots=True)
class ExchangeCalendar:
    version: str
    tzdata_version: str
    days: tuple[CalendarDay, ...]
    windows: tuple[LocalWindow, ...] = APPROVED_WINDOWS

    def __post_init__(self) -> None:
        dates = [day.trade_date for day in self.days]
        if len(set(dates)) != len(dates):
            raise DomainError("calendar contains duplicate trade dates")

    def day(self, trade_date: date) -> CalendarDay:
        try:
            return next(day for day in self.days if day.trade_date == trade_date)
        except StopIteration as error:
            raise DomainError("trade date is absent from the bound calendar") from error

    def classify(self, at_ns: int, trade_date: date) -> WindowDecision:
        if at_ns < 0:
            raise DomainError("calendar instant cannot be negative")
        day = self.day(trade_date)
        if day.holiday or trade_date.weekday() >= 5:
            return WindowDecision(False, None, ReasonCode.HOLIDAY)
        if day.expiry_session:
            return WindowDecision(False, None, ReasonCode.EXPIRY_SESSION)
        if day.roll_overlap:
            return WindowDecision(False, None, ReasonCode.ROLL_OVERLAP)
        local_zone = timezone(timedelta(minutes=day.utc_offset_minutes))
        seconds, nanoseconds = divmod(at_ns, 1_000_000_000)
        instant = (
            datetime(1970, 1, 1, tzinfo=UTC)
            + timedelta(seconds=seconds, microseconds=nanoseconds // 1_000)
        ).astimezone(local_zone)
        if instant.date() != trade_date:
            return WindowDecision(False, None, ReasonCode.OUTSIDE_APPROVED_WINDOW)
        local_time = instant.time().replace(tzinfo=None)
        for window in self.windows:
            effective_end = min(window.end, day.early_close or window.end)
            if window.start <= local_time < effective_end:
                return WindowDecision(True, window.name, None)
        reason = (
            ReasonCode.EARLY_CLOSE
            if day.early_close is not None and local_time >= day.early_close
            else ReasonCode.OUTSIDE_APPROVED_WINDOW
        )
        return WindowDecision(False, None, reason)
