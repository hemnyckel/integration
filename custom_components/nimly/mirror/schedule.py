"""Weekly access windows for guest codes.

Pure logic with no Home Assistant imports (the unit tests load it by path).

The lock has no schedules at all - the vendor's own module specification says
so - so a recurring guest is enforced here: the code is written when a window
opens and cleared when it closes, and the code itself never changes. Windows
are weekly (days + a start and end time in local time); an end at or before
the start crosses midnight into the following day, so "fri 22:00-02:00" covers
Saturday night too.

A window is stored as {"days": ["mon", "fri"], "start": "08:00", "end": "12:00"}
with lowercase three-letter day codes and HH:MM times.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any, Mapping

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
DAY_INDEX = {name: index for index, name in enumerate(DAYS)}

MAX_WINDOWS = 12
SCAN_DAYS = 8  # a week plus today, so a next boundary always exists


def _parse_day(value: Any) -> int | None:
    if not isinstance(value, str):
        return None
    try:
        return DAY_INDEX[value.strip().lower()[:3]]
    except (KeyError, IndexError):
        return None


def _parse_time(value: Any) -> time | None:
    if not isinstance(value, str):
        return None
    parts = value.strip().split(":")
    if len(parts) != 2:
        return None
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except (TypeError, ValueError):
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return time(hour=hour, minute=minute)


def normalize_windows(value: Any) -> list[dict[str, Any]] | None:
    """Validate and clean a window list; None when it cannot be used."""
    if not isinstance(value, (list, tuple)) or not value:
        return None
    result: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            return None
        days = item.get("days")
        if isinstance(days, str):
            days = [days]
        if not isinstance(days, (list, tuple)) or not days:
            return None
        parsed_days = sorted({day for raw in days if (day := _parse_day(raw)) is not None})
        if not parsed_days:
            return None
        start = _parse_time(item.get("start"))
        end = _parse_time(item.get("end"))
        if start is None or end is None:
            return None
        result.append(
            {"days": [DAYS[day] for day in parsed_days], "start": _fmt(start), "end": _fmt(end)}
        )
    if len(result) > MAX_WINDOWS:
        return None
    return result


def _fmt(value: time) -> str:
    return f"{value.hour:02d}:{value.minute:02d}"


def in_window(windows: list[dict[str, Any]], now: datetime) -> bool:
    """Whether the local time is inside any window.

    The window's day refers to its start; an end at or before the start runs
    past midnight, so the early hours of the next day still count.
    """
    today = now.weekday()
    previous = (today - 1) % 7
    current = now.time()
    for window in windows:
        days = [_parse_day(day) for day in window.get("days") or []]
        start = _parse_time(window.get("start"))
        end = _parse_time(window.get("end"))
        if start is None or end is None:
            continue
        if start < end:
            if today in days and start <= current < end:
                return True
        else:
            if today in days and current >= start:
                return True
            if previous in days and current < end:
                return True
    return False


def next_boundary(windows: list[dict[str, Any]], now: datetime) -> datetime | None:
    """The next moment the inside/outside state changes, or None.

    Only the moment matters: the caller re-evaluates `in_window` when it
    arrives, so overlapping windows and midnight crossings stay simple.
    """
    candidates: list[datetime] = []
    for offset in range(SCAN_DAYS + 1):
        date = (now + timedelta(days=offset)).date()
        weekday = date.weekday()
        for window in windows:
            days = {_parse_day(day) for day in window.get("days") or []}
            if weekday not in days:
                continue
            start = _parse_time(window.get("start"))
            end = _parse_time(window.get("end"))
            if start is None or end is None:
                continue
            start_at = datetime.combine(date, start, tzinfo=now.tzinfo)
            if end <= start:
                end_at = datetime.combine(date + timedelta(days=1), end, tzinfo=now.tzinfo)
            else:
                end_at = datetime.combine(date, end, tzinfo=now.tzinfo)
            for moment in (start_at, end_at):
                if moment > now:
                    candidates.append(moment)
    return min(candidates) if candidates else None


def describe(windows: list[dict[str, Any]]) -> str:
    """A compact human summary: "mon, fri 08:00-12:00; sat 10:00-14:00"."""
    parts = []
    for window in windows:
        days = "/".join(window.get("days") or [])
        parts.append(f"{days} {window.get('start')}-{window.get('end')}")
    return "; ".join(parts)
