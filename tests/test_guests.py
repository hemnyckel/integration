"""Unit tests for the guest-code logic in hemnyckel.mirror.guests — no HA needed."""

from __future__ import annotations

import importlib.util
import pathlib
import random
import unittest
from datetime import datetime, timezone

GUESTS_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "custom_components"
    / "hemnyckel"
    / "mirror"
    / "guests.py"
)
spec = importlib.util.spec_from_file_location("hemnyckel_guests", GUESTS_PATH)
assert spec is not None and spec.loader is not None
guests = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guests)

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)


class GenerateCodeTest(unittest.TestCase):
    def test_shape(self) -> None:
        code = guests.generate_code(rng=random.Random(1))
        self.assertEqual(len(code), 6)
        self.assertTrue(code.isdigit())

    def test_deterministic_for_tests(self) -> None:
        self.assertEqual(
            guests.generate_code(rng=random.Random(7)),
            guests.generate_code(rng=random.Random(7)),
        )

    def test_valid_code(self) -> None:
        self.assertTrue(guests.valid_code("1234"))
        self.assertTrue(guests.valid_code("12345678"))
        self.assertFalse(guests.valid_code("123"))
        self.assertFalse(guests.valid_code("123456789"))
        self.assertFalse(guests.valid_code("12a4"))


class PickSlotTest(unittest.TestCase):
    def test_lowest_free(self) -> None:
        self.assertEqual(guests.pick_slot({3, 4, 6}, 3, 10), 5)

    def test_full_lock(self) -> None:
        self.assertIsNone(guests.pick_slot({3, 4}, 3, 5))

    def test_floor_is_respected(self) -> None:
        self.assertEqual(guests.pick_slot(set(), 3, 10), 3)


class UntilTest(unittest.TestCase):
    def test_normalize(self) -> None:
        self.assertEqual(
            guests.normalize_until("2026-09-21T14:00:00+02:00"),
            "2026-09-21T12:00:00+00:00",
        )
        self.assertEqual(
            guests.normalize_until("2026-09-21T12:00:00Z"),
            "2026-09-21T12:00:00+00:00",
        )

    def test_bad_values(self) -> None:
        self.assertIsNone(guests.normalize_until(None))
        self.assertIsNone(guests.normalize_until("snart"))
        self.assertIsNone(guests.normalize_until(1234))

    def test_expiry(self) -> None:
        self.assertTrue(guests.is_expired("2026-09-21T11:59:00+00:00", NOW))
        self.assertFalse(guests.is_expired("2026-09-21T12:01:00+00:00", NOW))
        self.assertFalse(guests.is_expired(None, NOW))

    def test_expired_slots_sorted(self) -> None:
        store = {
            "9": {"until": "2026-09-21T13:00:00+00:00"},
            "4": {"until": "2026-09-21T11:00:00+00:00"},
            "5": {"until": None},
            "7": {"until": "2026-09-21T11:30:00+00:00"},
        }
        self.assertEqual(guests.expired_slots(store, NOW), [4, 7])


class CodeOwnerTest(unittest.TestCase):
    def test_finds_the_recurring_guest(self) -> None:
        store = {
            "3": {"kind": "recurring", "code": "111222", "name": "Cleaner"},
            "4": {"kind": "simple", "name": "One-shot"},
            "5": {"kind": "recurring", "code": "333444", "name": "Guest"},
        }
        self.assertEqual(guests.code_owner(store, "333444"), 5)
        self.assertEqual(guests.code_owner(store, "111222"), 3)

    def test_simple_guests_have_no_stored_code(self) -> None:
        store = {"4": {"kind": "simple", "name": "One-shot"}}
        self.assertIsNone(guests.code_owner(store, "123456"))

    def test_unknown_and_empty(self) -> None:
        store = {"3": {"kind": "recurring", "code": "111222"}}
        self.assertIsNone(guests.code_owner(store, "999999"))
        self.assertIsNone(guests.code_owner(store, ""))
        self.assertIsNone(guests.code_owner(store, None))


if __name__ == "__main__":
    unittest.main()
