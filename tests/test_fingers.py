"""Unit tests for the finger policy in hemnyckel.mirror.fingers — no HA needed."""

from __future__ import annotations

import importlib.util
import pathlib
import unittest

FINGERS_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "custom_components"
    / "hemnyckel"
    / "mirror"
    / "fingers.py"
)
spec = importlib.util.spec_from_file_location("hemnyckel_fingers", FINGERS_PATH)
assert spec is not None and spec.loader is not None
fingers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fingers)

ENROLLED = "2026-09-28T00:00:00+00:00"


def slot(*labels: str, has_fingerprint: bool = False, finger_used: bool = False) -> dict:
    return {
        "fingers": [
            {"label": label, "enrolled": ENROLLED} for label in labels
        ],
        "has_fingerprint": has_fingerprint,
        "finger_used": finger_used,
    }


class PolicyTest(unittest.TestCase):
    def test_one_template_per_slot(self) -> None:
        # The lock refused a second enrolment into an occupied slot with a red
        # blink; that physical answer is what settled the branch.
        self.assertEqual(fingers.TEMPLATES_PER_SLOT, 1)


class CanAddTest(unittest.TestCase):
    def test_empty_slot_accepts_a_finger(self) -> None:
        self.assertIsNone(fingers.can_add(slot(), "left index"))

    def test_second_finger_is_refused(self) -> None:
        self.assertIsNotNone(fingers.can_add(slot("left index"), "right thumb"))

    def test_duplicate_label_is_refused(self) -> None:
        self.assertIsNotNone(fingers.can_add(slot("left index"), "Left Index"))

    def test_blank_label_is_refused(self) -> None:
        self.assertIsNotNone(fingers.can_add(slot(), "   "))

    def test_a_bare_hint_still_counts_as_a_template(self) -> None:
        self.assertIsNotNone(
            fingers.can_add(slot(has_fingerprint=True), "left index")
        )

    def test_capacity_only_check_without_a_label(self) -> None:
        self.assertIsNone(fingers.can_add(slot()))
        self.assertIsNotNone(fingers.can_add(slot("left index")))

    def test_unbounded_branch_allows_several(self) -> None:
        self.assertIsNone(
            fingers.can_add(slot("left index"), "right thumb", templates_per_slot=None)
        )

    def test_limit_of_two(self) -> None:
        self.assertIsNone(
            fingers.can_add(slot("left index"), "right thumb", templates_per_slot=2)
        )
        self.assertIsNotNone(
            fingers.can_add(
                slot("left index", "right thumb"),
                "right middle",
                templates_per_slot=2,
            )
        )

    def test_missing_or_garbage_slot_data(self) -> None:
        self.assertIsNone(fingers.can_add(None, "left index"))
        self.assertIsNone(fingers.can_add("nonsense", "left index"))


class CanRelabelTest(unittest.TestCase):
    def test_a_labelled_finger_is_renamed_by_its_label(self) -> None:
        self.assertIsNone(fingers.can_relabel(slot("left index"), "left index"))
        self.assertIsNone(fingers.can_relabel(slot("left index")))

    def test_a_previous_label_the_slot_does_not_carry_is_refused(self) -> None:
        self.assertIsNotNone(
            fingers.can_relabel(slot("left index"), "right thumb")
        )

    def test_an_unlabelled_confirmed_finger_can_be_named(self) -> None:
        # The case the household hit: the lock has a template (finger_used)
        # but no label, so it is named, not re-enrolled.
        self.assertIsNone(
            fingers.can_relabel(slot(has_fingerprint=True, finger_used=True))
        )
        self.assertIsNone(fingers.can_relabel(slot(finger_used=True)))

    def test_an_unconfirmed_hint_can_still_be_named(self) -> None:
        self.assertIsNone(fingers.can_relabel(slot(has_fingerprint=True)))

    def test_a_slot_with_no_fingerprint_is_refused(self) -> None:
        self.assertIsNotNone(fingers.can_relabel(slot()))

    def test_missing_or_garbage_slot_data(self) -> None:
        self.assertIsNotNone(fingers.can_relabel(None))
        self.assertIsNotNone(fingers.can_relabel("nonsense"))


class FingerStateTest(unittest.TestCase):
    def test_no_label(self) -> None:
        self.assertEqual(fingers.finger_state(slot()), "none")
        self.assertEqual(fingers.finger_state(None), "none")

    def test_claimed(self) -> None:
        self.assertEqual(fingers.finger_state(slot("left index")), "claimed")

    def test_confirmed(self) -> None:
        self.assertEqual(
            fingers.finger_state(slot("left index", finger_used=True)), "confirmed"
        )


class NormaliseLabelTest(unittest.TestCase):
    def test_trim_collapse_and_lower(self) -> None:
        self.assertEqual(fingers.normalise_label("  Left   Index "), "left index")

    def test_non_string(self) -> None:
        self.assertEqual(fingers.normalise_label(None), "")
        self.assertEqual(fingers.normalise_label(7), "")


class PlanSlotsTest(unittest.TestCase):
    def test_branch_a_one_slot_per_finger_per_lock(self) -> None:
        plan = fingers.plan_slots("Elise", ["left index", "right thumb"], ["Door", "Cellar"])
        self.assertEqual(plan["per_lock"]["Door"], 2)
        self.assertEqual(plan["per_lock"]["Cellar"], 2)
        self.assertEqual(plan["total"], 4)

    def test_branch_b_shares_one_slot_per_lock(self) -> None:
        plan = fingers.plan_slots(
            "Elise", ["left index", "right thumb"], ["Door", "Cellar"],
            templates_per_slot=None,
        )
        self.assertEqual(plan["total"], 2)

    def test_duplicates_are_counted_once(self) -> None:
        plan = fingers.plan_slots("Elise", ["left index", "Left Index"], ["Door"])
        self.assertEqual(plan["per_lock"]["Door"], 1)

    def test_no_fingers_cost_nothing(self) -> None:
        plan = fingers.plan_slots("Elise", [], ["Door"])
        self.assertEqual(plan["total"], 0)

    def test_records_as_well_as_labels(self) -> None:
        plan = fingers.plan_slots(
            "Elise", [{"label": "left index"}, {"label": "right thumb"}], ["Door"]
        )
        self.assertEqual(plan["per_lock"]["Door"], 2)

    def test_a_single_lock_may_be_a_bare_string(self) -> None:
        plan = fingers.plan_slots("Elise", ["left index"], "Door")
        self.assertEqual(plan["per_lock"], {"Door": 1})


if __name__ == "__main__":
    unittest.main()
