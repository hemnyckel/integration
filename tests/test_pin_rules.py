"""Unit tests for the slot rules in hemnyckel.mirror.pin_rules — no Home Assistant."""

from __future__ import annotations

import importlib.util
import pathlib
import unittest

RULES_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "custom_components"
    / "hemnyckel"
    / "mirror"
    / "pin_rules.py"
)
spec = importlib.util.spec_from_file_location("hemnyckel_pin_rules", RULES_PATH)
assert spec is not None and spec.loader is not None
rules = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rules)


class FirstUserSlotTest(unittest.TestCase):
    def test_the_floor_is_a_constant(self) -> None:
        # Slots 0-2 are the locks' master credentials. Nothing - no stored
        # option, no service call - may lower this.
        self.assertEqual(rules.FIRST_USER_SLOT, 3)


class PinCapacityTest(unittest.TestCase):
    def test_lock_report_wins(self) -> None:
        self.assertEqual(rules.pin_capacity({"num_of_pin_users_supported": 50}), 50)

    def test_fallback(self) -> None:
        self.assertEqual(rules.pin_capacity(None), 1000)
        self.assertEqual(rules.pin_capacity({}), 1000)
        self.assertEqual(rules.pin_capacity({"num_of_pin_users_supported": 0}), 1000)
        self.assertEqual(
            rules.pin_capacity({"num_of_pin_users_supported": "nope"}), 1000
        )


class CheckCredentialSlotTest(unittest.TestCase):
    def test_every_master_slot_is_refused(self) -> None:
        for slot in (0, 1, 2):
            self.assertIsNotNone(rules.check_credential_slot(slot, {}), slot)
        self.assertIsNone(rules.check_credential_slot(3, {}))

    def test_capacity_ceiling(self) -> None:
        facts = {"num_of_pin_users_supported": 50}
        self.assertIsNone(rules.check_credential_slot(49, facts))
        self.assertIsNotNone(rules.check_credential_slot(50, facts))

    def test_last_manual_slot(self) -> None:
        self.assertIsNone(rules.check_credential_slot(999, {}))

    def test_invalid_slot(self) -> None:
        self.assertIsNotNone(rules.check_credential_slot(-1, {}))
        self.assertIsNotNone(rules.check_credential_slot(True, {}))  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
