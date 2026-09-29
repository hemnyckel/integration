"""Unit tests for the person-avatar view rules — no Home Assistant needed."""

from __future__ import annotations

import importlib.util
import os
import pathlib
import unittest

AVATAR_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "custom_components"
    / "hemnyckel"
    / "mirror"
    / "avatar.py"
)
spec = importlib.util.spec_from_file_location("hemnyckel_avatar", AVATAR_PATH)
assert spec is not None and spec.loader is not None
avatar = importlib.util.module_from_spec(spec)
spec.loader.exec_module(avatar)

PERSON_ID = "0123456789abcdef0123456789abcdef"


class ValidPersonIdTest(unittest.TestCase):
    def test_a_relay_person_id_is_accepted(self) -> None:
        self.assertTrue(avatar.valid_person_id(PERSON_ID))

    def test_anything_but_32_lower_hex_is_refused(self) -> None:
        for value in (
            "",
            "Elise",
            PERSON_ID.upper(),
            PERSON_ID[:-1],
            PERSON_ID + "0",
            "0123456789abcdef0123456789abcdeg",
            "../../etc/passwd",
            "0123456789abcdef/../0123456789abcdef",
            None,
            1234,
        ):
            self.assertFalse(avatar.valid_person_id(value), value)


class AvatarPathTest(unittest.TestCase):
    def test_a_valid_id_names_the_mirrored_file(self) -> None:
        self.assertEqual(
            avatar.avatar_path(PERSON_ID),
            os.path.join(avatar.SHARE_AVATAR_DIR, f"{PERSON_ID}.jpg"),
        )

    def test_something_that_is_not_an_id_has_no_path(self) -> None:
        # No traversal, no absolute path, no other person's file: the shape of
        # the id is the whole guard.
        for value in ("../../etc/passwd", "/etc/passwd", "Elise", "", None):
            self.assertIsNone(avatar.avatar_path(value), value)

    def test_the_directory_is_the_one_the_relay_writes(self) -> None:
        self.assertEqual(avatar.SHARE_AVATAR_DIR, "/share/hemnyckel/avatars")


class EntityTagTest(unittest.TestCase):
    def test_the_tag_changes_with_the_file(self) -> None:
        first = os.stat_result((0, 0, 0, 1, 0, 0, 100, 0, 0, 0))
        second = os.stat_result((0, 0, 0, 1, 0, 0, 101, 0, 0, 0))
        self.assertNotEqual(avatar.entity_tag(first), avatar.entity_tag(second))

    def test_the_tag_is_stable_for_an_unchanged_file(self) -> None:
        stat = os.stat_result((0, 0, 0, 1, 0, 0, 100, 0, 0, 0))
        self.assertEqual(avatar.entity_tag(stat), avatar.entity_tag(stat))


if __name__ == "__main__":
    unittest.main()
