"""The lock journal: one timeline of access and admin events.

Pure logic with no Home Assistant imports (the unit tests load it by path).

Events arrive from three places: the lock itself (0x0100 operation events),
the vendor cloud's attributed feed (which carries the person's name), and the
integration's own admin actions (PIN set/cleared, renaming, settings). One
physical unlock can show up both locally and in the cloud, so an entry is
merged with a same-event candidate instead of being stored twice: the lock's
own report keeps the time, source and slot, and a name from the cloud is
folded in.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

ORIGIN_LOCK = "lock"
ORIGIN_CLOUD = "cloud"
ORIGIN_HA = "ha"

MERGE_WINDOW_SECONDS = 240.0  # covers the cloud feed's poll lag after skew correction
DEFAULT_MAX_ENTRIES = 5000
DEFAULT_MAX_AGE_DAYS = 365

_MERGE_SCAN = 10  # only the newest entries can be the same physical event


def _parse_time(value: Any) -> float | None:
    """A timestamp as posix seconds, or None when it cannot be parsed."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


def make_entry(
    *,
    action: str,
    time: str,
    origin: str,
    source: str | None = None,
    slot: Any = None,
    name: str | None = None,
    detail: str | None = None,
) -> dict[str, Any]:
    """One journal entry, with absent fields left out."""
    entry: dict[str, Any] = {"time": time, "action": action, "origin": origin}
    if source:
        entry["source"] = source
    if isinstance(slot, int) and not isinstance(slot, bool):
        entry["slot"] = slot
    if name:
        entry["name"] = name
    if detail:
        entry["detail"] = detail
    return entry


def _compatible(first: dict[str, Any], candidate: dict[str, Any], window: float) -> bool:
    """Whether two entries describe the same physical event."""
    if first.get("action") != candidate.get("action"):
        return False
    if first.get("origin") == candidate.get("origin"):
        return False
    first_time = _parse_time(first.get("time"))
    candidate_time = _parse_time(candidate.get("time"))
    if first_time is None or candidate_time is None:
        return False
    if abs(first_time - candidate_time) > window:
        return False
    first_slot = first.get("slot")
    candidate_slot = candidate.get("slot")
    if first_slot is not None and candidate_slot is not None:
        return first_slot == candidate_slot
    return True


def _absorb(entry: dict[str, Any], candidate: dict[str, Any]) -> None:
    """Fill the entry's gaps from a same-event candidate."""
    for key in ("name", "source", "detail"):
        if not entry.get(key) and candidate.get(key):
            entry[key] = candidate[key]
    if entry.get("slot") is None and candidate.get("slot") is not None:
        entry["slot"] = candidate["slot"]


def trim(
    entries: list[dict[str, Any]],
    *,
    max_entries: int = DEFAULT_MAX_ENTRIES,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
    now: float | None = None,
) -> bool:
    """Apply the retention rules in place. True when anything was dropped."""
    dropped = False
    if max_age_days and now is not None:
        cutoff = now - max_age_days * 86400
        while entries:
            stored = _parse_time(entries[0].get("time"))
            if stored is None or stored < cutoff:
                entries.pop(0)
                dropped = True
            else:
                break
    if max_entries and len(entries) > max_entries:
        del entries[: len(entries) - max_entries]
        dropped = True
    return dropped


def add(
    entries: list[dict[str, Any]],
    candidate: dict[str, Any],
    *,
    window_seconds: float = MERGE_WINDOW_SECONDS,
    max_entries: int = DEFAULT_MAX_ENTRIES,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
    now: float | None = None,
) -> tuple[dict[str, Any], bool, bool]:
    """Merge or append one candidate; apply retention.

    Returns (stored entry, merged, trimmed): merged means the candidate was the
    same event as an existing entry, trimmed means the retention rules dropped
    something and the caller has to rewrite its store.
    """
    merged = False
    stored = candidate
    for entry in reversed(entries[-_MERGE_SCAN:]):
        if _compatible(entry, candidate, window_seconds):
            _absorb(entry, candidate)
            stored = entry
            merged = True
            break
    if not merged:
        entries.append(candidate)
    trimmed = trim(entries, max_entries=max_entries, max_age_days=max_age_days, now=now)
    return stored, merged, trimmed


def query(
    entries: list[dict[str, Any]],
    *,
    person: str | None = None,
    slot: int | None = None,
    action: str | None = None,
    since: Any = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Filter the journal, newest last, at most `limit` entries."""
    since_time = _parse_time(since)
    wanted = person.strip().lower() if isinstance(person, str) and person.strip() else None
    result = []
    for entry in entries:
        if wanted is not None:
            name = str(entry.get("name") or "").lower()
            if wanted not in name:
                continue
        if slot is not None and entry.get("slot") != slot:
            continue
        if action is not None and entry.get("action") != action:
            continue
        if since_time is not None:
            stored = _parse_time(entry.get("time"))
            if stored is None or stored < since_time:
                continue
        result.append(entry)
    if limit and len(result) > limit:
        result = result[-limit:]
    return result


def summarize(entries: list[dict[str, Any]], *, now: float | None = None) -> dict[str, int]:
    """Totals for the sensor attributes."""
    total = len(entries)
    recent = 0
    if now is not None:
        cutoff = now - 86400
        for entry in reversed(entries):
            stored = _parse_time(entry.get("time"))
            if stored is not None and stored >= cutoff:
                recent += 1
    return {"total": total, "last_24h": recent}
