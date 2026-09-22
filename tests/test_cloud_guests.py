"""Unit tests for cloud.guests (the sync planning helpers) — no Home Assistant."""

from __future__ import annotations

import importlib.util
import pathlib
import unittest
from datetime import datetime, timezone

GUESTS_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "custom_components"
    / "nimly"
    / "cloud"
    / "guests.py"
)
spec = importlib.util.spec_from_file_location("nimly_cloud_guests", GUESTS_PATH)
assert spec is not None and spec.loader is not None
planning = importlib.util.module_from_spec(spec)
spec.loader.exec_module(planning)


class ValidityTest(unittest.TestCase):
    def test_window_is_a_century(self) -> None:
        now = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)
        start, end = planning.validity(now)
        self.assertEqual(start, "2026-09-22T12:00:00+00:00")
        self.assertEqual(end, "2126-09-22T12:00:00+00:00")

    def test_leap_day_survives_a_leap_target(self) -> None:
        now = datetime(2028, 2, 29, 8, 0, tzinfo=timezone.utc)
        start, end = planning.validity(now)
        self.assertEqual(start, "2028-02-29T08:00:00+00:00")
        self.assertEqual(end, "2128-02-29T08:00:00+00:00")

    def test_leap_day_falls_back_when_the_target_is_not_one(self) -> None:
        now = datetime(2000, 2, 29, 8, 0, tzinfo=timezone.utc)
        start, end = planning.validity(now)
        self.assertEqual(start, "2000-02-29T08:00:00+00:00")
        self.assertEqual(end, "2100-02-28T08:00:00+00:00")


class MatchGuestTest(unittest.TestCase):
    def test_adopts_the_lone_free_identity(self) -> None:
        guests = [
            {"name": "Margareta", "id": "a", "hasDoorlockAccess": False},
            {"name": "Isabelle", "id": "b", "hasDoorlockAccess": True},
        ]
        self.assertEqual(planning.match_guest("Margareta", guests)["id"], "a")

    def test_never_adopts_an_identity_with_access(self) -> None:
        guests = [{"name": "Margareta", "id": "a", "hasDoorlockAccess": True}]
        self.assertIsNone(planning.match_guest("Margareta", guests))

    def test_ambiguous_match_is_refused(self) -> None:
        guests = [
            {"name": "Margareta", "id": "a", "hasDoorlockAccess": False},
            {"name": "margareta", "id": "b", "hasDoorlockAccess": False},
        ]
        self.assertIsNone(planning.match_guest("Margareta", guests))

    def test_case_and_space_insensitive(self) -> None:
        guests = [{"name": " Margareta ", "id": "a", "hasDoorlockAccess": False}]
        self.assertEqual(planning.match_guest("margareta", guests)["id"], "a")


class SameNamedTest(unittest.TestCase):
    def test_flags_conflicts(self) -> None:
        guests = [{"name": "Margareta", "id": "a", "hasDoorlockAccess": True}]
        self.assertTrue(planning.same_named("margareta", guests))
        self.assertFalse(planning.same_named("Städfirma", guests))


if __name__ == "__main__":
    unittest.main()
