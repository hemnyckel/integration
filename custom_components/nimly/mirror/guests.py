"""Guest codes: slot picking, code generation and expiry bookkeeping.

Pure logic with no Home Assistant imports (the unit tests load it by path).

The lock and its module have no schedules at all - the vendor's own module
specification says so and the ZCL schedule attributes answer UNSUPPORTED - so
validity windows are enforced here: the code is written when the guest window
starts and cleared when it ends. During the window the code works offline; if
Home Assistant is down at the end, the clear happens when it comes back.
"""

from __future__ import annotations

import random
from datetime import datetime, timezone
from typing import Any

CODE_LENGTH = 6
DIGITS = "0123456789"

GUEST_CREATED = "guest_created"
GUEST_REVOKED = "guest_revoked"
GUEST_EXPIRED = "guest_expired"
GUEST_WINDOW_OPEN = "guest_window_open"
GUEST_WINDOW_CLOSE = "guest_window_close"
GUEST_USED = "guest_used"


def generate_code(length: int = CODE_LENGTH, rng: random.Random | None = None) -> str:
    """A random numeric code; leading zeros are kept."""
    source = rng or random.SystemRandom()
    return "".join(source.choice(DIGITS) for _ in range(length))


def valid_code(code: str) -> bool:
    """The lock takes 4-8 digits; anything else is rejected before it is written."""
    return code.isdigit() and 4 <= len(code) <= 8


def pick_slot(occupied: set[int], first_user_slot: int, capacity: int) -> int | None:
    """The lowest free user slot, or None when the lock has no room left."""
    slot = max(1, int(first_user_slot))
    while slot < capacity:
        if slot not in occupied:
            return slot
        slot += 1
    return None


def normalize_until(value: Any) -> str | None:
    """Normalize an ISO timestamp to UTC; None when it cannot be parsed."""
    if value is None or isinstance(value, (int, float, bool)):
        return None
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def is_expired(until: str | None, now: datetime) -> bool:
    """True when the window has ended; an entry without an end never expires."""
    if not until:
        return False
    try:
        parsed = datetime.fromisoformat(until)
    except ValueError:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed <= now


def code_owner(guests: dict[str, dict[str, Any]], code: str) -> int | None:
    """The slot of the guest whose stored code this is, if any.

    Used when the app pushes a code back that we already hold: the push must
    bind to the guest's existing slot instead of writing a second copy. Both
    guest kinds count — a temporary guest's synced access must not leave a twin
    behind when the guest is revoked or expires.
    """
    wanted = str(code or "")
    if not wanted:
        return None
    for key, guest in guests.items():
        if not isinstance(guest, dict):
            continue
        if str(guest.get("code") or "") != wanted:
            continue
        try:
            return int(key)
        except (TypeError, ValueError):
            continue
    return None


def expired_slots(guests: dict[str, dict[str, Any]], now: datetime) -> list[int]:
    """The slots whose guest window is over, oldest deadline first."""
    due: list[tuple[str, int]] = []
    for key, guest in guests.items():
        try:
            slot = int(key)
        except (TypeError, ValueError):
            continue
        until = guest.get("until") if isinstance(guest, dict) else None
        if until and is_expired(str(until), now):
            due.append((str(until), slot))
    return [slot for _until, slot in sorted(due)]
