"""Unit tests for the pure helpers in nimly.mirror.facts — no Home Assistant."""

from __future__ import annotations

import importlib.util
import pathlib
import unittest

FACTS_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "custom_components"
    / "nimly"
    / "mirror"
    / "facts.py"
)
spec = importlib.util.spec_from_file_location("nimly_facts", FACTS_PATH)
assert spec is not None and spec.loader is not None
facts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(facts)


class SettingsDriftTest(unittest.TestCase):
    def test_no_drift(self) -> None:
        self.assertEqual(
            facts.compute_settings_drift(
                {"auto_relock_time": 1, "sound_volume": 2}, True, 2
            ),
            {},
        )

    def test_auto_lock_drift(self) -> None:
        drift = facts.compute_settings_drift({"auto_relock_time": 0}, True, None)
        self.assertEqual(drift["auto_lock"], {"lock": False, "app": True})

    def test_volume_drift(self) -> None:
        drift = facts.compute_settings_drift({"sound_volume": 1}, None, 2)
        self.assertEqual(drift["sound_volume"], {"lock": 1, "app": 2})

    def test_unknown_values_are_skipped(self) -> None:
        self.assertEqual(facts.compute_settings_drift({}, True, 2), {})


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
