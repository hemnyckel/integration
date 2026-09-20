"""Unit tests for the pure logic in nimly.const — no Home Assistant needed."""

from __future__ import annotations

import importlib.util
import pathlib
import unittest

CONST_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "custom_components"
    / "nimly"
    / "const.py"
)
spec = importlib.util.spec_from_file_location("nimly_const", CONST_PATH)
assert spec is not None and spec.loader is not None
const = importlib.util.module_from_spec(spec)
spec.loader.exec_module(const)


class ConstantsTest(unittest.TestCase):
    def test_domain(self) -> None:
        self.assertEqual(const.DOMAIN, "nimly")


class ParseEventTest(unittest.TestCase):
    def test_known_events(self) -> None:
        self.assertEqual(
            const.parse_event("DOORLOCK_UNLOCKED_BY_PIN"), ("unlock", "keypad")
        )
        self.assertEqual(
            const.parse_event("DOORLOCK_UNLOCKED_BY_FINGER"),
            ("unlock", "fingerprint"),
        )
        self.assertEqual(
            const.parse_event("DOORLOCK_LOCKED_FROM_APP"), ("lock", "zigbee")
        )

    def test_unknown_doorlock_event_is_read_structurally(self) -> None:
        action, source = const.parse_event("DOORLOCK_UNLOCKED_WITH_NEW_METHOD")
        self.assertEqual(action, "unlock")
        self.assertEqual(source, "unattributed")

    def test_unlock_is_not_hidden_by_the_prefix(self) -> None:
        action, _ = const.parse_event("DOORLOCK_UNLOCK_WITH_PIN")
        self.assertEqual(action, "unlock")

    def test_non_doorlock_values(self) -> None:
        self.assertEqual(const.parse_event("SOMETHING_ELSE"), (None, None))
        self.assertEqual(const.parse_event(None), (None, None))
        self.assertEqual(const.parse_event(""), (None, None))


if __name__ == "__main__":
    unittest.main()
