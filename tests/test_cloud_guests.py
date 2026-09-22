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


class ChooseIdentityTest(unittest.TestCase):
    def choose(self, name, guests, on_this_lock=(), linked=()):
        return planning.choose_identity(
            name, guests, on_this_lock=set(on_this_lock), linked=set(linked)
        )

    def test_creates_when_nothing_matches(self) -> None:
        guests = [{"name": "Isabelle", "id": "b", "hasDoorlockAccess": True}]
        self.assertEqual(self.choose("Margareta", guests), ("create", ""))

    def test_adopts_the_lone_identity(self) -> None:
        guests = [{"name": "Margareta", "id": "a", "hasDoorlockAccess": False}]
        self.assertEqual(self.choose("Margareta", guests), ("adopt", "a"))

    def test_adopts_across_locks(self) -> None:
        # The identity already holds a code on another lock; this lock has none.
        guests = [{"name": "Margareta", "id": "a", "hasDoorlockAccess": True}]
        self.assertEqual(self.choose("Margareta", guests), ("adopt", "a"))

    def test_conflict_on_this_lock_when_unrecorded(self) -> None:
        guests = [{"name": "Margareta", "id": "a", "hasDoorlockAccess": True}]
        self.assertEqual(
            self.choose("Margareta", guests, on_this_lock={"a"}), ("conflict", "")
        )

    def test_adopts_our_linked_identity_even_with_access_here(self) -> None:
        guests = [{"name": "Margareta", "id": "a", "hasDoorlockAccess": True}]
        self.assertEqual(
            self.choose("Margareta", guests, on_this_lock={"a"}, linked={"a"}),
            ("adopt", "a"),
        )

    def test_ambiguous_names_are_a_conflict(self) -> None:
        guests = [
            {"name": "Margareta", "id": "a", "hasDoorlockAccess": False},
            {"name": "margareta", "id": "b", "hasDoorlockAccess": False},
        ]
        self.assertEqual(self.choose("Margareta", guests), ("conflict", ""))

    def test_several_candidates_pick_the_linked_one(self) -> None:
        guests = [
            {"name": "Margareta", "id": "a", "hasDoorlockAccess": False},
            {"name": "Margareta", "id": "b", "hasDoorlockAccess": True},
        ]
        self.assertEqual(self.choose("Margareta", guests, linked={"b"}), ("adopt", "b"))

    def test_case_and_space_insensitive(self) -> None:
        guests = [{"name": " Margareta ", "id": "a", "hasDoorlockAccess": False}]
        self.assertEqual(self.choose("margareta", guests), ("adopt", "a"))


class SameNamedTest(unittest.TestCase):
    def test_flags_conflicts(self) -> None:
        guests = [{"name": "Margareta", "id": "a", "hasDoorlockAccess": True}]
        self.assertTrue(planning.same_named("margareta", guests))
        self.assertFalse(planning.same_named("Städfirma", guests))

    def test_all_returns_every_match(self) -> None:
        guests = [
            {"name": "Margareta", "id": "a"},
            {"name": "MARGARETA", "id": "b"},
            {"name": "Isabelle", "id": "c"},
        ]
        self.assertEqual([g["id"] for g in planning.same_named_all("margareta", guests)], ["a", "b"])


if __name__ == "__main__":
    unittest.main()
