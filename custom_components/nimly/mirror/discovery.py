"""Bridge discovery over MQTT: which kits exist and which are free.

Every Nimly Shadow Bridge publishes its retained identity on its own
``<prefix>/info`` topic (firmware 0.6.0 and later). The wizard and the bridge
entries subscribe to the wildcard ``nimly/+/info`` — plus the legacy shared
``nimly/info`` that firmware 0.5.x used — and match kits to config entries by
their MAC address, so any number of kits can share one broker without any
hand-written topic.

Pure logic with no Home Assistant imports (the unit tests load it by path).
"""

from __future__ import annotations

from typing import Any

LEGACY_TOPIC = "nimly/info"
WILDCARD_TOPIC = "nimly/+/info"

_HEX = set("0123456789abcdef")
_CHARS = set("abcdefghijklmnopqrstuvwxyz0123456789/_-")


def normalise_mac(value: object) -> str:
    """A MAC in any separator style as 12 lowercase hex characters."""
    return "".join(ch for ch in str(value or "").lower() if ch in _HEX)


def bridge_prefix(mac: object) -> str:
    """The prefix a default-configured kit uses: ``nimly/<mac>``."""
    return f"nimly/{normalise_mac(mac)}"


def valid_prefix(value: object) -> bool:
    """The topic shapes the bridge firmware accepts (mirror of prefix_valid in C)."""
    text = str(value or "")
    if len(text) < 3 or len(text) > 47 or text.startswith("/") or text.endswith("/"):
        return False
    return all(ch in _CHARS for ch in text)


def _row(payload: dict[str, Any]) -> dict[str, Any] | None:
    mac = normalise_mac(payload.get("bridge"))
    prefix = payload.get("prefix")
    if len(mac) != 12 or not valid_prefix(prefix):
        return None
    c6 = payload.get("c6") if isinstance(payload.get("c6"), dict) else {}
    return {
        "mac": mac,
        "prefix": str(prefix),
        "fw": str(payload.get("fw") or ""),
        "model": str(payload.get("model") or "Nimly Bridge"),
        "c6": c6,
    }


def collect_bridges(
    payloads: list[dict[str, Any]], used_prefixes: set[str]
) -> list[dict[str, Any]]:
    """Discovered kits, newest payload per MAC, each marked free or taken.

    ``used_prefixes`` are the prefixes other mirror entries already bound, so a
    wizard can offer only kits nobody owns yet; free kits sort first.
    """
    by_mac: dict[str, dict[str, Any]] = {}
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        row = _row(payload)
        if row is None:
            continue
        row["free"] = row["prefix"] not in used_prefixes
        by_mac[row["mac"]] = row
    return sorted(by_mac.values(), key=lambda item: (not item["free"], item["mac"]))
