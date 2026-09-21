"""Unit tests for weekly guest windows in nimly.mirror.schedule — no Home Assistant."""

from __future__ import annotations

import importlib.util
import pathlib
import unittest
from datetime import datetime, timezone

RULES_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "custom_components"
    / "nimly"
    / "mirror"
    / "schedule.py"
)
spec = importlib.util.spec_from_file_location("nimly_schedule", RULES_PATH)
assert spec is not None and spec.loader is not None
sched = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sched)

# 2026-09-21 is a Monday, so 25 is a Friday and 26 a Saturday.
FRI = "2026-09-25"
SAT = "2026-09-26"
MON = "2026-09-28"


def at(day: str, clock: str) -> datetime:
    return datetime.fromisoformat(f"{day}T{clock}:00+00:00")


WEEKDAY_MORNING = [{"days": ["fri"], "start": "08:00", "end": "12:00"}]
OVERNIGHT = [{"days": ["fri"], "start": "22:00", "end": "02:00"}]


class NormalizeTest(unittest.TestCase):
    def test_valid_window(self) -> None:
        windows = sched.normalize_windows(WEEKDAY_MORNING)
        self.assertEqual(
            windows, [{"days": ["fri"], "start": "08:00", "end": "12:00"}]
        )

    def test_single_day_string_and_dedupe(self) -> None:
        windows = sched.normalize_windows(
            [{"days": "fri", "start": "8:00", "end": "12:00"},
             {"days": ["fri", "FRI", "mon"], "start": "13:00", "end": "14:00"}]
        )
        self.assertEqual(windows[0]["start"], "08:00")
        self.assertEqual(windows[1]["days"], ["mon", "fri"])

    def test_rejects_bad_input(self) -> None:
        self.assertIsNone(sched.normalize_windows([]))
        self.assertIsNone(sched.normalize_windows("fri 08-12"))
        self.assertIsNone(sched.normalize_windows([{"days": ["xyz"], "start": "08:00", "end": "09:00"}]))
        self.assertIsNone(sched.normalize_windows([{"days": ["fri"], "start": "25:00", "end": "09:00"}]))
        self.assertIsNone(sched.normalize_windows([{"days": ["fri"], "start": "08:00"}]))
        too_many = [{"days": ["fri"], "start": "08:00", "end": "09:00"}] * 13
        self.assertIsNone(sched.normalize_windows(too_many))


class InWindowTest(unittest.TestCase):
    def test_inside_and_outside(self) -> None:
        self.assertTrue(sched.in_window(WEEKDAY_MORNING, at(FRI, "09:30")))
        self.assertFalse(sched.in_window(WEEKDAY_MORNING, at(FRI, "07:59")))
        self.assertFalse(sched.in_window(WEEKDAY_MORNING, at(FRI, "12:00")))
        self.assertFalse(sched.in_window(WEEKDAY_MORNING, at(SAT, "09:30")))

    def test_overnight(self) -> None:
        self.assertTrue(sched.in_window(OVERNIGHT, at(FRI, "23:00")))
        self.assertTrue(sched.in_window(OVERNIGHT, at(SAT, "01:00")))
        self.assertFalse(sched.in_window(OVERNIGHT, at(FRI, "21:00")))
        self.assertFalse(sched.in_window(OVERNIGHT, at(SAT, "03:00")))
        self.assertFalse(sched.in_window(OVERNIGHT, at(SAT, "22:00")))


class NextBoundaryTest(unittest.TestCase):
    def test_before_today(self) -> None:
        self.assertEqual(
            sched.next_boundary(WEEKDAY_MORNING, at(FRI, "06:00")), at(FRI, "08:00")
        )

    def test_inside_today(self) -> None:
        self.assertEqual(
            sched.next_boundary(WEEKDAY_MORNING, at(FRI, "09:30")), at(FRI, "12:00")
        )

    def test_after_today_goes_to_next_week(self) -> None:
        self.assertEqual(
            sched.next_boundary(WEEKDAY_MORNING, at(FRI, "13:00")),
            at("2026-10-02", "08:00"),
        )

    def test_overnight_end_is_next_day(self) -> None:
        self.assertEqual(
            sched.next_boundary(OVERNIGHT, at(FRI, "23:00")), at(SAT, "02:00")
        )

    def test_overnight_opening(self) -> None:
        self.assertEqual(
            sched.next_boundary(OVERNIGHT, at(FRI, "12:00")), at(FRI, "22:00")
        )

    def test_monday_window(self) -> None:
        windows = [{"days": ["mon"], "start": "07:00", "end": "09:00"}]
        self.assertEqual(
            sched.next_boundary(windows, at(FRI, "13:00")), at(MON, "07:00")
        )

    def test_empty_never_opens(self) -> None:
        self.assertIsNone(sched.next_boundary([], at(FRI, "13:00")))


class DescribeTest(unittest.TestCase):
    def test_summary(self) -> None:
        windows = [
            {"days": ["mon", "fri"], "start": "08:00", "end": "12:00"},
            {"days": ["sat"], "start": "10:00", "end": "14:00"},
        ]
        self.assertEqual(sched.describe(windows), "mon/fri 08:00-12:00; sat 10:00-14:00")


if __name__ == "__main__":
    unittest.main()
