"""Unit tests for the pure helpers in hemnyckel.mirror.facts — no Home Assistant."""

from __future__ import annotations

import importlib.util
import pathlib
import unittest

FACTS_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "custom_components"
    / "hemnyckel"
    / "mirror"
    / "facts.py"
)
spec = importlib.util.spec_from_file_location("hemnyckel_facts", FACTS_PATH)
assert spec is not None and spec.loader is not None
facts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(facts)


class CapabilitySummaryTest(unittest.TestCase):
    def test_summary(self) -> None:
        self.assertEqual(
            facts.capability_summary(
                {
                    "num_of_pin_users_supported": 50,
                    "num_of_rfid_users_supported": 50,
                    "num_of_total_users_supported": 100,
                }
            ),
            "50 PIN · 50 RFID · 100 total",
        )

    def test_partial_summary(self) -> None:
        self.assertEqual(
            facts.capability_summary({"num_of_pin_users_supported": 50}), "50 PIN"
        )

    def test_empty(self) -> None:
        self.assertIsNone(facts.capability_summary({}))


class PlaceholderNameTest(unittest.TestCase):
    def test_placeholders(self) -> None:
        self.assertTrue(facts.placeholder_slot_name(None))
        self.assertTrue(facts.placeholder_slot_name(""))
        self.assertTrue(facts.placeholder_slot_name("App slot 10"))
        self.assertTrue(facts.placeholder_slot_name("app slot 4"))

    def test_real_names(self) -> None:
        self.assertFalse(facts.placeholder_slot_name("Claes"))
        self.assertFalse(facts.placeholder_slot_name("Isabelle"))
        self.assertFalse(facts.placeholder_slot_name("App slotrummet"))


if __name__ == "__main__":
    unittest.main()
