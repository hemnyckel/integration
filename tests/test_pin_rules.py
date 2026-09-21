"""Unit tests for the slot rules in nimly.mirror.pin_rules — no Home Assistant."""

from __future__ import annotations

import importlib.util
import pathlib
import unittest

RULES_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "custom_components"
    / "nimly"
    / "mirror"
    / "pin_rules.py"
)
spec = importlib.util.spec_from_file_location("nimly_pin_rules", RULES_PATH)
assert spec is not None and spec.loader is not None
rules = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rules)


class FirstUserSlotTest(unittest.TestCase):
    def test_default_is_three(self) -> None:
        self.assertEqual(rules.first_user_slot(None), 3)
        self.assertEqual(rules.first_user_slot({}), 3)

    def test_value_is_read(self) -> None:
        self.assertEqual(rules.first_user_slot({"reserved_slots": 1}), 1)
        self.assertEqual(rules.first_user_slot({"reserved_slots": "2"}), 2)

    def test_clamped_so_slot_zero_stays_protected(self) -> None:
        self.assertEqual(rules.first_user_slot({"reserved_slots": 0}), 1)
        self.assertEqual(rules.first_user_slot({"reserved_slots": 9}), 3)
        self.assertEqual(rules.first_user_slot({"reserved_slots": "x"}), 3)


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
    def test_master_slots_are_refused(self) -> None:
        self.assertIsNotNone(rules.check_credential_slot(0, {}, {}))
        self.assertIsNotNone(rules.check_credential_slot(2, {}, {}))
        self.assertIsNone(rules.check_credential_slot(3, {}, {}))

    def test_floor_option_allows_one_and_two(self) -> None:
        options = {"reserved_slots": 1}
        self.assertIsNone(rules.check_credential_slot(1, options, {}))
        self.assertIsNone(rules.check_credential_slot(2, options, {}))
        self.assertIsNotNone(rules.check_credential_slot(0, options, {}))

    def test_capacity_ceiling(self) -> None:
        facts = {"num_of_pin_users_supported": 50}
        self.assertIsNone(rules.check_credential_slot(49, {}, facts))
        self.assertIsNotNone(rules.check_credential_slot(50, {}, facts))

    def test_last_manual_slot(self) -> None:
        self.assertIsNone(rules.check_credential_slot(999, {}, {}))

    def test_invalid_slot(self) -> None:
        self.assertIsNotNone(rules.check_credential_slot(-1, {}, {}))


if __name__ == "__main__":
    unittest.main()
