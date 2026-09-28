"""The finger policy: one template per slot, and labels as claims.

Pure logic with no Home Assistant imports (the unit tests load it by path).

The lock reports only *which slot* a fingerprint opened from — never which
finger, and never whether a template exists. A label is therefore a claim the
owner makes at enrolment; a slot's fingerprint is only *confirmed* once a finger
in it has really opened the door. The physical test (``docs/fingerprints.md``
section 2) settled the storage question: the reader blinks red and refuses a
second enrolment into a slot that already holds one, so a slot holds at most one
template. Several fingers per person therefore means several slots, each with
its own label.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

# One slot holds at most this many templates. The lock's own refusal of a second
# enrolment into an occupied slot is what set this to 1; ``None`` would mean
# unbounded (the rejected branch B of the design).
TEMPLATES_PER_SLOT: int | None = 1

# The canonical finger names, so the *same* finger on two locks reads as the
# same word. Free text is allowed, but the join across locks is by exact label,
# so a picker should offer these.
FINGER_LABELS: tuple[str, ...] = (
    "left thumb",
    "left index",
    "left middle",
    "left ring",
    "left little",
    "right thumb",
    "right index",
    "right middle",
    "right ring",
    "right little",
    "other",
)


def normalise_label(label: Any) -> str:
    """A stored label: trimmed, single-spaced and lower-case.

    Normalising at write time is what makes the exact-label join across locks
    work: "Left Index" typed on one door and "left index" on another are the
    same finger.
    """
    if not isinstance(label, str):
        return ""
    return " ".join(label.split()).lower()


def _label_of(item: Any) -> str:
    """The normalised label of a stored finger entry (a dict or a bare label)."""
    if isinstance(item, Mapping):
        return normalise_label(item.get("label"))
    return normalise_label(item)


def finger_labels(slot_data: Mapping[str, Any] | None) -> list[str]:
    """The labels recorded in a slot, in enrolment order."""
    if not isinstance(slot_data, Mapping):
        return []
    items = slot_data.get("fingers")
    if not isinstance(items, list):
        return []
    labels: list[str] = []
    for item in items:
        label = _label_of(item)
        if label:
            labels.append(label)
    return labels


def _template_count(slot_data: Mapping[str, Any] | None) -> int:
    """How many templates the slot is believed to hold.

    The labelled entries are the claim; a bare ``has_fingerprint`` mark (from
    before labels existed) still counts, because the lock refuses a second
    enrolment into such a slot just the same.
    """
    if not isinstance(slot_data, Mapping):
        return 0
    labels = finger_labels(slot_data)
    if labels:
        return len(labels)
    return 1 if slot_data.get("has_fingerprint") else 0


def can_add(
    slot_data: Mapping[str, Any] | None,
    label: Any = None,
    templates_per_slot: int | None = TEMPLATES_PER_SLOT,
) -> str | None:
    """None when another template may be added to the slot, else the reason.

    ``label`` is the finger the caller wants to add; it may be omitted when the
    question is only whether the slot has room at all. A duplicate label is
    always refused — a slot cannot hold the same finger twice.
    """
    existing = finger_labels(slot_data)
    if label is not None:
        normalised = normalise_label(label)
        if not normalised:
            return "a finger label is required"
        if normalised in existing:
            return f"'{normalised}' is already enrolled in this slot"
    limit = templates_per_slot
    if limit is not None and _template_count(slot_data) >= limit:
        return (
            "this slot already holds a fingerprint; clear it before enrolling "
            "another finger (one template per slot)"
        )
    return None


def finger_state(slot_data: Mapping[str, Any] | None) -> str:
    """The display state of a slot's fingerprint.

    ``none``      - no label is recorded for this slot;
    ``claimed``   - a label is recorded, but no use ever confirmed it;
    ``confirmed`` - a finger in this slot has actually opened the door.
    """
    if not finger_labels(slot_data):
        return "none"
    if isinstance(slot_data, Mapping) and slot_data.get("finger_used"):
        return "confirmed"
    return "claimed"


def plan_slots(
    person: Any,
    fingers: Any,
    locks: Any,
    templates_per_slot: int | None = TEMPLATES_PER_SLOT,
) -> dict[str, Any]:
    """The slots one person's fingers need, per lock (the capacity budget).

    Branch A (``templates_per_slot == 1``) is active: every finger needs its own
    slot, so five fingers cost five slots on each lock. The rejected branch B
    (``templates_per_slot`` is ``None``) lets a person's fingers share one slot
    per lock, so the count is one whatever the finger count.
    """
    if isinstance(fingers, str):
        raw: list[Any] = [fingers]
    elif isinstance(fingers, Mapping):
        raw = list(fingers.values())
    else:
        try:
            raw = list(fingers or [])
        except TypeError:
            raw = []

    labels: list[str] = []
    seen: set[str] = set()
    for item in raw:
        label = _label_of(item)
        if label and label not in seen:
            seen.add(label)
            labels.append(label)

    limit = templates_per_slot
    locks_list = [locks] if isinstance(locks, str) else list(locks or [])
    per_lock: dict[str, int] = {}
    for lock in locks_list:
        if not labels:
            needed = 0
        elif limit is None:
            needed = 1
        elif limit <= 0:
            needed = 0
        else:
            needed = -(-len(labels) // limit)
        per_lock[str(lock)] = needed

    return {
        "person": str(person) if person else "",
        "templates_per_slot": limit,
        "labels": labels,
        "per_lock": per_lock,
        "total": sum(per_lock.values()),
    }
