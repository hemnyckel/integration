"""Unit tests for the journal logic in hemnyckel.mirror.journal — no Home Assistant."""

from __future__ import annotations

import importlib.util
import pathlib
import unittest

JOURNAL_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "custom_components"
    / "hemnyckel"
    / "mirror"
    / "journal.py"
)
spec = importlib.util.spec_from_file_location("hemnyckel_journal", JOURNAL_PATH)
assert spec is not None and spec.loader is not None
journal = importlib.util.module_from_spec(spec)
spec.loader.exec_module(journal)

T0 = "2026-09-21T10:00:00+00:00"
T1 = "2026-09-21T10:00:30+00:00"  # inside the merge window
T2 = "2026-09-21T10:05:00+00:00"  # outside the merge window
NOW = 1790000000.0  # 2026-09-21-ish, only used for retention tests


def entry(action: str, time: str, origin: str, **kwargs):
    return journal.make_entry(action=action, time=time, origin=origin, **kwargs)


class MakeEntryTest(unittest.TestCase):
    def test_absent_fields_are_left_out(self) -> None:
        made = journal.make_entry(action="unlock", time=T0, origin="lock", slot=None)
        self.assertEqual(made, {"time": T0, "action": "unlock", "origin": "lock"})

    def test_fields_are_kept(self) -> None:
        made = journal.make_entry(
            action="pin_set", time=T0, origin="ha", slot=11, name="Carro"
        )
        self.assertEqual(made["slot"], 11)
        self.assertEqual(made["name"], "Carro")


class MergeTest(unittest.TestCase):
    def test_name_is_folded_into_the_local_event(self) -> None:
        entries = [entry("unlock", T0, journal.ORIGIN_LOCK, slot=11, source="keypad")]
        stored, merged, _ = journal.add(
            entries, entry("unlock", T1, journal.ORIGIN_HA, slot=11, name="Carro")
        )
        self.assertTrue(merged)
        self.assertEqual(len(entries), 1)
        self.assertEqual(stored["name"], "Carro")
        self.assertEqual(stored["source"], "keypad")
        self.assertEqual(stored["time"], T0)

    def test_different_actions_do_not_merge(self) -> None:
        entries = [entry("unlock", T0, journal.ORIGIN_LOCK, slot=11)]
        _, merged, _ = journal.add(entries, entry("lock", T1, journal.ORIGIN_HA, slot=11))
        self.assertFalse(merged)
        self.assertEqual(len(entries), 2)

    def test_same_origin_does_not_merge(self) -> None:
        entries = [entry("unlock", T0, journal.ORIGIN_HA, slot=11, name="A")]
        _, merged, _ = journal.add(entries, entry("unlock", T1, journal.ORIGIN_HA, name="B"))
        self.assertFalse(merged)
        self.assertEqual(len(entries), 2)

    def test_outside_the_window_does_not_merge(self) -> None:
        entries = [entry("unlock", T0, journal.ORIGIN_LOCK, slot=11)]
        _, merged, _ = journal.add(entries, entry("unlock", T2, journal.ORIGIN_HA, name="Carro"))
        self.assertFalse(merged)

    def test_conflicting_slots_do_not_merge(self) -> None:
        entries = [entry("unlock", T0, journal.ORIGIN_LOCK, slot=11)]
        _, merged, _ = journal.add(entries, entry("unlock", T1, journal.ORIGIN_HA, slot=12))
        self.assertFalse(merged)

    def test_a_missing_slot_still_merges_and_fills_in(self) -> None:
        entries = [entry("unlock", T0, journal.ORIGIN_HA, name="Carro")]
        stored, merged, _ = journal.add(
            entries, entry("unlock", T1, journal.ORIGIN_LOCK, slot=11, source="keypad")
        )
        self.assertTrue(merged)
        self.assertEqual(stored["slot"], 11)


class RetentionTest(unittest.TestCase):
    def test_entry_count_is_capped(self) -> None:
        entries = [entry("unlock", T0, journal.ORIGIN_LOCK, slot=i) for i in range(10)]
        trimmed = journal.trim(entries, max_entries=5, max_age_days=0)
        self.assertTrue(trimmed)
        self.assertEqual(len(entries), 5)
        self.assertEqual(entries[-1]["slot"], 9)

    def test_old_entries_age_out(self) -> None:
        entries = [
            entry("unlock", "2020-01-01T00:00:00+00:00", journal.ORIGIN_LOCK, slot=1),
            entry("unlock", "2026-09-21T10:00:00+00:00", journal.ORIGIN_LOCK, slot=2),
        ]
        journal.trim(entries, max_age_days=365, now=1789000000.0)
        self.assertEqual([item["slot"] for item in entries], [2])

    def test_no_trim_reports_false(self) -> None:
        entries = [entry("unlock", T0, journal.ORIGIN_LOCK, slot=1)]
        self.assertFalse(journal.trim(entries, max_entries=10, max_age_days=0))


class QueryTest(unittest.TestCase):
    ENTRIES = [
        entry("unlock", T0, journal.ORIGIN_LOCK, slot=11, name="Carro"),
        entry("lock", T1, journal.ORIGIN_LOCK, slot=11),
        entry("pin_set", T2, journal.ORIGIN_HA, slot=20, name="Gäst"),
    ]

    def test_filters(self) -> None:
        self.assertEqual(len(journal.query(self.ENTRIES, person="carro")), 1)
        self.assertEqual(len(journal.query(self.ENTRIES, slot=11)), 2)
        self.assertEqual(len(journal.query(self.ENTRIES, action="unlock")), 1)
        self.assertEqual(len(journal.query(self.ENTRIES, since=T2)), 1)

    def test_limit_keeps_the_newest(self) -> None:
        result = journal.query(self.ENTRIES, limit=1)
        self.assertEqual(result[0]["action"], "pin_set")

    def test_summary(self) -> None:
        summary = journal.summarize(self.ENTRIES, now=None)
        self.assertEqual(summary, {"total": 3, "last_24h": 0})


if __name__ == "__main__":
    unittest.main()
