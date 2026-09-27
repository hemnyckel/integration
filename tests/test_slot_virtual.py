"""Unit tests for app slot virtualization in hemnyckel.mirror.slot_virtual — no Home Assistant."""

from __future__ import annotations

import importlib.util
import pathlib
import unittest

RULES_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "custom_components"
    / "hemnyckel"
    / "mirror"
    / "slot_virtual.py"
)
spec = importlib.util.spec_from_file_location("hemnyckel_slot_virtual", RULES_PATH)
assert spec is not None and spec.loader is not None
virt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(virt)

FLOOR = 3
CAPACITY = 50


class PersistenceTest(unittest.TestCase):
    def test_round_trip(self) -> None:
        mapping = {3: 7, 12: 4}
        stored = virt.dump_map(mapping)
        self.assertEqual(stored, {"3": 7, "12": 4})
        self.assertEqual(virt.load_map(stored), mapping)

    def test_load_skips_garbage(self) -> None:
        self.assertEqual(virt.load_map({"3": "7", "x": 4, "5": None}), {3: 7})
        self.assertEqual(virt.load_map(None), {})
        self.assertEqual(virt.load_map("nope"), {})
        self.assertEqual(virt.load_map({"4": -1}), {})

    def test_dump_sorts(self) -> None:
        self.assertEqual(list(virt.dump_map({9: 2, 3: 4})), ["3", "9"])


class ResolveWriteTest(unittest.TestCase):
    def test_free_slot_passes_through(self) -> None:
        outcome, real, reason = virt.resolve_write(
            3, mapping={}, local_pins=set(), floor=FLOOR, capacity=CAPACITY
        )
        self.assertEqual((outcome, real, reason), (virt.PASS, 3, None))

    def test_local_collision_relocates(self) -> None:
        outcome, real, reason = virt.resolve_write(
            3, mapping={}, local_pins={3}, floor=FLOOR, capacity=CAPACITY
        )
        self.assertEqual((outcome, real), (virt.MOVE, 4))
        self.assertIsNone(reason)

    def test_relocation_avoids_other_mappings(self) -> None:
        outcome, real, _reason = virt.resolve_write(
            5, mapping={3: 4}, local_pins={5}, floor=FLOOR, capacity=CAPACITY
        )
        self.assertEqual((outcome, real), (virt.MOVE, 3))
        self.assertNotEqual(real, 4)

    def test_a_mappings_real_slot_is_not_stolen(self) -> None:
        outcome, real, _reason = virt.resolve_write(
            4, mapping={3: 4}, local_pins=set(), floor=FLOOR, capacity=CAPACITY
        )
        self.assertEqual((outcome, real), (virt.MOVE, 3))
        self.assertNotEqual(real, 4)

    def test_existing_mapping_is_reused_for_an_edit(self) -> None:
        outcome, real, reason = virt.resolve_write(
            3, mapping={3: 7}, local_pins={3, 12}, floor=FLOOR, capacity=CAPACITY
        )
        self.assertEqual((outcome, real, reason), (virt.REUSE, 7, None))

    def test_identity_mapping_is_an_app_edit(self) -> None:
        # A passthrough write is remembered as 6 -> 6; later edits must not be
        # treated as a collision with a local credential.
        outcome, real, reason = virt.resolve_write(
            6, mapping={6: 6}, local_pins=set(), floor=FLOOR, capacity=CAPACITY
        )
        self.assertEqual((outcome, real, reason), (virt.REUSE, 6, None))

    def test_master_slot_is_blocked(self) -> None:
        outcome, real, reason = virt.resolve_write(
            1, mapping={}, local_pins=set(), floor=FLOOR, capacity=CAPACITY
        )
        self.assertEqual((outcome, real), (virt.BLOCKED, None))
        self.assertIn("master", reason or "")

    def test_capacity_is_respected(self) -> None:
        outcome, _real, reason = virt.resolve_write(
            50, mapping={}, local_pins=set(), floor=FLOOR, capacity=CAPACITY
        )
        self.assertEqual(outcome, virt.BLOCKED)
        self.assertIn("capacity", reason or "")

    def test_full_lock_blocks_instead_of_overwriting(self) -> None:
        outcome, real, reason = virt.resolve_write(
            3, mapping={}, local_pins={3, 4}, floor=FLOOR, capacity=5
        )
        self.assertEqual((outcome, real), (virt.BLOCKED, None))
        self.assertIn("local credential", reason or "")

    def test_invalid_numbers_are_blocked(self) -> None:
        for bad in (-1, True, "3"):
            outcome, _real, _reason = virt.resolve_write(
                bad, mapping={}, local_pins=set(), floor=FLOOR, capacity=CAPACITY
            )
            self.assertEqual(outcome, virt.BLOCKED)


class ResolveClearTest(unittest.TestCase):
    def test_mapped_slot_clears_the_real_slot(self) -> None:
        self.assertEqual(
            virt.resolve_clear(3, mapping={3: 7}, local_pins={3}), (virt.CLEAR, 7)
        )

    def test_identity_mapping_clears_the_apps_own_slot(self) -> None:
        self.assertEqual(
            virt.resolve_clear(6, mapping={6: 6}, local_pins=set()), (virt.CLEAR, 6)
        )

    def test_local_credential_is_left_alone(self) -> None:
        self.assertEqual(
            virt.resolve_clear(3, mapping={}, local_pins={3}), (virt.IGNORE, None)
        )

    def test_nothing_to_clear(self) -> None:
        self.assertEqual(
            virt.resolve_clear(3, mapping={}, local_pins=set()), (virt.NOOP, None)
        )


class TranslationTest(unittest.TestCase):
    def test_both_directions(self) -> None:
        mapping = {3: 7, 12: 4}
        self.assertEqual(virt.real_of(3, mapping), 7)
        self.assertEqual(virt.virtual_of(7, mapping), 3)
        self.assertIsNone(virt.real_of(9, mapping))
        self.assertIsNone(virt.virtual_of(9, mapping))


if __name__ == "__main__":
    unittest.main()
