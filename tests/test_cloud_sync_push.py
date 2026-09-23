"""Unit tests for cloud.guests.update_fields — the push mapping, no HA."""

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
spec = importlib.util.spec_from_file_location("nimly_cloud_guests_update", GUESTS_PATH)
assert spec is not None and spec.loader is not None
guests = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guests)

NOW = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)


class UpdateFieldsTest(unittest.TestCase):
    def test_name_is_trimmed_and_sent(self) -> None:
        self.assertEqual(
            guests.update_fields({"validFrom": "x"}, {"name": "  Anna "}),
            {"name": "Anna"},
        )

    def test_blank_name_is_not_a_field(self) -> None:
        self.assertEqual(guests.update_fields({}, {"name": "   "}), {})

    def test_date_only_end_means_the_end_of_that_day(self) -> None:
        fields = guests.update_fields(
            {"validFrom": "2026-09-23T12:00:00+00:00"}, {"until": "2026-10-01"}
        )
        self.assertEqual(fields["validTo"], "2026-10-01T23:59:59.000Z")
        self.assertEqual(fields["validFrom"], "2026-09-23T12:00:00+00:00")

    def test_explicit_timestamp_is_kept(self) -> None:
        fields = guests.update_fields(
            {"validFrom": "f"}, {"until": "2026-10-01T15:30:00+00:00"}
        )
        self.assertEqual(fields["validTo"], "2026-10-01T15:30:00+00:00")

    def test_forever_becomes_the_century_window(self) -> None:
        fields = guests.update_fields({}, {"until": ""}, now=NOW)
        self.assertEqual(fields["validFrom"], "2026-09-23T12:00:00+00:00")
        self.assertEqual(fields["validTo"], "2126-09-23T12:00:00+00:00")

    def test_other_changes_do_not_touch_cloud_fields(self) -> None:
        self.assertEqual(guests.update_fields({}, {"paused": True, "code": "1234"}), {})

    def test_no_changes_no_fields(self) -> None:
        self.assertEqual(guests.update_fields({}, {}), {})


if __name__ == "__main__":
    unittest.main()
